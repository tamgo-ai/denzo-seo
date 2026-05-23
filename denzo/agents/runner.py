"""
AgentRunner — unified agent execution for the entire platform.
Every agent (including Pipeline Director) is launched and tracked here.

There is exactly ONE way to run an agent. The Director, API routes,
and any future entry points all go through AgentRunner.

Features:
- Thread tracking: every running agent is in _threads[tenant_id][agent_name]
- Stop events: every agent gets a threading.Event wired to agent._stop
- Duplicate prevention: refuses to start an agent that's already running
- Prerequisites: calls agent.check_prerequisites() before run()
- Cleanup: closes thread-local SQLite connection on exit
"""
import json
import sqlite3
import threading
import traceback

from denzo.agents.base_agent import close_thread_connection, DB_PATH


class AgentRunner:
    """Singleton registry of all running agent threads."""

    _threads: dict[str, dict[str, threading.Thread]] = {}
    _events: dict[str, dict[str, threading.Event]] = {}
    _lock = threading.Lock()

    @classmethod
    def start(cls, tenant_id: str, agent_name: str, ctx=None) -> dict:
        """
        Launch an agent in a daemon thread. Returns {"status": "started"|"already_running"|"prereq_failed"}.
        If ctx is None, it's built from the DB.
        """
        from denzo.agents.registry import AGENT_REGISTRY, get_agent
        from denzo.agents.base_agent import db_execute, db_write

        if agent_name not in AGENT_REGISTRY:
            return {"status": "error", "message": f"Unknown agent: {agent_name}"}

        with cls._lock:
            tenant_threads = cls._threads.setdefault(tenant_id, {})
            if agent_name in tenant_threads and tenant_threads[agent_name].is_alive():
                return {"status": "already_running", "message": f"{agent_name} is already running"}

            # Check DB status as double-check
            rows = db_execute(
                "SELECT status FROM agents WHERE tenant_id=? AND name=?",
                (tenant_id, agent_name)
            )
            if rows and rows[0]["status"] == "working":
                # Stale? Check if thread is actually alive
                if agent_name not in tenant_threads:
                    # DB says working but no thread — reset and proceed
                    db_write(
                        "UPDATE agents SET status='idle', current_task='Reset by AgentRunner (stale)' WHERE tenant_id=? AND name=?",
                        (tenant_id, agent_name)
                    )

            stop_event = threading.Event()
            cls._events.setdefault(tenant_id, {})[agent_name] = stop_event

            if ctx is None:
                from denzo.context.builder import build_client_context
                ctx = build_client_context(tenant_id)

            agent = get_agent(agent_name, ctx)

            # Check prerequisites before launching
            ready, reason = agent.check_prerequisites()
            if not ready:
                db_write(
                    "UPDATE agents SET status='idle', current_task=? WHERE tenant_id=? AND name=?",
                    (f"Waiting: {reason}", tenant_id, agent_name)
                )
                return {"status": "prereq_failed", "message": reason}

            def _thread_target():
                run_id = None
                try:
                    # Mark working
                    db_write(
                        """UPDATE agents
                           SET status='working', current_task='Starting...', updated_at=CURRENT_TIMESTAMP,
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

                    # Wire stop event into the agent
                    agent._stop = stop_event
                    agent.running = True

                    agent.run()

                    # Determine final status
                    if stop_event.is_set():
                        db_write(
                            "UPDATE agents SET status='idle', current_task='Stopped' WHERE tenant_id=? AND name=?",
                            (tenant_id, agent_name)
                        )
                        cls._finish_run(run_id, "stopped")
                    else:
                        rows = db_execute(
                            "SELECT status FROM agents WHERE tenant_id=? AND name=?",
                            (tenant_id, agent_name)
                        )
                        final_status = rows[0]["status"] if rows else "working"
                        if final_status == "working":
                            db_write(
                                "UPDATE agents SET status='done', current_task='Completed' WHERE tenant_id=? AND name=?",
                                (tenant_id, agent_name)
                            )
                            cls._finish_run(run_id, "completed")
                        else:
                            cls._finish_run(run_id, final_status)

                except Exception as exc:
                    tb = traceback.format_exc()
                    db_write(
                        "UPDATE agents SET status='error', current_task=? WHERE tenant_id=? AND name=?",
                        (str(exc)[:200], tenant_id, agent_name)
                    )
                    db_write(
                        "INSERT INTO activity (tenant_id,type,message,agent,level,created_at) VALUES (?,?,?,?,?,datetime('now'))",
                        (tenant_id, "agent", f"{agent_name} crashed: {str(exc)[:200]}", agent_name, "error"),
                    )
                    print(f"[{tenant_id}][{agent_name}] CRASH:\n{tb}", flush=True)
                    cls._finish_run(run_id, "error")
                finally:
                    # Cleanup: remove from tracking, close thread-local DB connection
                    with cls._lock:
                        cls._threads.get(tenant_id, {}).pop(agent_name, None)
                        cls._events.get(tenant_id, {}).pop(agent_name, None)
                    close_thread_connection()

            t = threading.Thread(
                target=_thread_target,
                daemon=True,
                name=f"agent:{tenant_id}:{agent_name}",
            )
            tenant_threads[agent_name] = t
            t.start()

        return {"status": "started", "agent": agent_name}

    @classmethod
    def stop(cls, tenant_id: str, agent_name: str) -> dict:
        """Signal an agent to stop. Returns {"status": "stop_requested"|"not_running"}."""
        with cls._lock:
            events = cls._events.get(tenant_id, {})
            event = events.get(agent_name)

        if event:
            event.set()
            return {"status": "stop_requested", "agent": agent_name}
        else:
            from denzo.agents.base_agent import db_write
            db_write(
                "UPDATE agents SET status='idle', current_task='Stopped by user' WHERE tenant_id=? AND name=?",
                (tenant_id, agent_name)
            )
            return {"status": "not_running", "agent": agent_name}

    @classmethod
    def stop_all(cls, tenant_id: str) -> dict:
        """Signal ALL agents for a tenant to stop. Returns count of stopped."""
        with cls._lock:
            events = cls._events.pop(tenant_id, {})
            cls._threads.pop(tenant_id, {})

        for event in events.values():
            event.set()

        from denzo.agents.base_agent import db_write
        db_write(
            "UPDATE agents SET status='idle', current_task='Reset by user', next_task='' WHERE tenant_id=?",
            (tenant_id,)
        )

        return {"status": "stopped", "count": len(events)}

    @classmethod
    def is_running(cls, tenant_id: str, agent_name: str) -> bool:
        """Check if an agent thread is currently alive."""
        with cls._lock:
            threads = cls._threads.get(tenant_id, {})
            t = threads.get(agent_name)
            return t is not None and t.is_alive()

    @classmethod
    def running_agents(cls, tenant_id: str) -> list[str]:
        """List agent names currently running for a tenant."""
        with cls._lock:
            threads = cls._threads.get(tenant_id, {})
            return [name for name, t in threads.items() if t.is_alive()]

    @classmethod
    def any_running(cls, tenant_id: str) -> bool:
        """True if ANY agent is running for this tenant."""
        return len(cls.running_agents(tenant_id)) > 0

    # ── Internal helpers ──────────────────────────────────────────────────────

    @classmethod
    def _finish_run(cls, run_id, status: str):
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
