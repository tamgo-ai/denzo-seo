"""
TenantAwareBaseAgent — the backbone of DENZO-SEO.
Every agent inherits from this. ClientContext carries all client-specific data.
No hardcoded business info anywhere — everything flows from the DB via ClientContext.
"""
import sqlite3, os, time, threading
from dataclasses import dataclass, field
from typing import List
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "denzo.db")

# ── Global API rate limiter — shared across ALL tenants and ALL agents ────────
# Configurable via DENZO_API_CONCURRENCY (default 4). The 2-second inter-call
# gap provides enough throttling; the semaphore controls peak parallelism.
_MAX_CONCURRENCY = int(os.getenv("DENZO_API_CONCURRENCY", "4"))
_api_semaphore = threading.Semaphore(_MAX_CONCURRENCY)
_last_api_call = 0.0
_api_lock = threading.Lock()

# Singleton Anthropic client — created ONCE, reused by all agents
_anthropic_client = None
_anthropic_client_lock = threading.Lock()


def _get_anthropic_client():
    """Return the module-level singleton Anthropic client. Thread-safe lazy init."""
    global _anthropic_client
    if _anthropic_client is None:
        with _anthropic_client_lock:
            if _anthropic_client is None:
                import anthropic
                _anthropic_client = anthropic.Anthropic(
                    api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                    timeout=90.0,
                    max_retries=0,  # we handle retries ourselves
                )
    return _anthropic_client


# ── Thread-local SQLite connections ───────────────────────────────────────────

_sqlite_local = threading.local()


def _get_conn():
    """Return this thread's persistent SQLite connection. Creates it on first use.

    Uses the unified _open_conn from db.py so PRAGMAs never diverge
    between web requests and agent threads.
    """
    conn = getattr(_sqlite_local, "conn", None)
    if conn is None:
        from denzo.db import _open_conn
        conn = _open_conn(timeout=30)
        _sqlite_local.conn = conn
    return conn


def close_thread_connection():
    """Close this thread's SQLite connection. Call on thread exit for cleanup."""
    conn = getattr(_sqlite_local, "conn", None)
    if conn:
        try:
            conn.close()
        except Exception:
            pass
        _sqlite_local.conn = None


# ── Client Context ──────────────────────────────────────────────────────────

