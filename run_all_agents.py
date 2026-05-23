"""
run_all_agents.py — Full pipeline runner for all tenants.
Runs agents sequentially to avoid API rate limit conflicts.

SAFETY: Checks for running agents before starting. Will NOT execute
if any agents are already working (e.g., Director is running).
"""
import sys
import traceback
from datetime import datetime

sys.path.insert(0, '/root/denzo-seo')

from denzo.context.builder import build_client_context
from denzo.agents.base_agent import db_execute, db_write
from denzo.agents.runner import AgentRunner


def log(msg):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def check_running_agents():
    """Abort if any agents are currently working across all tenants."""
    rows = db_execute(
        "SELECT tenant_id, name FROM agents WHERE status='working'"
    )
    if rows:
        log("WARNING: Agents are currently running:")
        for r in rows:
            log(f"  - {r['tenant_id']} / {r['name']}")
        log("")
        log("This means the Pipeline Director (or another process) is active.")
        log("Running this script simultaneously will cause double-execution and resource contention.")
        log("")
        response = input("Continue anyway? This is DANGEROUS. [y/N]: ")
        if response.lower() != 'y':
            log("Aborted.")
            sys.exit(0)
        log("Proceeding despite running agents — YOU HAVE BEEN WARNED.")
    else:
        log("No running agents detected — safe to proceed.")


def run_agent(agent_class, ctx, label=None):
    name = label or agent_class.__name__
    log(f"▶ {ctx.tenant_id} / {name}")
    try:
        agent = agent_class(ctx)
        agent.run()
        log(f"✓ {ctx.tenant_id} / {name} done")
        return True
    except Exception as e:
        log(f"✗ {ctx.tenant_id} / {name} FAILED: {e}")
        traceback.print_exc()
        return False


def run_tenant(tenant_id, agents_to_run):
    log(f"\n{'='*60}")
    log(f"TENANT: {tenant_id}")
    log(f"{'='*60}")
    ctx = build_client_context(tenant_id)
    log(f"  client={ctx.client_name} | vertical={ctx.industry_vertical} | publisher={ctx.publisher_type}")
    for agent_class, label in agents_to_run:
        run_agent(agent_class, ctx, label)


# ── Import all agents ──────────────────────────────────────────────────────────

# Layer 1 — Intelligence
from denzo.agents.layer1_research.keyword_strategist import KeywordStrategist
from denzo.agents.layer1_research.keyword_clusterer import KeywordClusterer
from denzo.agents.layer1_research.competitor_intel import CompetitorIntel
from denzo.agents.layer1_research.technical_auditor import TechnicalAuditor
from denzo.agents.layer1_research.site_style_analyzer import SiteStyleAnalyzer
from denzo.agents.layer1_research.data_intelligence import DataIntelligence
from denzo.agents.layer1_research.gbp_optimizer import GBPOptimizer
# Layer 2 — Strategy
from denzo.agents.layer2_strategy.eeat_architect import EEATArchitect
from denzo.agents.layer2_strategy.schema_engineer import SchemaEngineer
from denzo.agents.layer2_strategy.vertical_matrix_generator import VerticalMatrixGenerator
# Layer 3 — Generation
from denzo.agents.layer3_production.programmatic_seo import ProgrammaticSEO
# Layer 4 — Optimization
from denzo.agents.layer3_production.content_optimizer import ContentOptimizer
from denzo.agents.layer3_production.visual_content_optimizer import VisualContentOptimizer
from denzo.agents.layer3_production.geo_optimizer import GEOOptimizer
from denzo.agents.layer3_production.internal_linker import InternalLinker
from denzo.agents.layer3_production.content_freshness import ContentFreshness
# Layer 5 — Publishing
from denzo.agents.layer4_publishing.wordpress_publisher import WordPressPublisher
from denzo.agents.layer4_publishing.github_publisher import GitHubPublisher
# Layer 6 — Analytics
from denzo.agents.layer5_monitoring.rank_tracker import RankTracker
from denzo.agents.layer5_monitoring.geo_query_generator import GEOQueryGenerator
from denzo.agents.layer5_monitoring.geo_monitor import GEOMonitor
from denzo.agents.layer5_monitoring.serp_intelligence import SERPIntelligence
from denzo.agents.layer5_monitoring.reviews_intelligence import ReviewsIntelligence
from denzo.agents.layer5_monitoring.roi_attribution import ROIAttribution


if __name__ == "__main__":
    log("Starting full pipeline for all tenants...")
    log(f"Start time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    check_running_agents()

    # ── 1. ACG ──────────────────────────────────────────────────────────────────
    run_tenant('auto-collision-group', [
        (ContentOptimizer,  "Content Optimizer"),
        (GEOOptimizer,      "GEO Optimizer"),
        (InternalLinker,    "Internal Linker"),
        (WordPressPublisher,"WordPress Publisher"),
        (RankTracker,       "Rank Tracker"),
        (SERPIntelligence,  "SERP Intelligence"),
        (ROIAttribution,    "ROI Attribution"),
    ])

    # ── 2. NoHo ─────────────────────────────────────────────────────────────────
    run_tenant('noho-collision-center', [
        (ContentOptimizer,  "Content Optimizer"),
        (GEOOptimizer,      "GEO Optimizer"),
        (InternalLinker,    "Internal Linker"),
        (GitHubPublisher,   "GitHub Publisher"),
        (RankTracker,       "Rank Tracker"),
        (GEOQueryGenerator, "GEO Query Generator"),
        (GEOMonitor,        "GEO Monitor"),
    ])

    # ── 3. BMW Ontario ──────────────────────────────────────────────────────────
    run_tenant('bmw-of-ontario', [
        (GEOOptimizer,      "GEO Optimizer"),
        (InternalLinker,    "Internal Linker"),
        (RankTracker,       "Rank Tracker"),
        (SERPIntelligence,  "SERP Intelligence"),
        (ROIAttribution,    "ROI Attribution"),
        (GEOMonitor,        "GEO Monitor"),
    ])

    # ── 4. Denzo Studios ────────────────────────────────────────────────────────
    run_tenant('denzo-studios', [
        (ContentOptimizer,  "Content Optimizer"),
        (GEOOptimizer,      "GEO Optimizer"),
        (InternalLinker,    "Internal Linker"),
        (RankTracker,       "Rank Tracker"),
        (GEOMonitor,        "GEO Monitor"),
    ])

    # ── 5. TAMGO AI ─────────────────────────────────────────────────────────────
    run_tenant('tamgo-ai', [
        (ContentOptimizer,  "Content Optimizer"),
        (GEOOptimizer,      "GEO Optimizer"),
        (InternalLinker,    "Internal Linker"),
        (RankTracker,       "Rank Tracker"),
        (GEOMonitor,        "GEO Monitor"),
    ])

    log("\n" + "="*60)
    log("ALL TENANTS COMPLETE")
    log(f"End time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log("="*60)
