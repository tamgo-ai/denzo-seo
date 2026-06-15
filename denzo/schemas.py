"""
schemas.py — Pydantic models for inter-agent settings contracts.

Validates that data written by one agent matches what downstream agents expect.
Catches "contract breakage" at write time instead of silent runtime failures.

Each schema maps to a settings key used by agents to communicate via the DB.
When an agent calls save_output("key", data), the data is validated against
the corresponding schema. On load_output("key"), the data is re-validated.

Non-blocking by default: validation errors log warnings, never crash the pipeline.
Set DENZO_STRICT_SCHEMAS=true in .env to make validation errors fatal (for CI/testing).

Usage in agents:
    from denzo.schemas import SCHEMAS
    self.save_output("brand_voice", data, schema=SCHEMAS["brand_voice"])
    data = self.load_output("brand_voice", schema=SCHEMAS["brand_voice"])
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

STRICT_MODE = os.getenv("DENZO_STRICT_SCHEMAS", "").lower() in ("1", "true", "yes")


# ── Helper ────────────────────────────────────────────────────────────────────

def _validate(schema_name: str, schema_cls: type[BaseModel], data: Any) -> Optional[dict]:
    """
    Validate data against a Pydantic model. Returns parsed dict or None.
    In non-strict mode, logs warnings. In strict mode, raises ValueError.
    """
    if data is None:
        return None
    try:
        validated = schema_cls.model_validate(data)
        return validated.model_dump()
    except ValidationError as e:
        msg = f"[Schema:{schema_name}] Validation failed: {e}"
        if STRICT_MODE:
            raise ValueError(msg) from e
        logger.warning(msg)
        # Return original data as-is — don't break the pipeline
        return data if isinstance(data, dict) else None


# ── Inter-agent contract schemas ──────────────────────────────────────────────

class BrandVoice(BaseModel):
    """Written by: Site Style Analyzer. Read by: Content Optimizer, Programmatic SEO, GEO Optimizer."""
    tone: str = Field(default="professional", description="e.g. professional, friendly, authoritative")
    personality: str = Field(default="", description="Narrative personality description")
    phrases_to_use: list[str] = Field(default_factory=list)
    phrases_to_avoid: list[str] = Field(default_factory=list)
    sample_sentences: list[str] = Field(default_factory=list, description="3-5 example sentences in the brand voice")


class SiteStyleGuide(BaseModel):
    """Written by: Technical Auditor / Site Style Analyzer. Read by: Programmatic SEO, GitHub Publisher."""
    colors: dict = Field(default_factory=dict, description="e.g. {primary: '#hex', secondary: '#hex'}")
    fonts: dict = Field(default_factory=dict, description="e.g. {heading: 'name', body: 'name'}")
    logo_url: str = Field(default="")
    favicon_url: str = Field(default="")
    design_notes: str = Field(default="")


class SchemaFAQ(BaseModel):
    """Written by: Schema Engineer. Read by: Programmatic SEO."""
    questions: list[dict] = Field(default_factory=list, description="[{'q': str, 'a': str}, ...]")
    main_entity: str = Field(default="", description="Main entity being described (e.g. 'LocalBusiness')")


class SchemaLocalBusiness(BaseModel):
    """Written by: Schema Engineer. Read by: Programmatic SEO."""
    business_type: str = Field(default="LocalBusiness", description="Schema.org type")
    name: str = Field(default="")
    address: dict = Field(default_factory=dict)
    geo: dict = Field(default_factory=dict)
    telephone: str = Field(default="")
    url: str = Field(default="")
    opening_hours: list[str] = Field(default_factory=list)


class TechnicalAudit(BaseModel):
    """Written by: Technical Auditor. Read by: E-E-A-T Architect."""
    score: int = Field(default=0, ge=0, le=100)
    issues: list[dict] = Field(default_factory=list, description="[{'severity': str, 'description': str, 'fix': str}]")
    crawl_errors: int = Field(default=0)
    mobile_issues: int = Field(default=0)
    speed_score: Optional[int] = Field(default=None)
    recommendations: list[str] = Field(default_factory=list)


class ReviewsIntelligence(BaseModel):
    """Written by: Reviews Intelligence. Read by: E-E-A-T Architect, Programmatic SEO."""
    average_rating: float = Field(default=0.0, ge=0, le=5)
    total_reviews: int = Field(default=0)
    sentiment_summary: str = Field(default="")
    top_positive_themes: list[str] = Field(default_factory=list)
    top_negative_themes: list[str] = Field(default_factory=list)
    review_platforms: list[str] = Field(default_factory=list)


class DataIntelligenceReport(BaseModel):
    """Written by: Data Intelligence. Read by: Programmatic SEO."""
    market_trends: list[str] = Field(default_factory=list)
    industry_stats: list[dict] = Field(default_factory=list, description="[{'stat': str, 'source': str}]")
    target_demographics: str = Field(default="")
    seasonal_trends: dict = Field(default_factory=dict)


class PipelinePlan(BaseModel):
    """Written by: Director. Read by: Director (resume)."""
    strategy: str = Field(default="")
    phases: list[dict] = Field(default_factory=list)
    estimated_pages: int = Field(default=0)
    target_keywords: list[str] = Field(default_factory=list)
    notes: str = Field(default="")


class PipelineProgress(BaseModel):
    """Written by: Director. Read by: Director (resume)."""
    cycles_completed: int = Field(default=0)
    last_state: Optional[dict] = Field(default=None)


class PublishVelocity(BaseModel):
    """Written by: GitHub Publisher. Read by: GitHub Publisher, WordPress Publisher."""
    max_pages_per_day: int = Field(default=30, ge=1, le=100)
    min_delay_seconds: int = Field(default=30, ge=10, le=300)
    max_delay_seconds: int = Field(default=90, ge=10, le=300)


class MaxPagesCap(BaseModel):
    """Written by: E-E-A-T Architect. Read by: Vertical Matrix Generator."""
    max_pages: int = Field(default=500, ge=10, le=5000)


# ── Schema registry ───────────────────────────────────────────────────────────

SCHEMAS: dict[str, type[BaseModel]] = {
    "brand_voice":              BrandVoice,
    "site_style_guide":         SiteStyleGuide,
    "schema_faq":               SchemaFAQ,
    "schema_local_business":    SchemaLocalBusiness,
    "technical_audit":          TechnicalAudit,
    "reviews_intelligence":     ReviewsIntelligence,
    "data_intelligence_report": DataIntelligenceReport,
    "pipeline_plan":            PipelinePlan,
    "pipeline_progress":        PipelineProgress,
    "publish_velocity":         PublishVelocity,
    "max_pages_cap":            MaxPagesCap,
}


def validate_setting(key: str, data: Any) -> Any:
    """
    Validate a settings value against its registered schema.
    Called by save_output() and load_output().

    Returns validated data, or original data if no schema is registered
    or validation fails in non-strict mode.
    """
    schema_cls = SCHEMAS.get(key)
    if schema_cls is None:
        return data  # No schema registered — pass through

    result = _validate(key, schema_cls, data)
    return result if result is not None else data
