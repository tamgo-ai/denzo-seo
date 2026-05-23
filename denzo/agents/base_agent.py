"""
TenantAwareBaseAgent — the backbone of DENZO-SEO.
Every agent inherits from this. ClientContext carries all client-specific data.
No hardcoded business info anywhere — everything flows from the DB via ClientContext.
"""
import sqlite3, os, time, threading
from dataclasses import dataclass, field
from typing import List
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "denzo.db")

# ── Global API rate limiter — shared across ALL tenants and ALL agents ────────
_api_semaphore = threading.Semaphore(2)
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
    """Return this thread's persistent SQLite connection. Creates it on first use."""
    conn = getattr(_sqlite_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")  # 5s busy wait before raising locked
        conn.row_factory = sqlite3.Row
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
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
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
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
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
        """Persist agent output to settings so downstream agents can read it."""
        import json
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
            "VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.tenant_id, key, json.dumps(data, ensure_ascii=False))
        )

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

    def add_page(self, title: str, slug: str, page_type: str, location=None,
                 target_keyword=None, meta_title=None, meta_description=None,
                 content=None, notes=None):
        existing = db_execute(
            "SELECT id FROM pages WHERE tenant_id=? AND slug=?",
            (self.tenant_id, slug)
        )
        if not existing:
            db_write(
                "INSERT INTO pages (tenant_id,title,slug,type,location,target_keyword,"
                "meta_title,meta_description,content,status,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.tenant_id, title, slug, page_type, location, target_keyword,
                 meta_title, meta_description, content, "draft", notes)
            )
            self.log(f"Page: '{title}'", level="success")

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
                    system: str = None, model: str = "claude-haiku-4-5-20251001") -> str:
        import anthropic
        if system is None:
            system = SEO_EXPERTISE
        global _last_api_call

        client = _get_anthropic_client()
        messages = [{"role": "user", "content": prompt}]
        kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
        if system:
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
