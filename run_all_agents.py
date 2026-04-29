"""
run_all_agents.py — Full pipeline runner for all tenants.
Runs agents sequentially to avoid API rate limit conflicts.
"""
import sys
import traceback
from datetime import datetime

sys.path.insert(0, '/root/denzo-seo')

from denzo.context.builder import build_client_context
from denzo.agents.base_agent import db_execute, db_write

def log(msg):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


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

from denzo.agents.layer3_production.content_optimizer import ContentOptimizer
from denzo.agents.layer3_production.geo_optimizer import GEOOptimizer
from denzo.agents.layer3_production.internal_linker import InternalLinker
from denzo.agents.layer4_publishing.wordpress_publisher import WordPressPublisher
from denzo.agents.layer4_publishing.github_publisher import GitHubPublisher
from denzo.agents.layer5_monitoring.rank_tracker import RankTracker
from denzo.agents.layer5_monitoring.serp_intelligence import SERPIntelligence
from denzo.agents.layer5_monitoring.roi_attribution import ROIAttribution
from denzo.agents.layer5_monitoring.geo_monitor import GEOMonitor
from denzo.agents.layer1_research.keyword_strategist import KeywordStrategist
from denzo.agents.layer2_strategy.eeat_architect import EEATArchitect
from denzo.agents.layer2_strategy.schema_engineer import SchemaEngineer
from denzo.agents.layer3_production.programmatic_seo import ProgrammaticSEO

# ── Pipeline per tenant ────────────────────────────────────────────────────────

log("Starting full pipeline for all tenants...")
log(f"Start time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

# ── 1. ACG — 393 ready pages, 162 published, needs optimization + republish ──
run_tenant('auto-collision-group', [
    (ContentOptimizer,  "Content Optimizer"),
    (GEOOptimizer,      "GEO Optimizer"),
    (InternalLinker,    "Internal Linker"),
    (WordPressPublisher,"WordPress Publisher"),
    (RankTracker,       "Rank Tracker"),
    (SERPIntelligence,  "SERP Intelligence"),
    (ROIAttribution,    "ROI Attribution"),
])

# ── 2. NoHo — 484 ready pages, scores reset, full optimization ──
run_tenant('noho-collision-center', [
    (ContentOptimizer,  "Content Optimizer"),
    (GEOOptimizer,      "GEO Optimizer"),
    (InternalLinker,    "Internal Linker"),
    (GitHubPublisher,   "GitHub Publisher"),
    (RankTracker,       "Rank Tracker"),
    (GEOMonitor,        "GEO Monitor"),
])

# ── 3. BMW Ontario — 15 ready pages, good quality, new competitor data ──
run_tenant('bmw-of-ontario', [
    (GEOOptimizer,      "GEO Optimizer"),
    (InternalLinker,    "Internal Linker"),
    (RankTracker,       "Rank Tracker"),
    (SERPIntelligence,  "SERP Intelligence"),
    (ROIAttribution,    "ROI Attribution"),
    (GEOMonitor,        "GEO Monitor"),
])

# ── 4. Denzo Studios — 31 ready pages ──
run_tenant('denzo-studios', [
    (ContentOptimizer,  "Content Optimizer"),
    (GEOOptimizer,      "GEO Optimizer"),
    (InternalLinker,    "Internal Linker"),
    (RankTracker,       "Rank Tracker"),
    (GEOMonitor,        "GEO Monitor"),
])

# ── 5. TAMGO AI — 16 ready pages, no publisher configured ──
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