@dataclass
class ClientContext:
    """
    Replaces hardcoded NOHO_CONTEXT / ACG_CONTEXT.
    Built from DB by context/builder.py — never constructed manually.
    """
    tenant_id:          str
    client_name:        str
    website_url:        str        = ""
    phone:              str        = ""
    address:            str        = ""
    primary_city:       str        = ""
    state:              str        = "CA"
    tagline:            str        = ""
    description:        str        = ""
    service_cities:     List[str]  = field(default_factory=list)
    certifications:     List[str]  = field(default_factory=list)
    services:           List[str]  = field(default_factory=list)
    differentiators:    List[str]  = field(default_factory=list)
    competitors:        List[dict] = field(default_factory=list)
    insurance_partners: List[str]  = field(default_factory=list)
    domain:             str        = ""
    industry_vertical:  str        = "general"
    brand_tier:         str        = "mid"
    is_multilocation:   bool       = False
    locations_json:     str        = ""
    publisher_type:     str        = "github"
    github_repo:        str        = ""
    github_branch:      str        = "main"
    github_token:       str        = ""
    github_format:      str        = "html"
    github_path_prefix: str        = ""
    pages_domain:       str        = ""
    wp_url:             str        = ""
    wp_user:            str        = ""
    wp_app_password:    str        = ""
    dont_sell:          List[str]  = field(default_factory=list)

    def to_prompt_block(self) -> str:
        """Inject this into every agent prompt — replaces NOHO_CONTEXT."""
        cities    = ", ".join(self.service_cities) or self.primary_city
        certs     = ", ".join(self.certifications) or "N/A"
        svcs      = ", ".join(self.services)       or "General services"
        diffs     = "\n- ".join(self.differentiators)
        ins       = ", ".join(self.insurance_partners) or "All major insurers"
        comp_list = ", ".join(c.get("name", "") for c in self.competitors) or "N/A"

        industry = self.industry_vertical or "general"
        cert_label = "CERTIFICATIONS"
        ins_line = ""
        if industry in ("auto_body_shop", "collision_repair"):
            cert_label = "MANUFACTURER CERTIFICATIONS"
            ins_line = f"INSURANCE PARTNERS: {ins}\n"
        elif industry in ("automotive_dealership", "car_dealership"):
            cert_label = "DEALER AUTHORIZATIONS & PROGRAMS"
        elif industry in ("law_firm",):
            cert_label = "BAR MEMBERSHIPS & PRACTICE AREAS"
        elif industry in ("saas_tech", "agency"):
            cert_label = "CERTIFICATIONS & PARTNERSHIPS"

        dont_sell_line = ""
        if self.dont_sell:
            dont_sell_line = f"\nOUT OF SCOPE — NEVER target these topics: {', '.join(self.dont_sell)}\n"

        return f"""BUSINESS: {self.client_name} — {self.domain} — Tel: {self.phone}
Address: {self.address}
Primary market: {self.primary_city}, {self.state}
Service area: {cities}
Industry vertical: {industry}
Brand tier: {self.brand_tier}
Tagline: "{self.tagline}"
Description: {self.description}

SERVICES OFFERED: {svcs}
{cert_label}: {certs}
{ins_line}KEY DIFFERENTIATORS:
- {diffs}

KNOWN COMPETITORS: {comp_list}
{dont_sell_line}"""

    def to_brand_voice_block(self, brand_voice: dict | None = None) -> str:
        """Build a Brand Voice DNA prompt block from stored brand_voice settings.
        Shared by ProgrammaticSEO, ContentOptimizer, and GEOOptimizer."""
        if not brand_voice:
            return ""
        ctx = self
        return f"""
BRAND VOICE DNA — follow this exactly:
- Brand name: {brand_voice.get('brand_name', ctx.client_name)}
- Writing style: {brand_voice.get('writing_style', 'professional')}
- Years of experience to reference: {brand_voice.get('years_experience', '')}
- Clients served: {brand_voice.get('clients_served', '')}
- Founder voice: {brand_voice.get('founder_name', '')}
- Key proprietary insights to weave in: {brand_voice.get('key_insight_1', '')} / {brand_voice.get('key_insight_2', '')} / {brand_voice.get('key_insight_3', '')}
- Contrarian position: {brand_voice.get('contrarian_position', '')}
- Signature phrases to use: {brand_voice.get('phrases_to_use', '')}
- Phrases to NEVER use: {brand_voice.get('phrases_to_avoid', '')}

AUTHORITY SIGNAL RULES — include at least 2 of these in every piece:
1. First-person data: "In our experience with [X clients/years]..."
2. Named framework: Create a named methodology (e.g. "The [Brand] [Method/Framework/Approach]")
3. Contrarian position: "Most [industry players] will tell you X, but that's wrong because..."
4. Specific numbers: Use exact figures, percentages, timeframes — never vague estimates
5. Expert quote: "As {brand_voice.get('founder_name', 'our founder')}, puts it: '...'"
"""

    @property
    def all_cities(self) -> List[str]:
        cities = list(self.service_cities)
        if self.primary_city and self.primary_city not in cities:
            cities.insert(0, self.primary_city)
        return cities


# ── DB helpers ───────────────────────────────────────────────────────────────

def strip_json_fences(raw: str, start_char: str = "{") -> str:
    """
    Robustly extract a JSON object or array from a Claude response
    that may be wrapped in markdown code fences (```json ... ```).
    """
    cleaned = raw.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith(start_char):
                return candidate
    return cleaned


def db_execute(sql: str, params=(), retries=5):
    """Execute a read query. Uses thread-local connection pool."""
    for attempt in range(retries):
        try:
            conn = _get_conn()
            rows = conn.execute(sql, params).fetchall()
            return rows
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < retries - 1:
                time.sleep(0.3 * (attempt + 1))
            else:
                raise


def db_write(sql: str, params=()):
    """Execute a write query. Uses thread-local connection pool."""
    for attempt in range(5):
        try:
            conn = _get_conn()
            conn.execute(sql, params)
            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < 4:
                time.sleep(0.3 * (attempt + 1))
            else:
                raise


# ── Base Agent ───────────────────────────────────────────────────────────────

