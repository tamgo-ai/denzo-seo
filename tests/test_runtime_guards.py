"""Regressions for server saturation, repeated work and interrupted workers."""

import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from denzo.execution import (
    reserve_job,
    execute_job,
    finish_job,
    Cancellation,
    AgentCancelled,
)
from denzo.runtime_limits import CapacityExceeded, RuntimeLimitExceeded


def mark_running(db, job):
    db.execute(
        "UPDATE agent_jobs SET status='running',started_at=CURRENT_TIMESTAMP,heartbeat_at=CURRENT_TIMESTAMP WHERE id=?",
        (job,),
    )
    db.commit()


def test_execution_slots_are_shared_between_clients(platform_db, ctx, monkeypatch):
    from denzo.agents import registry

    entered, release = threading.Event(), threading.Event()

    class Slow:
        def check_prerequisites(self):
            return True, ""

        def run(self):
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(registry, "get_agent", lambda *args: Slow())
    first = reserve_job("alice", "Keyword Strategist", "rq")
    second = reserve_job("bob", "Keyword Strategist", "rq")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute_job, first, ctx)
        try:
            assert entered.wait(3)
            with pytest.raises(CapacityExceeded):
                execute_job(second, ctx)
            assert (
                platform_db.execute(
                    "SELECT COUNT(*) FROM agent_jobs WHERE status='running'"
                ).fetchone()[0]
                == 1
            )
        finally:
            release.set()
        assert future.result()["status"] == "done"


def test_thread_mode_cannot_start_unbounded_tenants(platform_db):
    assert reserve_job("alice", "Keyword Strategist", "thread")
    with pytest.raises(CapacityExceeded):
        reserve_job("bob", "Keyword Strategist", "thread")
    # The director has its own slot so it can orchestrate the worker.
    assert reserve_job("alice", "Pipeline Director", "thread")


def test_queue_admission_is_atomic_and_bounded(platform_db, monkeypatch):
    monkeypatch.setenv("DENZO_MAX_QUEUED_JOBS", "1")

    def attempt(tid):
        try:
            return reserve_job(tid, "Keyword Strategist", "rq")
        except CapacityExceeded:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(attempt, ["alice", "bob"]))
    assert sum(bool(job) for job in jobs) == 1
    assert platform_db.execute("SELECT COUNT(*) FROM agent_jobs").fetchone()[0] == 1


def test_queue_without_consumers_expires_and_late_completion_is_ignored(platform_db):
    from denzo.execution import recover_expired_jobs

    old = reserve_job("alice", "Keyword Strategist", "rq")
    platform_db.execute(
        "UPDATE agent_jobs SET created_at='2000-01-01' WHERE id=?", (old,)
    )
    platform_db.commit()
    recover_expired_jobs()
    row = platform_db.execute("SELECT * FROM agent_jobs WHERE id=?", (old,)).fetchone()
    assert row["status"] == "error" and row["retryable"] == 0
    replacement = reserve_job("alice", "Keyword Strategist", "rq")
    finish_job(old, "done")
    assert (
        platform_db.execute(
            "SELECT status FROM agent_jobs WHERE id=?", (old,)
        ).fetchone()[0]
        == "error"
    )
    assert (
        platform_db.execute(
            "SELECT status FROM agents WHERE tenant_id='alice' AND name='Keyword Strategist'"
        ).fetchone()[0]
        == "starting"
    )
    assert replacement != old


def test_stopping_pipeline_blocks_children_enqueued_during_the_stop(
    platform_db, monkeypatch
):
    from denzo.agents.runner import AgentRunner

    parent = reserve_job("alice", "Pipeline Director", "thread")
    mark_running(platform_db, parent)
    child = reserve_job("alice", "Keyword Strategist", "thread", parent_job_id=parent)
    result = AgentRunner.stop_all("alice")
    assert result["count"] == 2
    assert Cancellation(parent).is_set()
    assert (
        platform_db.execute(
            "SELECT status FROM agent_jobs WHERE id=?", (child,)
        ).fetchone()[0]
        == "cancelled"
    )
    with pytest.raises(AgentCancelled):
        reserve_job("alice", "Keyword Clusterer", "thread", parent_job_id=parent)


