"""Persistent daily schedules. Run with ``python -m denzo.scheduler``."""

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from denzo.db import get_db


def next_daily(now, zone="UTC", hour=9):
    if not 0 <= int(hour) <= 23:
        raise ValueError("Hour must be between 0 and 23")
    local = now.astimezone(ZoneInfo(zone))
    candidate = local.replace(hour=int(hour), minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def configure(tenant_id, enabled, zone="UTC", hour=9, now=None):
    now = now or datetime.now(timezone.utc)
    upcoming = next_daily(now, zone, hour).isoformat() if enabled else None
    db = get_db()
    try:
        db.execute(
            """INSERT INTO schedules(tenant_id,enabled,timezone,hour,next_run_at)
            VALUES(?,?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET enabled=excluded.enabled,
            timezone=excluded.timezone,hour=excluded.hour,next_run_at=excluded.next_run_at""",
            (tenant_id, int(enabled), zone, hour, upcoming),
        )
        db.execute(
            "UPDATE schedules SET failure_streak=0 WHERE tenant_id=?", (tenant_id,)
        )
        db.commit()
    finally:
        db.close()
    return upcoming


def _run_due(tenant_id):
    from denzo.agents.runner import AgentRunner
    from denzo.editorial import publishable

    if AgentRunner.any_running(tenant_id):
        return "busy"
    db = get_db()
    try:
        blocked = db.execute(
            """SELECT 1 FROM agent_jobs j JOIN agents a ON a.tenant_id=j.tenant_id AND a.name=j.agent_name
            WHERE j.tenant_id=? AND j.status='error' AND j.retryable=0 AND a.status='error'
            AND j.id=(SELECT id FROM agent_jobs WHERE tenant_id=j.tenant_id AND agent_name=j.agent_name ORDER BY created_at DESC,rowid DESC LIMIT 1) LIMIT 1""",
            (tenant_id,),
        ).fetchone()
        if blocked:
            return "blocked"
        pages = [
            dict(r)
            for r in db.execute("SELECT * FROM pages WHERE tenant_id=?", (tenant_id,))
        ]
        client = db.execute(
            "SELECT publisher_type FROM clients WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        if any(publishable(p) for p in pages):
            agent = (
                "WordPress Publisher"
                if client["publisher_type"] == "wordpress"
                else "GitHub Publisher"
            )
        elif any(p["status"] == "publishing" for p in pages):
            return "awaiting_deployment"
        elif any(
            p["status"] == "ready"
            and p.get("content")
            and p.get("quality_score") is None
            and "[CO_MAX_RETRIES]" not in (p.get("notes") or "")
            for p in pages
        ):
            agent = "Content Optimizer"
        elif any(p["status"] == "ready" and p.get("content") for p in pages):
            return "awaiting_review"
        else:
            agent = "Pipeline Director"
            db.execute(
                """INSERT INTO settings(tenant_id,key,value) VALUES (?,'generation_batch_size','1')
                ON CONFLICT(tenant_id,key) DO UPDATE SET value='1' """,
                (tenant_id,),
            )
            db.execute(
                """UPDATE agents SET status='idle',run_count=0 WHERE tenant_id=?
              AND name IN ('Pipeline Director','Programmatic SEO','Content Optimizer','Visual Content Optimizer',
                'GEO Optimizer','Internal Linker','GitHub Publisher','WordPress Publisher')
              AND status NOT IN ('working','starting')""",
                (tenant_id,),
            )
            db.commit()
    finally:
        db.close()
    result = AgentRunner.start(tenant_id, agent)
    return (
        "busy" if result["status"] in ("already_running", "busy") else result["status"]
    )


def tick(now=None, verify=True):
    now = now or datetime.now(timezone.utc)
    from denzo.execution import recover_expired_jobs

    recover_expired_jobs()
    if verify:
        from denzo.publication import reconcile_publications

        reconcile_publications()
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        due = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM schedules WHERE enabled=1 AND next_run_at<=?",
                (now.isoformat(),),
            )
        ]
        # Short claim lease lets a crashed scheduler resume without claiming the same schedule twice.
        for row in due:
            db.execute(
                "UPDATE schedules SET next_run_at=? WHERE tenant_id=?",
                ((now + timedelta(minutes=5)).isoformat(), row["tenant_id"]),
            )
        db.commit()
    finally:
        db.close()
    results = []
    for row in due:
        try:
            result = _run_due(row["tenant_id"])
        except Exception:
            logging.getLogger(__name__).exception(
                "Schedule failed: %s", row["tenant_id"]
            )
            result = "error"
        failures = row["failure_streak"] + 1 if result == "error" else 0
        paused = result == "blocked" or failures >= 3
        upcoming = (
            now + timedelta(minutes=5)
            if result in ("busy", "error")
            else next_daily(now, row["timezone"], row["hour"])
        )
        db = get_db()
        try:
            db.execute(
                "UPDATE schedules SET next_run_at=?,last_run_at=?,last_result=?,failure_streak=?,enabled=? WHERE tenant_id=? AND enabled=1",
                (
                    None if paused else upcoming.isoformat(),
                    now.isoformat(),
                    "paused_after_failures" if paused else result,
                    failures,
                    0 if paused else 1,
                    row["tenant_id"],
                ),
            )
            db.commit()
        finally:
            db.close()
        results.append({"tenant_id": row["tenant_id"], "result": result})
    return results


def main():
    from dotenv import load_dotenv

    load_dotenv()
    from denzo.db import init_db

    init_db()
    while True:
        try:
            tick()
        except Exception:
            logging.getLogger(__name__).exception("Scheduler tick failed")
        time.sleep(30)


if __name__ == "__main__":
    main()
