"""
worker.py — RQ worker entry point for agent execution.

Usage:
    rq worker denzo-seo --url redis://localhost:6379/0

When DENZO_EXECUTOR=rq is set in .env, AgentRunner.start() enqueues
an RQ job instead of spawning a daemon thread. The RQ worker picks it up.

In thread mode (default), agent execution runs inside the web process
as daemon threads. This is fine for dev/single-tenant.

RQ mode decouples execution from the web process — production-ready.
"""

import json
import os
import sqlite3
import traceback
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_agent_job(tenant_id: str, agent_name: str):
    """
    RQ job function. Loads context, instantiates agent, runs it.
    This runs inside the RQ worker process — NOT the web process.
    """
    from denzo.context.builder import build_client_context
    from denzo.agents.registry import AGENT_REGISTRY, get_agent
    from denzo.agents.base_agent import db_write, db_execute, close_thread_connection, DB_PATH

    run_id = None
    try:
        # Mark working in DB
        db_write(
            """UPDATE agents
               SET status='working', current_task='Starting... (RQ worker)', updated_at=CURRENT_TIMESTAMP,
                   last_run_at=CURRENT_TIMESTAMP, run_count=run_count+1
               WHERE tenant_id=? AND name=?""",
            (tenant_id, agent_name),
        )

        # Record pipeline run
        db = sqlite3.connect(DB_PATH, timeout=10)
        db.row_factory = sqlite3.Row
        cur = db.execute(
            "INSERT INTO pipeline_runs (tenant_id, triggered_by, agents_run, status) VALUES (?,?,?,?)",
            (tenant_id, "auto", json.dumps([agent_name]), "running")
        )
        run_id = cur.lastrowid
        db.commit()
        db.close()

        # Build context and run agent
        ctx = build_client_context(tenant_id)
        agent = get_agent(agent_name, ctx)

        # Check prerequisites
        ready, reason = agent.check_prerequisites()
        if not ready:
            db_write(
                "UPDATE agents SET status='idle', current_task=? WHERE tenant_id=? AND name=?",
                (f"Waiting: {reason}", tenant_id, agent_name)
            )
            _finish_run(run_id, "skipped")
            return {"status": "skipped", "reason": reason}

        agent.running = True
        agent.run()

        # Check final status
        rows = db_execute(
            "SELECT status FROM agents WHERE tenant_id=? AND name=?",
            (tenant_id, agent_name)
        )
        final_status = rows[0]["status"] if rows else "working"
        if final_status == "working":
            db_write(
                "UPDATE agents SET status='done', current_task='Completed (RQ)' WHERE tenant_id=? AND name=?",
                (tenant_id, agent_name)
            )
            _finish_run(run_id, "completed")
        else:
            _finish_run(run_id, final_status)

        return {"status": "completed"}

    except Exception as exc:
        tb = traceback.format_exc()
        try:
            db_write(
                "UPDATE agents SET status='error', current_task=? WHERE tenant_id=? AND name=?",
                (str(exc)[:200], tenant_id, agent_name)
            )
            db_write(
                "INSERT INTO activity (tenant_id,type,message,agent,level,created_at) VALUES (?,?,?,?,?,datetime('now'))",
                (tenant_id, "agent", f"{agent_name} crashed: {str(exc)[:200]}", agent_name, "error"),
            )
        except Exception:
            pass
        print(f"[RQ worker][{tenant_id}][{agent_name}] CRASH:\n{tb}", flush=True)
        _finish_run(run_id, "error")
        return {"status": "error", "message": str(exc)[:200]}

    finally:
        close_thread_connection()


def _finish_run(run_id, status: str):
    if not run_id:
        return
    try:
        from denzo.agents.base_agent import db_write
        db_write(
            "UPDATE pipeline_runs SET status=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, run_id)
        )
    except Exception:
        pass
