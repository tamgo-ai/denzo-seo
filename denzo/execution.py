"""Durable execution leases shared by web processes and RQ workers."""

import json
import threading
import time
import uuid
from denzo.db import get_db
from denzo.runtime_limits import (
    CapacityExceeded,
    RuntimeLimitExceeded,
    job_timeout,
    queue_ttl,
    running_limit,
    setting,
)

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
            """SELECT * FROM agent_jobs WHERE (? IS NULL OR tenant_id=?) AND (
              (status='running' AND heartbeat_at < datetime('now','-2 minutes')) OR
              (status='queued' AND created_at < datetime('now',?)))""",
            (tenant_id, tenant_id, f"-{queue_ttl()} seconds"),
        ).fetchall()
        for row in rows:
            reason = (
                "Queue wait expired; check workers before restarting"
                if row["status"] == "queued"
                else "Worker lease expired; check resources before restarting"
            )
            db.execute(
                "UPDATE agent_jobs SET status='error',retryable=0,error=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (reason, row["id"]),
            )
            db.execute(
                "UPDATE pipeline_runs SET status='error',completed_at=CURRENT_TIMESTAMP WHERE job_id=? AND status='running'",
                (row["id"],),
            )
            db.execute(
                "UPDATE agents SET status='error',current_task='Worker stopped; retry after checking publication status' WHERE tenant_id=? AND name=?",
                (row["tenant_id"], row["agent_name"]),
            )
        db.commit()
    finally:
        db.close()

    for row in rows:
        if row["agent_name"] == "Pipeline Director":
            cancel_children(row["id"])


def reserve_job(tenant_id, agent_name, executor, parent_job_id=None):
    recover_expired_jobs()
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        if parent_job_id:
            parent = db.execute(
                "SELECT * FROM agent_jobs WHERE id=? AND tenant_id=? AND status='running' AND cancel_requested=0 AND (deadline_at IS NULL OR deadline_at>datetime('now'))",
                (parent_job_id, tenant_id),
            ).fetchone()
            if not parent:
                raise AgentCancelled()
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
        queued = db.execute(
            "SELECT COUNT(*) FROM agent_jobs WHERE status='queued'"
        ).fetchone()[0]
        if queued >= setting("DENZO_MAX_QUEUED_JOBS", 24, 1, 200):
            raise CapacityExceeded(
                "Server queue is full; try again after current work finishes"
            )
        if executor == "thread":
            count = db.execute(
                "SELECT COUNT(*) FROM agent_jobs WHERE status IN ('queued','running') AND (agent_name='Pipeline Director')=?",
                (int(agent_name == "Pipeline Director"),),
            ).fetchone()[0]
            if count >= running_limit(agent_name):
                raise CapacityExceeded("Server concurrency limit reached")
        job_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO agent_jobs(id,tenant_id,agent_name,executor,status,parent_job_id) VALUES (?,?,?,?,'queued',?)",
            (job_id, tenant_id, agent_name, executor, parent_job_id),
        )
        db.execute(
            "UPDATE agents SET status='starting',run_count=run_count+1,retry_after=NULL,current_task='Queued',updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND name=?",
            (tenant_id, agent_name),
        )
        db.commit()
        return job_id
    finally:
        db.close()


def finish_job(job_id, status, error=None, retryable=True):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM agent_jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["status"] not in ("queued", "running"):
            return
        db.execute(
            "UPDATE agent_jobs SET status=?,error=?,retryable=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, error, int(retryable and row["retryable"]), job_id),
        )
        agent_status = "idle" if status == "cancelled" else status
        db.execute(
            """UPDATE agents SET status=?,current_task=CASE WHEN ? IS NOT NULL THEN ? ELSE current_task END,
            retry_after=CASE WHEN ?='error' THEN datetime('now', '+' || MIN(300,30*MAX(1,run_count)) || ' seconds') ELSE NULL END,
            updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND name=?
            AND NOT EXISTS (SELECT 1 FROM agent_jobs j WHERE j.tenant_id=? AND j.agent_name=?
              AND j.status IN ('queued','running') AND j.id!=?)""",
            (
                agent_status,
                error,
                error,
                status,
                row["tenant_id"],
                row["agent_name"],
                row["tenant_id"],
                row["agent_name"],
                job_id,
            ),
        )
        db.execute(
            "UPDATE pipeline_runs SET status=?,completed_at=CURRENT_TIMESTAMP WHERE job_id=? AND status='running'",
            (status, job_id),
        )
        db.commit()
    finally:
        db.close()


