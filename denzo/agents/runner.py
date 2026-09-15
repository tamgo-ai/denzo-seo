"""Agent execution using durable reservations and cooperative cancellation."""

import os
import threading
import logging
from denzo.db import get_db

_EXECUTOR_MODE = os.getenv("DENZO_EXECUTOR", "rq")


def redis_connection():
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    return Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        socket_connect_timeout=3,
        socket_timeout=5,
        retry_on_timeout=False,
        retry=Retry(NoBackoff(), 0),
    )


class AgentRunner:
    @classmethod
    def start(cls, tenant_id, agent_name, ctx=None):
        job_id = None
        from denzo.agents.registry import AGENT_REGISTRY, get_agent
        from denzo.context.builder import build_client_context
        from denzo.execution import reserve_job, execute_job, finish_job
        from denzo.runtime_limits import (
            CapacityExceeded,
            check_cancelled,
            job_timeout,
            queue_ttl,
        )
        from denzo.agents.base_agent import _sqlite_local

        check_cancelled()
        if agent_name not in AGENT_REGISTRY:
            return {"status": "error", "message": "Unknown agent"}
        try:
            from denzo.billing.enforce import agent_entitled

            allowed, reason = agent_entitled(tenant_id, agent_name)
            if not allowed:
                return {"status": "prereq_failed", "message": reason}
            ctx = ctx or build_client_context(tenant_id)
            agent = get_agent(agent_name, ctx)
            ready, reason = agent.check_prerequisites()
            if not ready:
                return {"status": "prereq_failed", "message": reason}
            if _EXECUTOR_MODE not in ("rq", "thread"):
                return {
                    "status": "error",
                    "message": "DENZO_EXECUTOR must be rq or thread",
                }
            parent = getattr(_sqlite_local, "job_token", None)
            job_id = reserve_job(
                tenant_id, agent_name, _EXECUTOR_MODE, parent.job_id if parent else None
            )
            if not job_id:
                return {
                    "status": "already_running",
                    "message": "An execution or content change is already in progress",
                }
            if _EXECUTOR_MODE == "rq":
                from rq import Queue
                from denzo.worker import run_agent_job, job_failed, job_stopped

                queue = (
                    "denzo-director"
                    if agent_name == "Pipeline Director"
                    else "denzo-seo"
                )
                conn = redis_connection()
                try:
                    Queue(queue, connection=conn).enqueue(
                        run_agent_job,
                        job_id,
                        job_id=job_id,
                        job_timeout=job_timeout(agent_name) + 30,
                        ttl=queue_ttl(),
                        on_failure=job_failed,
                        on_stopped=job_stopped,
                        result_ttl=86400,
                        failure_ttl=604800,
                    )
                except Exception:
                    finish_job(
                        job_id, "error", "Queue unavailable; no work was started"
                    )
                    raise
            else:

                def target():
                    try:
                        execute_job(job_id, ctx)
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Agent failed: %s", agent_name
                        )

                threading.Thread(
                    target=target, daemon=True, name=f"agent:{tenant_id}:{agent_name}"
                ).start()
            return {
                "status": "started",
                "agent": agent_name,
                "job_id": job_id,
                "executor": _EXECUTOR_MODE,
            }
        except CapacityExceeded as exc:
            return {"status": "busy", "message": str(exc), "retry_after": 30}
        except Exception as exc:
            return {"status": "error", "message": str(exc)[:300], "job_id": job_id}

    @classmethod
    def stop(cls, tenant_id, agent_name):
        if agent_name == "Pipeline Director":
            return cls.stop_all(tenant_id)
        return cls._request_stop(tenant_id, agent_name)

    @classmethod
    def _request_stop(cls, tenant_id, agent_name=None, parent_job_id=None):
        from denzo.execution import finish_job

        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            where = "tenant_id=? AND (? IS NULL OR agent_name=?) AND (? IS NULL OR parent_job_id=?) AND status IN ('queued','running')"
            params = (tenant_id, agent_name, agent_name, parent_job_id, parent_job_id)
            rows = db.execute(
                "SELECT * FROM agent_jobs WHERE " + where, params
            ).fetchall()
            db.execute(
                "UPDATE agent_jobs SET cancel_requested=1 WHERE " + where, params
            )
            db.commit()
        finally:
            db.close()
        for row in rows:
            if row["status"] == "queued":
                finish_job(row["id"], "cancelled", "Cancelled before execution")
                if row["executor"] == "rq":
                    try:
                        from rq.job import Job

                        Job.fetch(row["id"], connection=redis_connection()).cancel()
                    except Exception:
                        logging.getLogger(__name__).warning(
                            "Queued job cancellation recorded in database: %s",
                            row["id"],
                        )
            elif row["executor"] == "rq":
                try:
                    from rq.command import send_stop_job_command

                    send_stop_job_command(redis_connection(), row["id"])
                except Exception:
                    logging.getLogger(__name__).warning(
                        "Cooperative cancellation recorded; RQ stop command unavailable: %s",
                        row["id"],
                    )
        return {
            "status": "stop_requested" if rows else "not_running",
            "agent": agent_name,
            "count": len(rows),
        }

    @classmethod
    def stop_all(cls, tenant_id):
        result = cls._request_stop(tenant_id)
        db = get_db()
        try:
            db.execute(
                "UPDATE agents SET status='idle',current_task='Reset by user',run_count=0 WHERE tenant_id=? AND NOT EXISTS (SELECT 1 FROM agent_jobs j WHERE j.tenant_id=agents.tenant_id AND j.agent_name=agents.name AND j.status IN ('queued','running'))",
                (tenant_id,),
            )
            db.commit()
        finally:
            db.close()
        return {"status": "stop_requested", "count": result["count"]}

    @classmethod
    def running_agents(cls, tenant_id):
        db = get_db()
        try:
            return [
                r[0]
                for r in db.execute(
                    "SELECT agent_name FROM agent_jobs WHERE tenant_id=? AND status IN ('queued','running')",
                    (tenant_id,),
                )
            ]
        finally:
            db.close()

    @classmethod
    def is_running(cls, tenant_id, agent_name):
        return agent_name in cls.running_agents(tenant_id)

    @classmethod
    def any_running(cls, tenant_id):
        return bool(cls.running_agents(tenant_id))
