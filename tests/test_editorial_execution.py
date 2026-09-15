import pytest
from denzo.editorial import transition, revision_hash, publishable
from denzo.db import get_db


def read_page(db, pid):
    return dict(db.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone())


def test_changed_revision_revokes_approval_and_archives_old_content(platform_db, page):
    transition("alice", page["id"], "approve", 101)
    assert publishable(read_page(platform_db, page["id"]))
    platform_db.execute(
        "UPDATE pages SET content='<h2>New draft</h2>' WHERE id=?", (page["id"],)
    )
    platform_db.commit()
    updated = read_page(platform_db, page["id"])
    assert updated["approval_hash"] is None and updated["quality_score"] is None
    assert not publishable(updated)
    old = platform_db.execute(
        "SELECT * FROM content_versions WHERE page_id=?", (page["id"],)
    ).fetchone()
    assert old["content"] == page["content"] and old["quality_score"] == 85
    from denzo.db import init_db

    init_db()
    init_db()
    assert read_page(platform_db, page["id"])["content"] == "<h2>New draft</h2>"
    assert (
        platform_db.execute(
            "SELECT COUNT(*) FROM content_versions WHERE page_id=?", (page["id"],)
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "New title"),
        ("meta_title", "New SEO title"),
        ("meta_description", "New description"),
        ("schema_markup", "{}"),
        ("slug", "new-path"),
        ("type", "service"),
    ],
)
def test_every_published_field_is_bound_to_approval(platform_db, page, field, value):
    transition("alice", page["id"], "approve", 101)
    platform_db.execute(f"UPDATE pages SET {field}=? WHERE id=?", (value, page["id"]))
    platform_db.commit()
    assert not publishable(read_page(platform_db, page["id"]))


def test_page_ids_cannot_cross_tenants(platform_db, page):
    with pytest.raises(LookupError):
        transition("bob", page["id"], "approve", 102)
    assert read_page(platform_db, page["id"])["approval_hash"] is None


def test_concurrent_writers_reserved_once_and_other_clients_independent(platform_db):
    from concurrent.futures import ThreadPoolExecutor
    from denzo.execution import reserve_job

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(
            pool.map(lambda _: reserve_job("alice", "Programmatic SEO", "rq"), range(2))
        )
    assert sum(bool(j) for j in jobs) == 1
    assert reserve_job("alice", "Internal Linker", "rq") is None
    assert reserve_job("bob", "Programmatic SEO", "rq")


def test_editorial_review_waits_for_writers(platform_db, page):
    from denzo.execution import reserve_job

    reserve_job("alice", "Content Optimizer", "rq")
    with pytest.raises(ValueError, match="processed"):
        transition("alice", page["id"], "approve", 101)


def test_cooperative_cancel_fences_writes(platform_db, ctx):
    from denzo.execution import reserve_job, Cancellation, AgentCancelled
    from denzo.agents import base_agent

    job = reserve_job("alice", "Programmatic SEO", "thread")
    token = Cancellation(job)
    token.set()
    base_agent._sqlite_local.job_token = token
    try:
        with pytest.raises(AgentCancelled):
            base_agent.db_write(
                "INSERT INTO settings(tenant_id,key,value) VALUES ('alice','must_not_exist','1')"
            )
    finally:
        base_agent._sqlite_local.job_token = None
    assert (
        platform_db.execute(
            "SELECT 1 FROM settings WHERE key='must_not_exist'"
        ).fetchone()
        is None
    )


def test_expired_lease_fences_old_worker(platform_db):
    from denzo.execution import reserve_job, Cancellation

    old = reserve_job("alice", "Programmatic SEO", "rq")
    platform_db.execute(
        "UPDATE agent_jobs SET status='running',heartbeat_at='2000-01-01' WHERE id=?",
        (old,),
    )
    platform_db.commit()
    new = reserve_job("alice", "Programmatic SEO", "rq")
    assert new and new != old
    assert Cancellation(old).is_set()


def test_worker_propagates_failure_for_rq(platform_db, ctx, monkeypatch):
    from denzo.execution import reserve_job, execute_job
    from denzo.agents import registry

    class Broken:
        def check_prerequisites(self):
            return True, ""

        def run(self):
            raise ValueError("Broken provider")

    monkeypatch.setattr(registry, "get_agent", lambda *args: Broken())
    job = reserve_job("alice", "Keyword Strategist", "rq")
    with pytest.raises(ValueError, match="Broken provider"):
        execute_job(job, ctx)
    row = platform_db.execute("SELECT * FROM agent_jobs WHERE id=?", (job,)).fetchone()
    assert row["status"] == "error" and "Broken provider" in row["error"]
    assert (
        platform_db.execute(
            "SELECT status FROM pipeline_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        == "error"
    )


def test_rq_deduplicates_and_director_has_separate_queue(platform_db, ctx, monkeypatch):
    from types import SimpleNamespace
    import rq, redis
    from denzo.agents import runner, registry

    calls = []

    class Queue:
        def __init__(self, name, **kwargs):
            self.name = name

        def enqueue(self, *args, **kwargs):
            calls.append((self.name, args, kwargs))

    monkeypatch.setattr(rq, "Queue", Queue)
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **k: object())
    monkeypatch.setattr(runner, "_EXECUTOR_MODE", "rq")
    monkeypatch.setattr(
        registry,
        "get_agent",
        lambda *args: SimpleNamespace(check_prerequisites=lambda: (True, "")),
    )
    assert (
        runner.AgentRunner.start("alice", "Pipeline Director", ctx)["status"]
        == "started"
    )
    assert (
        runner.AgentRunner.start("alice", "Pipeline Director", ctx)["status"]
        == "already_running"
    )
    assert (
        runner.AgentRunner.start("alice", "Keyword Strategist", ctx)["status"]
        == "started"
    )
    assert [x[0] for x in calls] == ["denzo-director", "denzo-seo"]


def test_daily_scheduler_is_persistent_and_idempotent(platform_db, monkeypatch):
    from datetime import datetime, timezone
    from denzo import scheduler

    start = datetime(2026, 9, 15, 7, tzinfo=timezone.utc)
    assert scheduler.next_daily(start, "America/Los_Angeles", 9).hour == 16
    scheduler.configure("alice", True, "UTC", 9, start)
    calls = []
    monkeypatch.setattr(
        scheduler, "_run_due", lambda tid: calls.append(tid) or "started"
    )
    due = start.replace(hour=10)
    scheduler.tick(due, verify=False)
    scheduler.tick(due, verify=False)
    assert calls == ["alice"]
    scheduler.configure("alice", False, now=due)
    scheduler.tick(due.replace(day=16), verify=False)
    assert calls == ["alice"]


def test_scheduler_does_not_publish_unreviewed_content(platform_db, page, monkeypatch):
    from denzo.scheduler import _run_due
    from denzo.agents.runner import AgentRunner

    def no_start(*args, **kwargs):
        raise AssertionError("Must wait for review")

    monkeypatch.setattr(AgentRunner, "start", no_start)
    assert _run_due("alice") == "awaiting_review"


def test_empty_ready_page_is_not_publishable():
    assert not publishable(
        {"status": "ready", "content": None, "quality_score": 85, "managed": 1}
    )
