"""
run_all_agents.py — Full pipeline runner for all tenants.

Usage:
  python3 run_all_agents.py              # Default: optimization + publishing + analytics (resume-friendly)
  python3 run_all_agents.py --full       # Complete pipeline: Layer 1 → 2 → 3 → 4 → 5 → 6
  python3 run_all_agents.py --from 3     # Start from Layer 3 (Programmatic SEO) through Layer 6

SAFETY: Checks for running agents before starting. Will NOT execute if any agents
are already working (e.g., Director is running). Use --force to override.
"""
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, '/root/denzo-seo')

from denzo.context.builder import build_client_context
from denzo.agents.base_agent import db_execute, db_write
from denzo.agents.runner import AgentRunner


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
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
        if "--force" in sys.argv:
            log("--force flag set — proceeding despite running agents.")
            return
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


def run_layer(tenant_id, ctx, agents_spec):
    """Run a list of (agent_class, label) tuples sequentially."""
    for agent_class, label in agents_spec:
        run_agent(agent_class, ctx, label)


# ── Agent imports by layer ──────────────────────────────────────────────────────

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
from denzo.agents.layer4_publishing.indexation_accelerator import IndexationAccelerator

# Layer 6 — Analytics
from denzo.agents.layer5_monitoring.rank_tracker import RankTracker
from denzo.agents.layer5_monitoring.geo_query_generator import GEOQueryGenerator
from denzo.agents.layer5_monitoring.geo_monitor import GEOMonitor
from denzo.agents.layer5_monitoring.serp_intelligence import SERPIntelligence
from denzo.agents.layer5_monitoring.reviews_intelligence import ReviewsIntelligence
from denzo.agents.layer5_monitoring.roi_attribution import ROIAttribution
from denzo.agents.layer5_monitoring.content_duplicate_checker import ContentDuplicateChecker
from denzo.agents.layer5_monitoring.perplexity_tracker import PerplexityTracker
from denzo.agents.layer5_monitoring.geo_gap_closer import GEOGapCloser


# ── Agent specs by layer ────────────────────────────────────────────────────────

LAYER_1_AGENTS = [
    (KeywordStrategist,   "Keyword Strategist"),
    (KeywordClusterer,    "Keyword Clusterer"),
    (CompetitorIntel,     "Competitor Intel"),
    (TechnicalAuditor,    "Technical Auditor"),
    (SiteStyleAnalyzer,   "Site Style Analyzer"),
    (DataIntelligence,    "Data Intelligence"),
    (GBPOptimizer,        "GBP Optimizer"),
]

LAYER_2_AGENTS = [
    (EEATArchitect,           "E-E-A-T Architect"),
    (SchemaEngineer,          "Schema Engineer"),
    (VerticalMatrixGenerator, "Vertical Matrix Generator"),
]

LAYER_3_AGENTS = [
    (ProgrammaticSEO, "Programmatic SEO"),
]

LAYER_4_AGENTS = [
    (ContentOptimizer,       "Content Optimizer"),
    (VisualContentOptimizer, "Visual Content Optimizer"),
    (GEOOptimizer,           "GEO Optimizer"),
    (InternalLinker,         "Internal Linker"),
]

LAYER_5_AGENTS = [
    (WordPressPublisher,      "WordPress Publisher"),
    (GitHubPublisher,         "GitHub Publisher"),
    (IndexationAccelerator,   "Indexation Accelerator"),
]

LAYER_6_AGENTS = [
    (RankTracker,        "Rank Tracker"),
    (GEOQueryGenerator,  "GEO Query Generator"),
    (GEOMonitor,         "GEO Monitor"),
    (SERPIntelligence,   "SERP Intelligence"),
    (ReviewsIntelligence,"Reviews Intelligence"),
    (ROIAttribution,           "ROI Attribution"),
    (ContentFreshness,         "Content Freshness"),
    (ContentDuplicateChecker,  "Content Duplicate Checker"),
    (PerplexityTracker,        "Perplexity Tracker"),
    (GEOGapCloser,             "GEO Gap Closer"),
]

ALL_LAYERS = [
    ("Layer 1 — Intelligence", LAYER_1_AGENTS),
    ("Layer 2 — Strategy",     LAYER_2_AGENTS),
    ("Layer 3 — Generation",   LAYER_3_AGENTS),
    ("Layer 4 — Optimization", LAYER_4_AGENTS),
    ("Layer 5 — Publishing",   LAYER_5_AGENTS),
    ("Layer 6 — Analytics",    LAYER_6_AGENTS),
]

# Default (resume) mode: only optimization through analytics
# This is safe to run repeatedly — skips expensive Layer 1/2/3 re-runs
RESUME_LAYERS = [
    ("Layer 4 — Optimization", LAYER_4_AGENTS),
    ("Layer 5 — Publishing",   LAYER_5_AGENTS),
    ("Layer 6 — Analytics",    LAYER_6_AGENTS),
]


def run_tenant_full(tenant_id, start_layer=1):
    """Run the full pipeline for a tenant, optionally starting from a specific layer."""
    log(f"\n{'='*60}")
    log(f"TENANT: {tenant_id}")
    log(f"{'='*60}")
    ctx = build_client_context(tenant_id)
    log(f"  client={ctx.client_name} | vertical={ctx.industry_vertical} | publisher={ctx.publisher_type}")

    layers_to_run = [
        (label, agents) for label, agents in ALL_LAYERS
        if int(label.split()[2]) >= start_layer  # "Layer 1 — ..."
    ]

    for layer_label, agents in layers_to_run:
        log(f"\n── {layer_label} ──")
        run_layer(tenant_id, ctx, agents)


# ── Tenant definitions ──────────────────────────────────────────────────────────

TENANTS = [
    "auto-collision-group",
    "noho-collision-center",
    "bmw-of-ontario",
    "denzo-studios",
    "tamgo-ai",
]


if __name__ == "__main__":
    run_full = "--full" in sys.argv
    from_layer = 4  # default: resume from optimization

    for arg in sys.argv:
        if arg.startswith("--from="):
            from_layer = int(arg.split("=")[1])
        elif arg == "--from":
            idx = sys.argv.index("--from")
            from_layer = int(sys.argv[idx + 1])

    if run_full:
        from_layer = 1

    mode_label = "FULL PIPELINE" if from_layer <= 1 else f"FROM LAYER {from_layer}"
    log(f"Starting {mode_label} for all tenants...")
    log(f"Start time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")

    check_running_agents()

    for tid in TENANTS:
        run_tenant_full(tid, start_layer=from_layer)

    log("\n" + "="*60)
    log("ALL TENANTS COMPLETE")
    log(f"End time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log("="*60)