def test_elapsed_job_budget_fences_writes_and_disables_automatic_retry(
    platform_db, ctx, monkeypatch
):
    from denzo.agents import registry, base_agent

    class TooLong:
        def check_prerequisites(self):
            return True, ""

        def run(self):
            self._stop.deadline = 0
            base_agent.db_write(
                "INSERT INTO settings(tenant_id,key,value) VALUES('alice','late-write','1')"
            )

    monkeypatch.setattr(registry, "get_agent", lambda *args: TooLong())
    job = reserve_job("alice", "Keyword Strategist", "rq")
    with pytest.raises(RuntimeLimitExceeded, match="time limit"):
        execute_job(job, ctx)
    assert not platform_db.execute(
        "SELECT 1 FROM settings WHERE key='late-write'"
    ).fetchone()
    row = platform_db.execute("SELECT * FROM agent_jobs WHERE id=?", (job,)).fetchone()
    assert row["status"] == "error" and row["retryable"] == 0
    assert (
        platform_db.execute(
            "SELECT status FROM pipeline_runs WHERE job_id=?", (job,)
        ).fetchone()[0]
        == "error"
    )


def test_api_call_budget_caps_the_whole_job(platform_db, ctx, monkeypatch):
    from denzo.agents import registry, base_agent

    monkeypatch.setenv("DENZO_MAX_API_CALLS_PER_JOB", "2")
    calls = []
    fake = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kw: (
                calls.append(kw)
                or SimpleNamespace(content=[SimpleNamespace(text="ok")])
            )
        )
    )
    monkeypatch.setattr(base_agent, "_get_anthropic_client", lambda: fake)
    monkeypatch.setattr(base_agent, "interruptible_wait", lambda *args: None)

    class Repeating(base_agent.TenantAwareBaseAgent):
        def check_prerequisites(self):
            return True, ""

        def run(self):
            for _ in range(100):
                self.call_claude("test")

    monkeypatch.setattr(
        registry, "get_agent", lambda *args: Repeating("Keyword Strategist", ctx)
    )
    job = reserve_job("alice", "Keyword Strategist", "rq")
    with pytest.raises(RuntimeLimitExceeded, match="call budget"):
        execute_job(job, ctx)
    row = platform_db.execute("SELECT * FROM agent_jobs WHERE id=?", (job,)).fetchone()
    assert len(calls) == 2 and row["api_calls"] == 2 and row["retryable"] == 0


def test_bad_provider_credentials_do_not_retry(ctx, monkeypatch):
    import anthropic
    from denzo.agents import base_agent

    attempts = []

    def reject(**kwargs):
        attempts.append(1)
        raise anthropic.AuthenticationError(
            "Invalid credentials",
            response=SimpleNamespace(
                status_code=401, request=SimpleNamespace(), headers={}
            ),
            body=None,
        )

    monkeypatch.setattr(
        base_agent,
        "_get_anthropic_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=reject)),
    )
    monkeypatch.setattr(base_agent, "interruptible_wait", lambda *args: None)
    agent = base_agent.TenantAwareBaseAgent("Keyword Strategist", ctx)
    with pytest.raises(RuntimeLimitExceeded, match="HTTP 401"):
        agent.call_claude("test")
    assert attempts == [1]


def test_stop_interrupts_a_saturated_api_semaphore(ctx, monkeypatch):
    from denzo.agents import base_agent

    agent = base_agent.TenantAwareBaseAgent("Keyword Strategist", ctx)
    agent._stop.set()
    monkeypatch.setattr(base_agent, "_api_semaphore", threading.Semaphore(0))
    with pytest.raises(AgentCancelled):
        with agent._api_slot():
            pytest.fail("Cancelled job entered API slot")