def strip_html_wrappers(content: str) -> str:
    """Strip full HTML document wrappers (DOCTYPE, html, head, body) from a fragment.
    Claude occasionally wraps content in full HTML docs even when told not to.
    Shared by ProgrammaticSEO, WordPressPublisher, and any future content generators."""
    import re as _re
    cleaned = _re.sub(r'<!DOCTYPE[^>]*>', '', content, flags=_re.IGNORECASE)
    cleaned = _re.sub(r'<html[^>]*>', '', cleaned, flags=_re.IGNORECASE)
    cleaned = _re.sub(r'</html>', '', cleaned, flags=_re.IGNORECASE)
    cleaned = _re.sub(r'<head>.*?</head>', '', cleaned, flags=_re.IGNORECASE | _re.DOTALL)
    cleaned = _re.sub(r'<body[^>]*>', '', cleaned, flags=_re.IGNORECASE)
    cleaned = _re.sub(r'</body>', '', cleaned, flags=_re.IGNORECASE)
    return cleaned.strip()


def strip_h1_tags(content: str) -> str:
    """Replace all H1 tags with H2 tags.
    WordPress themes and site templates provide H1 from the page title,
    so content fragments must never contain H1 tags."""
    import re as _re
    cleaned = _re.sub(r'<h1(\s|>)', lambda m: '<h2' + m.group(1), content)
    cleaned = _re.sub(r'</h1>', '</h2>', cleaned)
    return cleaned


def validate_page_quality(content: str, page_type: str = "service") -> list[str]:
    """Pre-publish quality gate. Returns list of issues (empty = passes).
    Google's quality threshold: pages below these standards risk
    'thin content' classification and won't rank."""
    import re as _re
    issues = []

    if not content or len(content.strip()) < 200:
        issues.append("Content too short (<200 chars)")
        return issues

    # Word count: strip HTML tags, count words
    text = _re.sub(r'<[^>]+>', ' ', content)
    text = _re.sub(r'\s+', ' ', text).strip()
    word_count = len(text.split())
    min_words = 500 if page_type in ("service", "location", "inventory", "financing") else 400
    if word_count < min_words:
        issues.append(f"Thin content: {word_count} words (min {min_words})")

    # Must have at least one H2 heading
    if not _re.search(r'<h2[^>]*>', content, _re.IGNORECASE):
        issues.append("Missing H2 heading")

    # Must have FAQ schema or definition block for SEO (supports both quote styles)
    has_faq = 'schema.org/FAQPage' in content or \
              'schema.org/Question' in content
    has_definition = '<p' in content and (' is a ' in text or ' provides ' in text or ' offers ' in text)
    if not has_faq and not has_definition:
        issues.append("Missing FAQ schema or definition block (GEO requirement)")

    # CTA must be present
    has_cta = _re.search(r'href="(tel:|/contact|/quote|/appointment|/estimate|/demo)', content, _re.IGNORECASE) or \
              'btn-primary' in content or 'cta' in content.lower()
    if not has_cta:
        issues.append("Missing call-to-action (CTA)")

    return issues


def build_llms_txt(ctx: "ClientContext", base_url: str = "") -> str:
    """Build llms.txt markdown content from ClientContext.
    Shared by WordPressPublisher and GitHubPublisher for consistency.
    See https://llmstxt.org/ for the emerging standard."""
    services_list = "\n".join(f"- {s}" for s in (ctx.services or []))
    cities = ([ctx.primary_city] if getattr(ctx, "primary_city", None) else []) + \
             (getattr(ctx, "service_cities", None) or [])
    cities_list = "\n".join(f"- {c}" for c in cities if c)
    certs_list  = "\n".join(f"- {c}" for c in (getattr(ctx, "certifications", None) or []))
    diffs_list  = "\n".join(f"- {d}" for d in (getattr(ctx, "differentiators", None) or []))
    ins_list    = "\n".join(f"- {i}" for i in (getattr(ctx, "insurance_partners", None) or []))

    primary_city = getattr(ctx, "primary_city", "") or ""
    state        = getattr(ctx, "state", "") or ""
    address      = getattr(ctx, "address", "") or (f"{primary_city}, {state}".strip(", "))
    tagline      = getattr(ctx, "tagline", "") or ""
    description  = getattr(ctx, "description", "") or \
                   f"{ctx.client_name} is a trusted local business serving {primary_city} and surrounding areas."

    services_preview = ", ".join((ctx.services or [])[:2])
    default_tagline  = f"Professional {services_preview} services in {primary_city}, {state}".strip(", .")

    content = f"""# {ctx.client_name}

> {tagline or default_tagline}

## About
{description}

## Services
{services_list or '- Professional services'}

## Locations Served
{cities_list or f'- {primary_city}'}

## Certifications & Credentials
{certs_list or '- Licensed and insured'}

## Why Choose Us
{diffs_list or '- Quality service'}

## Contact
- Phone: {ctx.phone or 'Call for info'}
- Website: {ctx.domain or base_url}
- Address: {address}
"""
    if ins_list:
        content += f"\n## Insurance Partners\n{ins_list}\n"

    domain = getattr(ctx, "domain", "") or ""
    resolved_base = base_url or domain
    if resolved_base:
        content += f"\n## Sitemap\n- {resolved_base.rstrip('/')}/sitemap.xml\n"

    return content


