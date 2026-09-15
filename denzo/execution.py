"""Durable execution leases shared by web processes and RQ workers."""

import json
import threading
import time
import uuid
from denzo.db import get_db

CONTENT_WRITERS = (
    "Site Inventory",
    "E-E-A-T Architect",
    "Schema Engineer",
    "Vertical Matrix Generator",
    "Programmatic SEO",
    "Content Optimizer",
    "Visual Content Optimizer",
    "GEO Optimizer",
    "Internal Linker",
    "Content Freshness",
    "GitHub Publisher",
    "WordPress Publisher",
    "GEO Gap Closer",
    "Content Duplicate Checker",
    "Video Engine",
)


class AgentCancelled(Exception):
    pass


def recover_expired_jobs(tenant_id=None):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """SELECT * FROM agent_jobs WHERE status='running'
           AND heartbeat_at < datetime('now','-2 minutes') AND (? IS NULL OR tenant_id=?)""",
            (tenant_id, tenant_id),
        ).fetchall()
        for row in rows:
            db.execute(
                "UPDATE agent_jobs SET status='error',error='Worker lease expired',completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (row["id"],),
            )
            db.execute(
                "UPDATE agents SET status='error',current_task='Worker stopped; retry after checking publication status' WHERE tenant_id=? AND name=?",
                (row["tenant_id"], row["agent_name"]),
            )
        db.commit()
    finally:
        db.close()


def reserve_job(tenant_id, agent_name, executor):
    recover_expired_jobs(tenant_id)
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        active = db.execute(
            "SELECT * FROM agent_jobs WHERE tenant_id=? AND status IN ('queued','running')",
            (tenant_id,),
        ).fetchall()
        for row in active:
            if row["agent_name"] == agent_name or (
                agent_name in CONTENT_WRITERS and row["agent_name"] in CONTENT_WRITERS
            ):
                db.rollback()
                return None
        job_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO agent_jobs(id,tenant_id,agent_name,executor,status) VALUES (?,?,?,?,'queued')",
            (job_id, tenant_id, agent_name, executor),
        )
        db.execute(
            "UPDATE agents SET status='starting',current_task='Queued',updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND name=?",
            (tenant_id, agent_name),
        )
        db.commit()
        return job_id
    finally:
        db.close()


def finish_job(job_id, status, error=None):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return
        db.execute(
            "UPDATE agent_jobs SET status=?,error=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, error, job_id),
        )
        agent_status = "idle" if status == "cancelled" else status
        db.execute(
            """UPDATE agents SET status=?,current_task=CASE WHEN ? IS NOT NULL THEN ? ELSE current_task END,
            updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND name=?
            AND NOT EXISTS (SELECT 1 FROM agent_jobs j WHERE j.tenant_id=? AND j.agent_name=?
              AND j.status IN ('queued','running') AND j.id!=?)""",
            (
                agent_status,
                error,
                error,
                row["tenant_id"],
                row["agent_name"],
                row["tenant_id"],
                row["agent_name"],
                job_id,
            ),
        )
        db.commit()
    finally:
        db.close()


class Cancellation:
    def __init__(self, job_id):
        self.job_id, self.last_check, self.cancelled = job_id, 0, False

    def is_set(self):
        if not self.cancelled and time.monotonic() - self.last_check > 0.25:
            db = get_db()
            try:
                row = db.execute(
                    "SELECT status,cancel_requested FROM agent_jobs WHERE id=?",
                    (self.job_id,),
                ).fetchone()
                self.cancelled = (
                    not row
                    or row["cancel_requested"]
                    or row["status"] not in ("queued", "running")
                )
                self.last_check = time.monotonic()
            finally:
                db.close()
        return bool(self.cancelled)

    def set(self):
        db = get_db()
        try:
            db.execute(
                "UPDATE agent_jobs SET cancel_requested=1 WHERE id=?", (self.job_id,)
            )
            db.commit()
        finally:
            db.close()
        self.cancelled = True


def execute_job(job_id, ctx=None):
    from denzo.agents.registry import get_agent
    from denzo.context.builder import build_client_context
    from denzo.agents import base_agent

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["status"] != "queued":
            return {"status": "skipped"}
        if row["cancel_requested"]:
            db.rollback()
            finish_job(job_id, "cancelled", "Cancelled before execution")
            return {"status": "cancelled"}
        db.execute(
            "UPDATE agent_jobs SET status='running',started_at=CURRENT_TIMESTAMP,heartbeat_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        )
        db.execute(
            "UPDATE agents SET status='working',run_count=run_count+1,last_run_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND name=?",
            (row["tenant_id"], row["agent_name"]),
        )
        run_id = db.execute(
            "INSERT INTO pipeline_runs(tenant_id,triggered_by,agents_run,status) VALUES (?,'auto',?,'running')",
            (row["tenant_id"], json.dumps([row["agent_name"]])),
        ).lastrowid
        db.commit()
    finally:
        db.close()
    done = threading.Event()

    def heartbeat():
        while not done.wait(10):
            db = get_db()
            try:
                db.execute(
                    "UPDATE agent_jobs SET heartbeat_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
                    (job_id,),
                )
                db.commit()
            finally:
                db.close()

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    status, error = "error", None
    try:
        from denzo.billing.enforce import agent_entitled

        allowed, reason = agent_entitled(row["tenant_id"], row["agent_name"])
        if not allowed:
            raise ValueError(reason)
        token = Cancellation(job_id)
        base_agent._sqlite_local.job_token = token
        agent = get_agent(
            row["agent_name"], ctx or build_client_context(row["tenant_id"])
        )
        agent._stop = token
        ready, reason = agent.check_prerequisites()
        if not ready:
            status, error = "idle", f"Waiting: {reason}"
            return {"status": status, "reason": reason}
        agent.running = True
        agent.run()
        if token.is_set():
            raise AgentCancelled()
        db = get_db()
        try:
            current = db.execute(
                "SELECT status FROM agents WHERE tenant_id=? AND name=?",
                (row["tenant_id"], row["agent_name"]),
            ).fetchone()
        finally:
            db.close()
        status = (
            current["status"]
            if current and current["status"] not in ("working", "starting")
            else "done"
        )
        if status == "idle":
            status = "error"
        if status == "error":
            raise RuntimeError("Agent reported an error; see activity log")
        return {"status": status}
    except AgentCancelled:
        status, error = "cancelled", "Stopped by user"
        return {"status": "cancelled"}
    except Exception as exc:
        status, error = "error", str(exc)[:300]
        raise
    finally:
        done.set()
        thread.join(timeout=2)
        base_agent._sqlite_local.job_token = None
        base_agent.close_thread_connection()
        finish_job(job_id, status, error)
        db = get_db()
        try:
            db.execute(
                "UPDATE pipeline_runs SET status=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, run_id),
            )
            db.commit()
        finally:
            db.close()