def test_queue_failure_attempts_and_cooldowns_stop_the_director(
    ctx, platform_db, monkeypatch
):
    import rq
    from denzo.agents import runner, registry
    from denzo.agents.director import PipelineDirector

    calls = []

    class BrokenQueue:
        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, *args, **kwargs):
            calls.append(1)
            raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(rq, "Queue", BrokenQueue)
    monkeypatch.setattr(runner, "redis_connection", lambda: object())
    monkeypatch.setattr(runner, "_EXECUTOR_MODE", "rq")
    monkeypatch.setattr(
        registry,
        "get_agent",
        lambda *args: SimpleNamespace(check_prerequisites=lambda: (True, "")),
    )
    director = PipelineDirector(ctx)
    for count in range(1, 4):
        director._start_agent("Site Inventory")
        row = platform_db.execute(
            "SELECT * FROM agents WHERE tenant_id='alice' AND name='Site Inventory'"
        ).fetchone()
        assert (
            row["run_count"] == count
            and row["retry_after"]
            and "Queue unavailable" in row["current_task"]
        )
        assert director._evaluate(director._assess_state()) == []
        platform_db.execute(
            "UPDATE agents SET retry_after=NULL WHERE tenant_id='alice'"
        )
        platform_db.commit()
    assert len(calls) == 3 and director._check_deadlock(
        director._assess_state()["agents"]
    )


def test_orchestration_exceptions_do_not_repeat_for_two_hours(
    ctx, platform_db, monkeypatch
):
    from denzo.agents.director import PipelineDirector
    from denzo import runtime_limits

    director = PipelineDirector(ctx)
    calls = []

    def broken():
        calls.append(1)
        raise ValueError("Malformed stored state")

    monkeypatch.setattr(director, "_generate_strategy", lambda: None)
    monkeypatch.setattr(director, "_assess_state", broken)
    monkeypatch.setattr(runtime_limits, "interruptible_wait", lambda *args: None)
    director.run()
    assert len(calls) == 3
    assert (
        platform_db.execute(
            "SELECT status FROM agents WHERE tenant_id='alice' AND name='Pipeline Director'"
        ).fetchone()[0]
        == "error"
    )


@pytest.mark.parametrize("assessment", [(None, ""), (40, "")])
def test_optimizer_does_not_repeat_failed_or_unchanged_pages(
    ctx, page, platform_db, monkeypatch, assessment
):
    from denzo.agents.layer3_production.content_optimizer import ContentOptimizer

    platform_db.execute("UPDATE pages SET quality_score=NULL WHERE id=?", (page["id"],))
    platform_db.commit()
    agent = ContentOptimizer(ctx)
    calls = []
    monkeypatch.setattr(
        agent, "_score_and_fix", lambda p, **kw: calls.append(p["id"]) or assessment
    )
    agent.run()
    assert calls == [page["id"]]


def test_content_batch_skips_unmanaged_inventory(ctx, page, platform_db, monkeypatch):
    from denzo.agents.layer3_production.content_optimizer import ContentOptimizer

    platform_db.execute(
        "UPDATE pages SET managed=0,status='published',quality_score=NULL WHERE id=?",
        (page["id"],),
    )
    platform_db.commit()
    agent = ContentOptimizer(ctx)
    monkeypatch.setattr(
        agent,
        "_score_and_fix",
        lambda *a, **kw: pytest.fail("Rewrote unmanaged inventory"),
    )
    agent.run()
    assert (
        platform_db.execute(
            "SELECT content FROM pages WHERE id=?", (page["id"],)
        ).fetchone()[0]
        == page["content"]
    )


