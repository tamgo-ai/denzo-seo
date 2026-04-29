"""
Agent registry — maps agent names to their classes.
All 15 agents listed; MVP builds the first 6.
"""
from denzo.agents.base_agent import ClientContext

# (module_path, class_name, layer, color)
AGENT_REGISTRY = {
    # Layer 0 — Director (autonomous orchestrator)
    "Pipeline Director":   ("director",                             "PipelineDirector",   0, "indigo"),
    # Layer 1 — Research
    "Keyword Strategist":    ("layer1_research.keyword_strategist",    "KeywordStrategist",  1, "blue"),
    "Competitor Intel":      ("layer1_research.competitor_intel",       "CompetitorIntel",    1, "purple"),
    "Technical Auditor":     ("layer1_research.technical_auditor",      "TechnicalAuditor",   1, "gray"),
    "Site Style Analyzer":   ("layer1_research.site_style_analyzer",    "SiteStyleAnalyzer",  1, "teal"),
    "Data Intelligence":     ("layer1_research.data_intelligence",      "DataIntelligence",   1, "indigo"),
    # Layer 2 — Strategy
    "E-E-A-T Architect":   ("layer2_strategy.eeat_architect",       "EEATArchitect",      2, "indigo"),
    "Schema Engineer":     ("layer2_strategy.schema_engineer",      "SchemaEngineer",     2, "violet"),
    # Layer 3 — Generation (creates content from scratch)
    "Programmatic SEO":    ("layer3_production.programmatic_seo",   "ProgrammaticSEO",    3, "orange"),
    # Layer 4 — Optimization (refines content — needs Layer 3 to finish first)
    "Content Optimizer":        ("layer3_production.content_optimizer",         "ContentOptimizer",        4, "yellow"),
    "Visual Content Optimizer": ("layer3_production.visual_content_optimizer",  "VisualContentOptimizer",  4, "pink"),
    "GEO Optimizer":            ("layer3_production.geo_optimizer",             "GEOOptimizer",            4, "teal"),
    "Internal Linker":          ("layer3_production.internal_linker",           "InternalLinker",          4, "green"),
    # Layer 5 — Publishing
    "GitHub Publisher":    ("layer4_publishing.github_publisher",   "GitHubPublisher",    5, "slate"),
    "WordPress Publisher": ("layer4_publishing.wordpress_publisher","WordPressPublisher", 5, "sky"),
    # Layer 6 — Analytics
    "Rank Tracker":          ("layer5_monitoring.rank_tracker",            "RankTracker",          6, "emerald"),
    "GEO Query Generator":   ("layer5_monitoring.geo_query_generator",     "GEOQueryGenerator",    6, "violet"),
    "GEO Monitor":           ("layer5_monitoring.geo_monitor",             "GEOMonitor",           6, "cyan"),
    "SERP Intelligence":     ("layer5_monitoring.serp_intelligence",       "SERPIntelligence",     6, "rose"),
    "Reviews Intelligence":  ("layer5_monitoring.reviews_intelligence",    "ReviewsIntelligence",  6, "orange"),
    "ROI Attribution":       ("layer5_monitoring.roi_attribution",         "ROIAttribution",       6, "amber"),
}

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
