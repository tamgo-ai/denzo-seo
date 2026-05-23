"""
Systematic agent test runner.
Tests every agent for a given tenant, measuring time, output, and stability.
"""
import sys, time, json, os, threading, psutil
sys.path.insert(0, '/root/denzo-seo')

from denzo.context.builder import build_client_context
from denzo.agents.registry import AGENT_REGISTRY, get_agent
from denzo.agents.runner import AgentRunner
from denzo.agents.base_agent import db_execute, db_write


def get_mem():
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024  # MB


def test_agent(tenant_id, agent_name, ctx):
    """Test a single agent. Returns (success, duration, output_preview, error)."""
    print(f"\n{'='*60}")
    print(f"TESTING: {agent_name}")
    print(f"{'='*60}")

    mem_before = get_mem()
    t0 = time.time()

    # Reset agent to idle
    db_write(
        "UPDATE agents SET status='idle', current_task='', run_count=0 WHERE tenant_id=? AND name=?",
        (tenant_id, agent_name)
    )

    try:
        agent = get_agent(agent_name, ctx)
        ready, reason = agent.check_prerequisites()
        if not ready:
            print(f"  ⏭ SKIP: {reason}")
            db_write(
                "UPDATE agents SET status='idle', current_task=? WHERE tenant_id=? AND name=?",
                (f"Skip: {reason}", tenant_id, agent_name)
            )
            return (True, time.time() - t0, f"SKIPPED: {reason}", None)

        # Run agent
        agent.run()
        duration = time.time() - t0
        mem_after = get_mem()

        # Check final status
        rows = db_execute(
            "SELECT status, current_task FROM agents WHERE tenant_id=? AND name=?",
            (tenant_id, agent_name)
        )
        status = rows[0]["status"] if rows else "unknown"
        task = rows[0]["current_task"] if rows else ""

        # Get last log messages
        logs = db_execute(
            "SELECT message, level FROM activity WHERE tenant_id=? AND agent=? ORDER BY id DESC LIMIT 3",
            (tenant_id, agent_name)
        )
        preview = " | ".join(r["message"][:80] for r in (logs or []))

        mem_delta = mem_after - mem_before
        print(f"  Status: {status}")
        print(f"  Duration: {duration:.1f}s")
        print(f"  Memory: {mem_before:.0f}MB → {mem_after:.0f}MB (Δ{mem_delta:+.0f}MB)")
        print(f"  Output: {preview[:200]}")

        if status == "error":
            print(f"  ❌ FAILED: {task}")
            return (False, duration, preview, task)

        print(f"  ✅ PASS")
        return (True, duration, preview, None)

    except Exception as e:
        duration = time.time() - t0
        print(f"  ❌ CRASH: {e}")
        import traceback
        traceback.print_exc()
        return (False, duration, str(e)[:100], str(e))


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/test_agents.py <tenant_id> [agent_name]")
        print("Available tenants:")
        rows = db_execute("SELECT tenant_id, name FROM clients")
        for r in rows:
            print(f"  {r['tenant_id']} — {r['name']}")
        sys.exit(1)

    tenant_id = sys.argv[1]
    specific_agent = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Tenant: {tenant_id}")
    print(f"Start memory: {get_mem():.0f}MB")
    ctx = build_client_context(tenant_id)
    print(f"Client: {ctx.client_name} | Industry: {ctx.industry_vertical}")
    print(f"Keywords: {db_execute('SELECT COUNT(*) as n FROM keywords WHERE tenant_id=?', (tenant_id,))[0]['n']}")
    print(f"Pages: {db_execute('SELECT COUNT(*) as n FROM pages WHERE tenant_id=?', (tenant_id,))[0]['n']}")

    # Agents to test — by layer
    agents_to_test = specific_agent.split(",") if specific_agent else [
        # Layer 1 — Intelligence
        "Keyword Strategist", "Competitor Intel", "Technical Auditor",
        "Site Style Analyzer", "Data Intelligence", "GBP Optimizer",
        "Keyword Clusterer",
        # Layer 2 — Strategy
        "E-E-A-T Architect", "Schema Engineer", "Vertical Matrix Generator",
        # Layer 3 — Generation
        "Programmatic SEO",
        # Layer 4 — Optimization
        "Content Optimizer", "Visual Content Optimizer", "GEO Optimizer",
        "Internal Linker", "Content Freshness",
        # Layer 5 — Publishing
        "GitHub Publisher", "WordPress Publisher",
        # Layer 6 — Analytics
        "Rank Tracker", "GEO Query Generator", "GEO Monitor",
        "SERP Intelligence", "Reviews Intelligence", "ROI Attribution",
    ]

    results = []
    for agent_name in agents_to_test:
        if agent_name not in AGENT_REGISTRY:
            print(f"\n⚠ Unknown agent: {agent_name}")
            continue

        success, duration, preview, error = test_agent(tenant_id, agent_name, ctx)
        results.append({
            "agent": agent_name,
            "success": success,
            "duration": duration,
            "preview": preview[:100],
            "error": error,
        })

        # Small delay between agents to let system breathe
        time.sleep(1)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n\n{'='*60}")
    print(f"RESULTS SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for r in results if r["success"])
    failed = sum(1 for r in results if not r["success"])
    total_time = sum(r["duration"] for r in results)
    final_mem = get_mem()

    print(f"Passed: {passed}/{len(results)}")
    print(f"Failed: {failed}/{len(results)}")
    print(f"Total time: {total_time:.0f}s ({total_time/60:.1f}min)")
    print(f"Final memory: {final_mem:.0f}MB")

    for r in results:
        icon = "✅" if r["success"] else "❌"
        print(f"  {icon} {r['agent']:30s} {r['duration']:5.0f}s  {r['preview'][:80]}")

    if failed > 0:
        print(f"\n❌ FAILURES:")
        for r in results:
            if not r["success"]:
                print(f"  {r['agent']}: {r['error']}")

    # Memory leak check
    print(f"\nMemory: start={get_mem():.0f}MB end={final_mem:.0f}MB")
    if final_mem > get_mem() * 1.5:
        print("⚠ Possible memory leak detected")
    else:
        print("✅ Memory stable")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