def test_scheduled_dispatch_stops_after_repeated_failures(platform_db, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from denzo import scheduler

    start = datetime(2026, 9, 15, 8, tzinfo=timezone.utc)
    scheduler.configure("alice", True, now=start)
    calls = []
    monkeypatch.setattr(scheduler, "_run_due", lambda tid: calls.append(tid) or "error")
    for minute in (0, 5, 10, 15):
        scheduler.tick(start.replace(hour=9) + timedelta(minutes=minute), verify=False)
    row = platform_db.execute(
        "SELECT * FROM schedules WHERE tenant_id='alice'"
    ).fetchone()
    assert len(calls) == 3 and not row["enabled"] and row["next_run_at"] is None


def test_rq_killed_worker_is_visible_and_cannot_retry_automatically(platform_db):
    from denzo.worker import BoundedWorker

    job = reserve_job("alice", "Keyword Strategist", "rq")
    mark_running(platform_db, job)
    worker = object.__new__(BoundedWorker)
    worker.log = logging.getLogger("test-killed-worker")
    worker._work_horse_killed_handler = None
    worker.handle_work_horse_killed(SimpleNamespace(id=job), 123, 9, None)
    row = platform_db.execute("SELECT * FROM agent_jobs WHERE id=?", (job,)).fetchone()
    assert (
        row["status"] == "error"
        and row["retryable"] == 0
        and "terminated" in row["error"]
    )


def test_busy_api_returns_429_without_claiming_it_started(client, monkeypatch):
    from denzo.agents.runner import AgentRunner

    monkeypatch.setattr(
        AgentRunner,
        "start",
        lambda *args: {"status": "busy", "message": "Queue full", "retry_after": 30},
    )
    for path in (
        "/api/alice/pipeline/run",
        "/api/alice/agents/start/Keyword Strategist",
    ):
        response = client.post(path)
        assert response.status_code == 429 and response.headers["Retry-After"] == "30"
        assert response.get_json()["status"] == "busy"


def test_lighthouse_timeout_kills_its_spawned_children(tmp_path):
    import subprocess, psutil
    from denzo.processes import run_bounded

    marker = tmp_path / "child.pid"
    script = "import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded([sys.executable, "-c", script, str(marker)], timeout=0.7)
    pid = int(marker.read_text())
    try:
        process = psutil.Process(pid)
        assert process.status() == psutil.STATUS_ZOMBIE or not process.is_running()
    except psutil.NoSuchProcess:
        pass


def test_browser_slot_is_shared_between_processes(platform_db):
    import subprocess
    from denzo.db import DB_PATH
    from denzo.processes import browser_slot

    script = "import denzo.db,sys; denzo.db.DB_PATH=sys.argv[1]; from denzo.processes import browser_slot;\nwith browser_slot(timeout=0): pass"
    with browser_slot(timeout=0):
        result = subprocess.run(
            [sys.executable, "-c", script, DB_PATH],
            capture_output=True,
            text=True,
            timeout=5,
        )
    assert result.returncode != 0 and "Another browser audit" in result.stderr
    with browser_slot(timeout=0):
        pass


def test_gsc_repeated_page_does_not_loop(ctx, monkeypatch):
    from denzo.agents.utils import gsc_client

    rows = [
        {"keys": ["2026-09-01", f"query {i}", "https://example.com"]}
        for i in range(5000)
    ]
    calls = []
    monkeypatch.setattr(
        gsc_client, "get_bound_site", lambda tid: "sc-domain:example.com"
    )
    monkeypatch.setattr(
        gsc_client,
        "query_search_analytics",
        lambda *a, **kw: calls.append(kw["start_row"]) or rows,
    )
    result = gsc_client.sync_last_n_days("alice")
    assert calls == [0, 5000] and result["rows"] == 5000 and result["truncated"]


def test_inventory_resumes_its_saved_urls_in_small_batches(ctx, monkeypatch):
    from denzo.agents.layer1_research.site_inventory import SiteInventoryAgent

    monkeypatch.setenv("DENZO_INVENTORY_PAGE_BATCH", "2")
    urls = ["https://example.com", *[f"https://example.com/p{i}" for i in range(4)]]
    read = []
    agent = SiteInventoryAgent(ctx)
    monkeypatch.setattr(agent, "_crawl_sitemap", lambda url: list(urls))
    monkeypatch.setattr(
        agent, "_fetch_page_data", lambda url: read.append(url) or {"source_url": url}
    )
    monkeypatch.setattr(agent, "_insert_existing_page", lambda data: None)
    agent.run()
    assert read == urls[:2] and agent.load_output("site_inventory")["next_offset"] == 2
    monkeypatch.setattr(
        agent,
        "_crawl_sitemap",
        lambda url: pytest.fail("Rediscovered instead of resuming"),
    )
    agent.run()
    assert read == urls[:4] and agent.load_output("site_inventory")["next_offset"] == 4


def test_public_reader_honors_timeout_and_handles_closed_http_sockets(monkeypatch):
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from denzo.auditor import safe_fetch

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "5")
            self.end_headers()
            self.wfile.write(b"hello")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    monkeypatch.setattr(safe_fetch, "validate_url", lambda url: (url, "127.0.0.1"))
    limits = []
    connect = safe_fetch.socket.create_connection
    monkeypatch.setattr(
        safe_fetch.socket,
        "create_connection",
        lambda *a, **kw: limits.append(kw["timeout"]) or connect(*a, **kw),
    )
    try:
        result = safe_fetch.fetch_html(
            f"http://example.com:{server.server_port}", timeout=3
        )
        assert result["ok"] and result["html"] == "hello" and "body" not in result
        assert 0 < limits[0] <= 3
    finally:
        server.server_close()
        thread.join(timeout=2)