def cancel_children(job_id):
    from denzo.agents.runner import AgentRunner

    db = get_db()
    try:
        row = db.execute(
            "SELECT tenant_id FROM agent_jobs WHERE id=?", (job_id,)
        ).fetchone()
    finally:
        db.close()
    if row:
        AgentRunner._request_stop(row["tenant_id"], parent_job_id=job_id)


class Cancellation:
    def __init__(self, job_id, timeout=None):
        self.job_id, self.last_check, self.cancelled = job_id, 0, False
        self.deadline = time.monotonic() + timeout if timeout else None
        self.reason = None

    def is_set(self):
        if (
            self.deadline is not None
            and time.monotonic() >= self.deadline
            and not self.cancelled
        ):
            self.block("Agent execution time limit reached")
        if not self.cancelled and time.monotonic() - self.last_check > 0.25:
            db = get_db()
            try:
                row = db.execute(
                    "SELECT status,cancel_requested,error,retryable FROM agent_jobs WHERE id=?",
                    (self.job_id,),
                ).fetchone()
                self.cancelled = (
                    not row
                    or row["cancel_requested"]
                    or row["status"] not in ("queued", "running")
                )
                if self.cancelled and row and not row["retryable"]:
                    self.reason = (
                        row["error"] or "Execution stopped after a runtime failure"
                    )
                self.last_check = time.monotonic()
            finally:
                db.close()
        return bool(self.cancelled)

    def block(self, reason):
        self.reason, self.cancelled = reason, True
        db = get_db()
        try:
            db.execute(
                "UPDATE agent_jobs SET retryable=0,cancel_requested=1,error=? WHERE id=? AND status='running'",
                (reason, self.job_id),
            )
            db.commit()
        finally:
            db.close()

    def consume_api_call(self):
        if self.is_set():
            if self.reason:
                raise RuntimeLimitExceeded(self.reason)
            raise AgentCancelled()
        db = get_db()
        try:
            updated = db.execute(
                "UPDATE agent_jobs SET api_calls=api_calls+1 WHERE id=? AND status='running' AND cancel_requested=0 AND api_calls<?",
                (self.job_id, setting("DENZO_MAX_API_CALLS_PER_JOB", 60, 1, 500)),
            ).rowcount
            row = (
                db.execute(
                    "SELECT status,cancel_requested FROM agent_jobs WHERE id=?",
                    (self.job_id,),
                ).fetchone()
                if not updated
                else None
            )
            db.commit()
        finally:
            db.close()
        if not updated:
            if not row or row["cancel_requested"] or row["status"] != "running":
                self.cancelled = True
                raise AgentCancelled()
            self.block("Agent API call budget reached")
            raise RuntimeLimitExceeded(self.reason)

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
        count = db.execute(
            "SELECT COUNT(*) FROM agent_jobs WHERE status='running' AND (agent_name='Pipeline Director')=?",
            (int(row["agent_name"] == "Pipeline Director"),),
        ).fetchone()[0]
        if count >= running_limit(row["agent_name"]):
            db.rollback()
            finish_job(job_id, "error", "Server concurrency limit reached")
            raise CapacityExceeded("Server concurrency limit reached")
        db.execute(
            "UPDATE agent_jobs SET status='running',started_at=CURRENT_TIMESTAMP,heartbeat_at=CURRENT_TIMESTAMP,deadline_at=datetime('now',?) WHERE id=?",
            (f"+{job_timeout(row['agent_name'])} seconds", job_id),
        )
        db.execute(
            "UPDATE agents SET status='working',last_run_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND name=?",
            (row["tenant_id"], row["agent_name"]),
        )
        run_id = db.execute(
            "INSERT INTO pipeline_runs(tenant_id,triggered_by,agents_run,status,job_id) VALUES (?,'auto',?,'running',?)",
            (row["tenant_id"], json.dumps([row["agent_name"]]), job_id),
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
        token = Cancellation(job_id, job_timeout(row["agent_name"]))
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
            if token.reason:
                raise RuntimeLimitExceeded(token.reason)
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
        from rq.timeouts import JobTimeoutException

        if isinstance(exc, (RuntimeLimitExceeded, JobTimeoutException)):
            token.block(error)
        raise
    finally:
        done.set()
        thread.join(timeout=2)
        base_agent._sqlite_local.job_token = None
        base_agent.close_thread_connection()
        finish_job(job_id, status, error)
        if row["agent_name"] == "Pipeline Director" and status in (
            "error",
            "cancelled",
        ):
            cancel_children(job_id)
        db = get_db()
        try:
            db.execute(
                "UPDATE pipeline_runs SET status=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, run_id),
            )
            db.commit()
        finally:
            db.close()
