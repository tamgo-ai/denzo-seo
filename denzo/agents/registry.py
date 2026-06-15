"""
Agent registry — world-class multi-vertical SEO + GEO platform.
26 agents across 7 layers: Intelligence → Strategy → Generation → Optimization → Publishing → Analytics
"""
from denzo.agents.base_agent import ClientContext

# (module_path, class_name, layer, color)
AGENT_REGISTRY = {
    # Layer 0 — Director (autonomous orchestrator)
    "Pipeline Director":         ("director",                                    "PipelineDirector",         0, "indigo"),
    # Layer 1 — Intelligence (research + discovery)
    "Keyword Strategist":        ("layer1_research.keyword_strategist",          "KeywordStrategist",        1, "blue"),
    "Keyword Clusterer":         ("layer1_research.keyword_clusterer",           "KeywordClusterer",         1, "cyan"),
    "Competitor Intel":          ("layer1_research.competitor_intel",            "CompetitorIntel",          1, "purple"),
    "Technical Auditor":         ("layer1_research.deep_auditor",                 "DeepTechnicalAuditor",     1, "gray"),
    "Site Style Analyzer":       ("layer1_research.site_style_analyzer",         "SiteStyleAnalyzer",        1, "teal"),
    "Data Intelligence":         ("layer1_research.data_intelligence",           "DataIntelligence",         1, "indigo"),
    "GBP Optimizer":             ("layer1_research.gbp_optimizer",               "GBPOptimizer",             1, "green"),
    # Layer 2 — Strategy (content architecture)
    "E-E-A-T Architect":         ("layer2_strategy.eeat_architect",              "EEATArchitect",            2, "indigo"),
    "Schema Engineer":           ("layer2_strategy.schema_engineer",             "SchemaEngineer",           2, "violet"),
    "Vertical Matrix Generator": ("layer2_strategy.vertical_matrix_generator",   "VerticalMatrixGenerator",  2, "fuchsia"),
    # Layer 3 — Generation (creates content from scratch)
    "Programmatic SEO":          ("layer3_production.programmatic_seo",          "ProgrammaticSEO",          3, "orange"),
    # Layer 4 — Optimization (refines + enhances — needs Layer 3 to finish first)
    "Content Optimizer":         ("layer3_production.content_optimizer",         "ContentOptimizer",         4, "yellow"),
    "Visual Content Optimizer":  ("layer3_production.visual_content_optimizer",  "VisualContentOptimizer",   4, "pink"),
    "GEO Optimizer":             ("layer3_production.geo_optimizer",             "GEOOptimizer",             4, "teal"),
    "Internal Linker":           ("layer3_production.internal_linker",           "InternalLinker",           4, "green"),
    "Content Freshness":         ("layer3_production.content_freshness",         "ContentFreshness",         4, "lime"),
    # Layer 5 — Publishing
    "GitHub Publisher":          ("layer4_publishing.github_publisher",          "GitHubPublisher",          5, "slate"),
    "WordPress Publisher":       ("layer4_publishing.wordpress_publisher",       "WordPressPublisher",       5, "sky"),
    # Layer 6 — Analytics + Monitoring
    "Rank Tracker":              ("layer5_monitoring.rank_tracker",              "RankTracker",              6, "emerald"),
    "GEO Query Generator":       ("layer5_monitoring.geo_query_generator",       "GEOQueryGenerator",        6, "violet"),
    "GEO Monitor":               ("layer5_monitoring.geo_monitor",               "GEOMonitor",               6, "cyan"),
    "SERP Intelligence":         ("layer5_monitoring.serp_intelligence",         "SERPIntelligence",         6, "rose"),
    "Reviews Intelligence":      ("layer5_monitoring.reviews_intelligence",      "ReviewsIntelligence",      6, "orange"),
    "ROI Attribution":           ("layer5_monitoring.roi_attribution",           "ROIAttribution",           6, "amber"),
    "Content Duplicate Checker": ("layer5_monitoring.content_duplicate_checker", "ContentDuplicateChecker",  6, "rose"),
    "Perplexity Tracker":        ("layer5_monitoring.perplexity_tracker",        "PerplexityTracker",       6, "violet"),
    "GEO Gap Closer":            ("layer5_monitoring.geo_gap_closer",            "GEOGapCloser",            6, "emerald"),
    "Indexation Accelerator":   ("layer4_publishing.indexation_accelerator",    "IndexationAccelerator",   5, "emerald"),
    # Layer 5 — Action Agents (autonomous management beyond content)
    "GBP Autopilot":             ("layer4_publishing.gbp_autopilot",              "GBPAutopilot",            5, "amber"),
    "Video Engine":              ("layer4_publishing.video_engine",               "VideoEngine",             5, "rose"),
    # ── Capa 0.5 — Discovery & Reconciliation (runs BEFORE all generation) ──
    "Site Inventory":            ("layer1_research.site_inventory",               "SiteInventoryAgent",      1, "stone"),
    "Keyword Footprint":         ("layer1_research.keyword_footprint",             "KeywordFootprintAgent",   1, "sand"),
    "GEO Baseline":              ("layer5_monitoring.geo_baseline",                "GEOBaselineAgent",        1, "sky"),
}

DISCOVERY_AGENTS = ["Site Inventory", "Keyword Footprint", "GEO Baseline"]

LAYER_LABELS = {
    0: "Director",
    1: "Intelligence",
    2: "Strategy",
    3: "Generation",
    4: "Optimization",
    5: "Publishing",
    6: "Analytics",
}

# Agents to seed on new client creation
DEFAULT_AGENTS = list(AGENT_REGISTRY.keys())


def get_agent(name: str, ctx: ClientContext):
    if name not in AGENT_REGISTRY:
        raise ValueError(f"Unknown agent: '{name}'")
    module_path, class_name, layer, color = AGENT_REGISTRY[name]
    import importlib
    mod = importlib.import_module(f"denzo.agents.{module_path}")
    cls = getattr(mod, class_name)
    return cls(ctx=ctx)