def test_apify_does_not_materialize_the_whole_dataset(ctx, monkeypatch):
    import apify_client
    from denzo.agents.utils.apify_service import ApifyService, ACTOR_IDS

    count = []

    def records():
        for i in range(1000000):
            count.append(i)
            yield {"id": i}

    fake = SimpleNamespace(
        actor=lambda name: SimpleNamespace(
            call=lambda **kw: {"defaultDatasetId": "items"}
        ),
        dataset=lambda name: SimpleNamespace(iterate_items=records),
    )
    monkeypatch.setattr(apify_client, "ApifyClient", lambda *a, **k: fake)
    service = object.__new__(ApifyService)
    service._key = "test"
    service._log = lambda *args: None
    assert len(service.run_actor(next(iter(ACTOR_IDS)), {}, max_items=3)) == 3
    assert count == [0, 1, 2]


@pytest.mark.parametrize("outcome", ["success", "timeout", "killed"])
def test_real_rq_worker_lifecycle(platform_db, monkeypatch, outcome):
    import os, signal, time, uuid
    import redis, rq
    from denzo.agents import registry
    from denzo.worker import BoundedWorker, run_agent_job, job_failed, job_stopped

    url = os.getenv("DENZO_TEST_REDIS_URL")
    if not url:
        pytest.skip("Dedicated test Redis is required; enabled in GitHub Actions")
    connection = redis.Redis.from_url(url, socket_timeout=3, socket_connect_timeout=3)
    connection.ping()

    class Work:
        def check_prerequisites(self):
            return True, ""

        def run(self):
            if outcome == "timeout":
                time.sleep(10)
            elif outcome == "killed":
                os.kill(os.getpid(), signal.SIGKILL)

    monkeypatch.setattr(registry, "get_agent", lambda *args: Work())
    queue = rq.Queue("denzo-test-" + uuid.uuid4().hex, connection=connection)
    job_id = reserve_job("alice", "Keyword Strategist", "rq")
    job = queue.enqueue(
        run_agent_job,
        job_id,
        job_id=job_id,
        job_timeout=1,
        on_failure=job_failed,
        on_stopped=job_stopped,
    )
    try:
        worker = BoundedWorker([queue], connection=connection)
        worker.work(burst=True, logging_level="ERROR")
        row = platform_db.execute(
            "SELECT * FROM agent_jobs WHERE id=?", (job_id,)
        ).fetchone()
        assert row["status"] == ("done" if outcome == "success" else "error")
        if outcome != "success":
            assert row["retryable"] == 0 and row["error"]
        assert job.get_status().value == (
            "finished" if outcome == "success" else "failed"
        )
    finally:
        job.delete()
        queue.delete(delete_jobs=True)
        connection.close()


@pytest.mark.parametrize("operation", ["director", "scheduler", "linker"])
def test_large_site_html_is_not_loaded_for_every_page(
    ctx, platform_db, monkeypatch, operation
):
    import tracemalloc
    from denzo.agents.director import PipelineDirector
    from denzo.agents.layer3_production.internal_linker import InternalLinker
    from denzo.scheduler import _run_due

    body = "<p>" + ("Existing page content. " * 10000) + "</p>"
    platform_db.executemany(
        "INSERT INTO pages(tenant_id,title,slug,type,status,content,quality_score,managed) VALUES ('alice','Large page',?,'blog','ready',?,80,1)",
        [(f"large-{i}", body) for i in range(30)],
    )
    platform_db.commit()
    linker = InternalLinker(ctx)
    monkeypatch.setattr(linker, "_plan_batch", lambda *args: [])
    run = {
        "director": lambda: PipelineDirector(ctx)._assess_state(),
        "scheduler": lambda: _run_due("alice"),
        "linker": linker.run,
    }[operation]
    tracemalloc.start()
    try:
        run()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    # The fixture has >6 MiB of HTML; state queries need none, linking needs one batch.
    assert peak < (4 * 1024 * 1024 if operation == "linker" else 2 * 1024 * 1024)