SEO_EXPERTISE = """You are a Senior SEO + GEO Specialist with 15 years of experience in Local SEO.
You understand:
- Google's local algorithm: proximity, relevance, prominence
- E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) for YMYL
- Topical Authority: covering ALL aspects of a topic, not isolated keywords
- GEO (Generative Engine Optimization): how Google AI Overviews, ChatGPT, and Perplexity cite content
- Schema Markup: LocalBusiness, AutoRepair, Service, FAQ, BreadcrumbList
- Internal Linking: hub-and-spoke for local businesses
- Content velocity: frequent relevant publishing accelerates indexing

GEO RULES — what makes LLMs cite your content:
1. First paragraph MUST directly answer the primary keyword (definition pattern)
2. Include specific numbers, timelines, and verifiable facts
3. Use structured Q&A / FAQ sections
4. Mention the business full name + address + phone at least once
5. Expert voice: "According to [business name]'s certified technicians..."
6. Zero fluff: every sentence must add information
"""


class TenantAwareBaseAgent:
    """
    All agents inherit from this.
    ctx (ClientContext) carries all client-specific data.
    Every DB operation is scoped to self.tenant_id — no cross-tenant leaks.
    """

    # ── Prerequisites — override in subclasses ────────────────────────────────
    PREREQUISITES: List[str] = []     # agent names that must be 'done' before running
    MIN_KEYWORDS: int = 0             # minimum keywords needed in DB

    def __init__(self, name: str, ctx: ClientContext, layer: int = 1, color: str = "blue"):
        self.name       = name
        self.ctx        = ctx
        self.tenant_id  = ctx.tenant_id
        self.layer      = layer
        self.color      = color
        self.running    = False
        self._stop      = threading.Event()

    # ── Prerequisites check ──────────────────────────────────────────────────

    def check_prerequisites(self) -> tuple[bool, str]:
        """
        Verify this agent's prerequisites are met.
        Returns (ready: bool, reason: str).
        Called by AgentRunner before calling run().
        """
        if self.MIN_KEYWORDS > 0:
            rows = db_execute(
                "SELECT COUNT(*) AS n FROM keywords WHERE tenant_id=?",
                (self.tenant_id,)
            )
            count = rows[0]["n"] if rows else 0
            if count < self.MIN_KEYWORDS:
                return False, f"Need {self.MIN_KEYWORDS} keywords, have {count}"

        for prereq_name in self.PREREQUISITES:
            rows = db_execute(
                "SELECT status FROM agents WHERE tenant_id=? AND name=?",
                (self.tenant_id, prereq_name)
            )
            status = rows[0]["status"] if rows else "unknown"
            if status != "done":
                return False, f"Prerequisite '{prereq_name}' not done (status: {status})"

        return True, "OK"

    # ── Logging ───────────────────────────────────────────────────────────────

    def log(self, message: str, level: str = "info"):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db_write(
            "INSERT INTO activity (tenant_id,type,message,agent,level,created_at) VALUES (?,?,?,?,?,?)",
            (self.tenant_id, "agent", message, self.name, level, now)
        )
        db_write(
            "UPDATE agents SET last_message=?, updated_at=? WHERE tenant_id=? AND name=?",
            (message, now, self.tenant_id, self.name)
        )
        # Trim activity log every 50 calls
        self._log_call_count = getattr(self, "_log_call_count", 0) + 1
        if self._log_call_count % 50 == 0:
            db_write(
                """DELETE FROM activity WHERE tenant_id=? AND id NOT IN (
                       SELECT id FROM activity WHERE tenant_id=?
                       ORDER BY id DESC LIMIT 2000
                   )""",
                (self.tenant_id, self.tenant_id)
            )
        print(f"[{self.tenant_id}][{self.name}] {level.upper()}: {message}")

    def set_status(self, status: str, current_task: str = "", next_task: str = ""):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db_write(
            "UPDATE agents SET status=?,current_task=?,next_task=?,updated_at=? WHERE tenant_id=? AND name=?",
            (status, current_task, next_task, now, self.tenant_id, self.name)
        )

    def stop(self):
        self._stop.set()
        self.running = False

    def should_stop(self) -> bool:
        return self._stop.is_set()

    # ── Structured output ─────────────────────────────────────────────────────

    def save_output(self, key: str, data: dict):
        """Persist agent output to settings so downstream agents can read it.

        Validates against registered Pydantic schema if one exists for this key.
        Validation errors are logged as warnings — they don't crash the pipeline
        (set DENZO_STRICT_SCHEMAS=true to make them fatal).
        """
        import json
        from denzo.schemas import validate_setting
        # Validate before saving — catches contract breakage early
        validated = validate_setting(key, data)
        data_to_save = validated if isinstance(validated, dict) else data
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
            "VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.tenant_id, key, json.dumps(data_to_save, ensure_ascii=False))
        )

    def load_output(self, key: str) -> dict | None:
        """Read agent output from settings. Validates against registered schema."""
        import json
        rows = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key=?",
            (self.tenant_id, key)
        )
        if not rows or not rows[0]["value"]:
            return None
        try:
            data = json.loads(rows[0]["value"])
        except json.JSONDecodeError:
            return None
        from denzo.schemas import validate_setting
        validated = validate_setting(key, data)
        return validated if isinstance(validated, dict) else data

    def log_result(self, what: str, count: int, examples: list[str] = None, score: str = ""):
        """Standardized completion log showing what the agent produced with examples."""
        parts = [f"{what}: {count}"]
        if score:
            parts.append(f"score={score}")
        if examples:
            sample = ", ".join(f'"{ex}"' for ex in examples[:3])
            parts.append(f"examples=[{sample}]")
        self.log(" | ".join(parts), "success" if count > 0 else "warning")

    # ── Tenant-scoped DB helpers ──────────────────────────────────────────────

    def add_keyword(self, keyword: str, volume=None, difficulty=None,
                    intent=None, location=None, category=None, priority="medium"):
        existing = db_execute(
            "SELECT id FROM keywords WHERE tenant_id=? AND keyword=? AND location=?",
            (self.tenant_id, keyword, location or "")
        )
        if not existing:
            db_write(
                "INSERT INTO keywords (tenant_id,keyword,volume,difficulty,intent,location,category,priority) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (self.tenant_id, keyword, volume, difficulty, intent,
                 location or "", category, priority)
            )

    # ── Content hashing (idempotency) ──────────────────────────────────────

    @staticmethod
    def compute_content_hash(html: str) -> str:
        """SHA256 of normalized content. Used for idempotent publishing."""
        import hashlib
        # Normalize whitespace so formatting changes don't trigger re-publish
        import re
        normalized = re.sub(r'\s+', ' ', html.strip())
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    def add_page(self, title: str, slug: str, page_type: str, location=None,
                 target_keyword=None, meta_title=None, meta_description=None,
                 content=None, notes=None):
        """Insert a page with anti-cannibalization gate.

        Before inserting, checks:
        1. Slug uniqueness (existing guard)
        2. topic_map ownership — one intent, one page
        3. Semantic duplicate detection (>90% similar = blocked)
        """
        import json as _json

        # ── Gate 0: Slug uniqueness ────────────────────────────────────────
        existing = db_execute(
            "SELECT id FROM pages WHERE tenant_id=? AND slug=?",
            (self.tenant_id, slug)
        )
        if existing:
            self.log(f"SKIP '{title}': slug '{slug}' already exists", "info")
            return

        # ── Gate 1: topic_map ownership ─────────────────────────────────────
        if target_keyword:
            topic_rows = db_execute(
                "SELECT id, status, owner_page_id, owner_url FROM topic_map "
                "WHERE tenant_id=? AND primary_keyword=?",
                (self.tenant_id, target_keyword)
            )
            if not topic_rows:
                # Check if another page already targets this keyword (regardless of topic_map)
                existing_kw = db_execute(
                    "SELECT id, title FROM pages WHERE tenant_id=? AND target_keyword=? LIMIT 1",
                    (self.tenant_id, target_keyword)
                )
                if existing_kw:
                    self.log(
                        f"BLOCKED '{title}': keyword '{target_keyword}' already targeted by "
                        f"page_id={existing_kw[0]['id']} ('{existing_kw[0]['title'][:40]}'). Cannibalization prevented.",
                        "error"
                    )
                    return
                # No existing page for this keyword → allow (topic_map will be populated later)

            topic = topic_rows[0] if topic_rows else None
            if topic:  # Only enforce ownership checks if topic_map entry exists
                if topic["status"] == "owned_existing":
                    self.log(
                        f"BLOCKED '{title}': keyword '{target_keyword}' already owned by "
                        f"existing client content at {topic['owner_url']}. Cannibalization prevented.",
                        "error"
                    )
                    return

                if topic["owner_page_id"]:
                    # Another generated page already owns this keyword
                    self.log(
                        f"BLOCKED '{title}': keyword '{target_keyword}' already owned by page_id={topic['owner_page_id']}. "
                        "One intent, one page.",
                        "error"
                    )
                    return

        # ── Gate 2: Semantic duplicate check ────────────────────────────────
        if content and target_keyword:
            if self._check_semantic_duplicate(target_keyword, content, page_type):
                self.log(
                    f"BLOCKED '{title}': semantic duplicate of existing content "
                    f"(keyword='{target_keyword}', type='{page_type}')",
                    "error"
                )
                return

        # ── All gates passed → create ───────────────────────────────────────
        db_write(
            "INSERT INTO pages (tenant_id,title,slug,type,location,target_keyword,"
            "meta_title,meta_description,content,status,notes,origin,managed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.tenant_id, title, slug, page_type, location, target_keyword,
             meta_title, meta_description, content, "draft", notes,
             'generated', 1)
        )

        # Update topic_map with owner_page_id
        if target_keyword:
            new_page = db_execute(
                "SELECT id FROM pages WHERE tenant_id=? AND slug=?",
                (self.tenant_id, slug)
            )
            if new_page:
                # INSERT OR REPLACE — creates entry if bootstrapping, updates if exists
                db_write(
                    """INSERT OR REPLACE INTO topic_map
                       (tenant_id, primary_keyword, owner_page_id, status)
                       VALUES (?, ?, ?, 'owned_generated')""",
                    (self.tenant_id, target_keyword, new_page[0]["id"])
                )

        self.log(f"Page: '{title}'", level="success")

    def _check_semantic_duplicate(self, keyword: str, content: str, page_type: str) -> bool:
        """Check if content is semantically duplicate of existing same-type pages.
        Uses keyword+title overlap as a lightweight semantic check.
        Returns True if a duplicate is detected."""
        import re

        # Get existing same-type pages
        rows = db_execute(
            "SELECT id, title, target_keyword, content FROM pages "
            "WHERE tenant_id=? AND type=? AND content IS NOT NULL AND content != '' "
            "LIMIT 50",
            (self.tenant_id, page_type)
        )
        if not rows:
            return False

        # Normalize the new content
        def _normalize(text: str) -> set:
            if not text:
                return set()
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'[^\w\s]', ' ', text.lower())
            words = {w for w in text.split() if len(w) > 2}
            return words

        new_words = _normalize(f"{keyword} {content[:2000]}")

        for row in rows:
            existing_words = _normalize(f"{row['target_keyword'] or ''} {(row['content'] or '')[:2000]}")
            if not existing_words or not new_words:
                continue

            # Jaccard similarity
            intersection = new_words & existing_words
            union = new_words | existing_words
            similarity = len(intersection) / len(union) if union else 0

            if similarity >= 0.85:
                return True

        return False

    def add_competitor(self, name: str, url=None, location=None,
                       strengths=None, weaknesses=None, notes=None):
        existing = db_execute(
            "SELECT id FROM competitors WHERE tenant_id=? AND name=?",
            (self.tenant_id, name)
        )
        if not existing:
            db_write(
                "INSERT INTO competitors (tenant_id,name,url,location,strengths,weaknesses,notes) "
                "VALUES (?,?,?,?,?,?,?)",
                (self.tenant_id, name, url, location, strengths, weaknesses, notes)
            )

    # ── Claude API ───────────────────────────────────────────────────────────

    def call_claude(self, prompt: str, max_tokens: int = 1500,
                    system: str = None, model: str = "claude-haiku-4-5-20251001",
                    cache_system: bool = False) -> str:
        import anthropic
        if system is None:
            system = SEO_EXPERTISE
        global _last_api_call

        client = _get_anthropic_client()
        messages = [{"role": "user", "content": prompt}]
        kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)

        # Prompt caching: when cache_system=True, wrap the system prompt with
        # cache_control so Anthropic caches it. Subsequent calls with the same
        # system prompt get ~90% cost reduction on the cached portion.
        # Minimum 1024 tokens required for Claude 4 cacheable blocks.
        if cache_system and system:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif system:
            kwargs["system"] = system

        for attempt in range(4):
            retry_wait = 0
            with _api_semaphore:
                with _api_lock:
                    elapsed = time.time() - _last_api_call
                    if elapsed < 2:
                        time.sleep(2 - elapsed)
                    _last_api_call = time.time()
                try:
                    response = client.messages.create(**kwargs)
                    for block in response.content:
                        if hasattr(block, 'text'):
                            return block.text
                    return ""
                except anthropic.RateLimitError:
                    retry_wait = 30 * (attempt + 1)
                    self.log(f"Rate limit — waiting {retry_wait}s...", "warning")
                except anthropic.APITimeoutError:
                    retry_wait = 10 * (attempt + 1)
                    self.log(f"Timeout — retry {attempt+1}/4", "warning")
                except Exception as e:
                    if attempt < 3:
                        retry_wait = 8
                    else:
                        self.log(f"API error: {str(e)[:80]}", "error")
                        return ""
            if retry_wait:
                time.sleep(retry_wait)
        return ""

    def build_cacheable_system(self, extra: str = "") -> str:
        """Build a cacheable system prompt combining SEO_EXPERTISE + ClientContext.
        Total is typically 1500-2500 tokens — well above the 1024 minimum for caching.
        Use with call_claude(..., cache_system=True)."""
        base = SEO_EXPERTISE + "\n\n" + self.ctx.to_prompt_block()
        if extra:
            base += "\n\n" + extra
        return base

    def call_claude_vision(self, image_url: str, prompt: str, max_tokens: int = 500) -> str:
        """Send an image URL to Claude Vision and return the text response."""
        import anthropic
        import base64
        import requests as _req

        global _last_api_call

        try:
            r = _req.get(image_url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            image_data = base64.b64encode(r.content).decode("utf-8")
            content_type = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            ct_map = {"image/jpg": "image/jpeg", "image/svg+xml": "image/png"}
            media_type = ct_map.get(content_type, content_type)
            if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                media_type = "image/jpeg"
        except Exception as e:
            return f"__download_error__: {e}"

        client = _get_anthropic_client()
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    }
                },
                {"type": "text", "text": prompt}
            ]
        }]

        for attempt in range(3):
            retry_wait = 0
            with _api_semaphore:
                with _api_lock:
                    elapsed = time.time() - _last_api_call
                    if elapsed < 2:
                        time.sleep(2 - elapsed)
                    _last_api_call = time.time()
                try:
                    resp = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=max_tokens,
                        messages=messages
                    )
                    for block in resp.content:
                        if hasattr(block, 'text'):
                            return block.text
                    return ""
                except anthropic.RateLimitError:
                    retry_wait = 30 * (attempt + 1)
                except Exception as e:
                    if attempt < 2:
                        retry_wait = 8
                    else:
                        return f"__vision_error__: {str(e)[:60]}"
            if retry_wait:
                time.sleep(retry_wait)
        return ""

    def run(self):
        raise NotImplementedError("Each agent must implement run()")
