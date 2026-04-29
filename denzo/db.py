"""
Database layer — SQLite with WAL mode, multi-tenant by tenant_id.
All tables include tenant_id. No data leaks between clients.
"""
import sqlite3, os, time
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "denzo.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    -- ── AUTH ──────────────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role          TEXT DEFAULT 'admin',
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- ── CLIENTS (tenants) ─────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS clients (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id        TEXT UNIQUE NOT NULL,
        name             TEXT NOT NULL,
        business_type    TEXT NOT NULL DEFAULT 'general',
        website_url      TEXT,
        phone            TEXT,
        address          TEXT,
        city             TEXT,
        state            TEXT DEFAULT 'CA',
        logo_url         TEXT,
        publisher_type   TEXT DEFAULT 'github',
        status           TEXT DEFAULT 'active',
        is_multilocation BOOLEAN DEFAULT 0,
        brand_tier       TEXT DEFAULT 'mid',
        locations_json   TEXT,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- ── CLIENT CONTEXT ────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS client_context (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id          TEXT UNIQUE NOT NULL,
        tagline            TEXT,
        description        TEXT,
        service_cities     TEXT DEFAULT '[]',
        primary_city       TEXT,
        certifications     TEXT DEFAULT '[]',
        services           TEXT DEFAULT '[]',
        differentiators    TEXT DEFAULT '[]',
        competitors        TEXT DEFAULT '[]',
        insurance_partners TEXT DEFAULT '[]',
        domain             TEXT,
        industry_vertical  TEXT DEFAULT 'general',
        github_repo        TEXT,
        github_branch      TEXT DEFAULT 'main',
        github_token       TEXT,
        github_format      TEXT DEFAULT 'html',
        github_path_prefix TEXT DEFAULT '',
        pages_domain       TEXT DEFAULT '',
        wp_url             TEXT,
        wp_user            TEXT,
        wp_app_password    TEXT,
        dont_sell          TEXT DEFAULT '[]',
        FOREIGN KEY (tenant_id) REFERENCES clients(tenant_id)
    );

    -- ── KEYWORDS ──────────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS keywords (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id  TEXT NOT NULL,
        keyword    TEXT NOT NULL,
        volume     TEXT,
        difficulty TEXT,
        intent     TEXT,
        location   TEXT,
        category   TEXT,
        priority   TEXT DEFAULT 'media',
        status     TEXT DEFAULT 'identified',
        notes      TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, keyword, location)
    );
    CREATE INDEX IF NOT EXISTS idx_kw_tenant    ON keywords(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_kw_priority  ON keywords(tenant_id, priority);
    CREATE INDEX IF NOT EXISTS idx_kw_status    ON keywords(tenant_id, status);

    -- ── PAGES ─────────────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS pages (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id        TEXT NOT NULL,
        title            TEXT NOT NULL,
        slug             TEXT,
        type             TEXT,
        location         TEXT,
        target_keyword   TEXT,
        status           TEXT DEFAULT 'draft',
        content          TEXT,
        meta_title       TEXT,
        meta_description TEXT,
        schema_markup    TEXT,
        publish_url      TEXT,
        publish_ref      TEXT,
        quality_score    INTEGER,
        visual_score     INTEGER,
        notes            TEXT,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        published_at     TIMESTAMP,
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, slug)
    );
    CREATE INDEX IF NOT EXISTS idx_pages_tenant   ON pages(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_pages_status   ON pages(tenant_id, status);
    CREATE INDEX IF NOT EXISTS idx_pages_quality  ON pages(tenant_id, quality_score);
    CREATE INDEX IF NOT EXISTS idx_pages_visual   ON pages(tenant_id, visual_score);
    CREATE INDEX IF NOT EXISTS idx_pages_type     ON pages(tenant_id, type);

    -- ── COMPETITORS ───────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS competitors (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id           TEXT NOT NULL,
        name                TEXT NOT NULL,
        url                 TEXT,
        location            TEXT,
        top_keywords        TEXT,
        strengths           TEXT,
        weaknesses          TEXT,
        notes               TEXT,
        -- Enhanced competitor intelligence fields
        tier                INTEGER DEFAULT 2,          -- 1=same brand+nearby, 2=other
        certified_brands    TEXT DEFAULT '[]',          -- JSON list of brands they're certified for
        gap_cities          TEXT DEFAULT '[]',          -- JSON list of cities they target we don't
        gap_keywords_json   TEXT DEFAULT '[]',          -- JSON list of keywords they rank for we don't
        competitor_score    REAL DEFAULT 0.0,           -- brand_match*2 + proximity + authority
        discovery_method    TEXT DEFAULT 'manual',      -- 'manual'|'geo_radius'|'serp'
        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_comp_tenant ON competitors(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_comp_tier   ON competitors(tenant_id, tier);

    -- ── CANNIBALIZATION RISKS ─────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS cannibalization_risks (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id     TEXT NOT NULL,
        page_slug_a   TEXT NOT NULL,
        page_title_a  TEXT,
        page_slug_b   TEXT NOT NULL,
        page_title_b  TEXT,
        shared_keyword TEXT,
        risk_level    TEXT DEFAULT 'medium',  -- 'high'|'medium'|'low'
        suggestion    TEXT,
        resolved      INTEGER DEFAULT 0,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, page_slug_a, page_slug_b)
    );
    CREATE INDEX IF NOT EXISTS idx_cann_tenant ON cannibalization_risks(tenant_id);

    -- ── AGENTS (runtime state per client) ─────────────────────────────────────
    CREATE TABLE IF NOT EXISTS agents (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id    TEXT NOT NULL,
        name         TEXT NOT NULL,
        layer        INTEGER DEFAULT 1,
        color        TEXT DEFAULT 'blue',
        status       TEXT DEFAULT 'idle',
        current_task TEXT DEFAULT '',
        next_task    TEXT DEFAULT '',
        last_message TEXT DEFAULT '',
        last_run_at  TIMESTAMP,
        run_count    INTEGER DEFAULT 0,
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, name)
    );
    CREATE INDEX IF NOT EXISTS idx_agents_tenant  ON agents(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_agents_working ON agents(tenant_id, status);

    -- ── ACTIVITY LOG ──────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS activity (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id  TEXT NOT NULL,
        type       TEXT DEFAULT 'agent',
        message    TEXT,
        agent      TEXT DEFAULT '',
        level      TEXT DEFAULT 'info',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_act_tenant ON activity(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_act_id     ON activity(tenant_id, id);

    -- ── SETTINGS ──────────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS settings (
        tenant_id  TEXT NOT NULL,
        key        TEXT NOT NULL,
        value      TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (tenant_id, key)
    );

    -- ── GEO QUERIES ───────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS geo_queries (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id            TEXT NOT NULL,
        query                TEXT,
        ai_model             TEXT,
        response             TEXT,
        client_mentioned     INTEGER DEFAULT 0,
        client_position      INTEGER,
        competitors_mentioned TEXT,
        checked_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_geo_tenant ON geo_queries(tenant_id);

    -- ── GEO QUERY BANK ────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS geo_query_bank (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id   TEXT NOT NULL,
        query       TEXT NOT NULL,
        category    TEXT DEFAULT 'general',  -- branded|service|location|comparison|problem
        active      INTEGER DEFAULT 1,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, query)
    );
    CREATE INDEX IF NOT EXISTS idx_geo_bank_tenant ON geo_query_bank(tenant_id, active);

    -- ── SITE IMAGES ───────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS site_images (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id    TEXT NOT NULL,
        url          TEXT NOT NULL,
        alt          TEXT DEFAULT '',
        width        TEXT DEFAULT '',
        height       TEXT DEFAULT '',
        context      TEXT DEFAULT 'general',
        description  TEXT DEFAULT '',
        tags         TEXT DEFAULT '[]',
        suitable_for TEXT DEFAULT '[]',
        analyzed     INTEGER DEFAULT 0,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, url)
    );
    CREATE INDEX IF NOT EXISTS idx_images_tenant ON site_images(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_images_analyzed ON site_images(tenant_id, analyzed);

    -- ── PIPELINE RUNS ─────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id    TEXT NOT NULL,
        triggered_by TEXT DEFAULT 'manual',
        agents_run   TEXT DEFAULT '[]',
        started_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        status       TEXT DEFAULT 'running'
    );

    -- ── LOCATIONS (multi-location support) ────────────────────────────────────
    CREATE TABLE IF NOT EXISTS locations (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id  TEXT NOT NULL,
        name       TEXT,
        address    TEXT,
        city       TEXT,
        state      TEXT,
        url        TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tenant_id) REFERENCES clients(tenant_id)
    );
    CREATE INDEX IF NOT EXISTS idx_locations_tenant ON locations(tenant_id);
    """)

    # Seed admin from env vars on first install — never hardcode credentials
    admin_user = os.getenv("DENZO_ADMIN_USER")
    admin_pass = os.getenv("DENZO_ADMIN_PASS")
    if admin_user and admin_pass:
        existing = conn.execute("SELECT id FROM users WHERE username=?", (admin_user,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                (admin_user, generate_password_hash(admin_pass), "admin")
            )

    conn.commit()

    # ── Schema migrations — add columns to existing DBs ───────────────────────
    # SQLite does not support IF NOT EXISTS in ALTER TABLE, so we use try/except.
    _migrations = [
        "ALTER TABLE clients ADD COLUMN is_multilocation BOOLEAN DEFAULT 0",
        "ALTER TABLE clients ADD COLUMN brand_tier TEXT DEFAULT 'mid'",
        "ALTER TABLE clients ADD COLUMN locations_json TEXT",
        "ALTER TABLE client_context ADD COLUMN dont_sell TEXT DEFAULT '[]'",
        "ALTER TABLE client_context ADD COLUMN github_format TEXT DEFAULT 'html'",
        "ALTER TABLE client_context ADD COLUMN github_path_prefix TEXT DEFAULT ''",
        "ALTER TABLE client_context ADD COLUMN pages_domain TEXT DEFAULT ''",
        # SaaS funnel — user subscription + client ownership
        "ALTER TABLE users ADD COLUMN email TEXT",
        "ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'",
        "ALTER TABLE users ADD COLUMN trial_ends_at TIMESTAMP",
        "ALTER TABLE clients ADD COLUMN owner_user_id INTEGER",
    ]
    for sql in _migrations:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass  # column already exists — safe to ignore

    conn.close()
    print("✓ DENZO-SEO database initialized")


def slugify(text: str) -> str:
    import re
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)   # strip colons, apostrophes, special chars
    text = re.sub(r"\s+", "-", text)             # spaces → hyphens
    text = re.sub(r"-{2,}", "-", text)           # collapse double hyphens
    return text[:60].strip("-")
