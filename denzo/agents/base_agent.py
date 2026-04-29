"""
TenantAwareBaseAgent — the backbone of DENZO-SEO.
Every agent inherits from this. ClientContext carries all client-specific data.
No hardcoded business info anywhere — everything flows from the DB via ClientContext.
"""
import sqlite3, os, time, threading, random
from dataclasses import dataclass, field
from typing import List
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "denzo.db")

# Global API rate limiter — shared across ALL tenants and ALL agents
_api_semaphore = threading.Semaphore(2)
_last_api_call = 0.0
_api_lock = threading.Lock()


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
    github_format:      str        = "html"   # "html" | "nextjs"
    github_path_prefix: str        = ""       # e.g. "public/" for Next.js sites
    pages_domain:       str        = ""       # e.g. "https://www.nohocollisioncenter.com"
    wp_url:             str        = ""
    wp_user:            str        = ""
    wp_app_password:    str        = ""
    dont_sell:          List[str]  = field(default_factory=list)  # topics/keywords to NEVER target

    def to_prompt_block(self) -> str:
        """Inject this into every agent prompt — replaces NOHO_CONTEXT."""
        cities    = ", ".join(self.service_cities) or self.primary_city
        certs     = ", ".join(self.certifications) or "N/A"
        svcs      = ", ".join(self.services)       or "General services"
        diffs     = "\n- ".join(self.differentiators)
        ins       = ", ".join(self.insurance_partners) or "All major insurers"
        comp_list = ", ".join(c.get("name", "") for c in self.competitors) or "N/A"

        # Industry-aware labels
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

def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def strip_json_fences(raw: str, start_char: str = "{") -> str:
    """
    Robustly extract a JSON object or array from a Claude response
    that may be wrapped in markdown code fences (```json ... ```).
    Works correctly even when the response has exactly 2 fences — unlike
    the fragile `split('```', 2)[-1]` and `lstrip('```json')` patterns.
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
    for attempt in range(retries):
        try:
            conn = _connect()
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            return rows
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < retries - 1:
                time.sleep(0.3 * (attempt + 1))
            else:
                raise


def db_write(sql: str, params=()):
    for attempt in range(5):
        try:
            conn = _connect()
            conn.execute(sql, params)
            conn.commit()
            conn.close()
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
    All 15 agents inherit from this.
    ctx (ClientContext) carries all client-specific data.
    Every DB operation is scoped to self.tenant_id — no cross-tenant leaks.
    """

    def __init__(self, name: str, ctx: ClientContext, layer: int = 1, color: str = "blue"):
        self.name       = name
        self.ctx        = ctx
        self.tenant_id  = ctx.tenant_id
        self.layer      = layer
        self.color      = color
        self.running    = False
        self._stop      = threading.Event()

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
        # Deterministic cleanup — trim when log exceeds 2000 entries for this tenant
        count_rows = db_execute(
            "SELECT COUNT(*) AS n FROM activity WHERE tenant_id=?", (self.tenant_id,)
        )
        if count_rows and count_rows[0]["n"] > 2000:
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

    # ── Claude API (global rate limiter, shared Anthropic key) ───────────────

    def call_claude(self, prompt: str, max_tokens: int = 1500,
                    system: str = None, model: str = "claude-haiku-4-5-20251001") -> str:
        global _last_api_call
        import anthropic

        client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            timeout=90.0
        )

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
                    return response.content[0].text
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
            # Sleep OUTSIDE the semaphore so other agents can proceed
            if retry_wait:
                time.sleep(retry_wait)
        return ""

    def call_claude_vision(self, image_url: str, prompt: str, max_tokens: int = 500) -> str:
        """
        Send an image URL to Claude Vision and return the text response.
        Downloads the image and sends as base64 — works for any publicly accessible URL.
        """
        import anthropic
        import base64
        import requests as _req

        global _last_api_call

        # Download image
        try:
            r = _req.get(image_url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            image_data = base64.b64encode(r.content).decode("utf-8")
            content_type = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            # Normalize media type
            ct_map = {"image/jpg": "image/jpeg", "image/svg+xml": "image/png"}
            media_type = ct_map.get(content_type, content_type)
            if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                media_type = "image/jpeg"
        except Exception as e:
            return f"__download_error__: {e}"

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""), timeout=60.0)
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
                    return resp.content[0].text
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
