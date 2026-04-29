"""
migrate_acg.py — Migrate Auto Collision Group into DENZO-SEO as a tenant.

Reads: /root/auto-collision-group/seo-dashboard/seo.db
Writes: /root/denzo-seo/data/denzo.db

Run: python3 /root/denzo-seo/scripts/migrate_acg.py
"""
import json
import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ACG_DB    = "/root/auto-collision-group/seo-dashboard/seo.db"
DENZO_DB  = "/root/denzo-seo/data/denzo.db"
TENANT_ID = "auto-collision-group"

# ── ACG business constants ────────────────────────────────────────────────────
CLIENT_NAME   = "Auto Collision Group"
WEBSITE_URL   = "https://www.autoacg.com"
PHONE         = "833-333-4224"
ADDRESS       = "Whittier, CA"
PRIMARY_CITY  = "Whittier"
STATE         = "CA"
DOMAIN        = "autoacg.com"
PAGES_DOMAIN  = "https://www.autoacg.com"
INDUSTRY      = "auto_body_shop"
TAGLINE       = "We Fight For You. Not The Insurance Company."

WP_URL          = "https://www.autoacg.com"
WP_USER         = "Raul"
WP_APP_PASSWORD = "RvjV8N0g0t6AIL5dVjrcGKeW"

SERVICE_CITIES = [
    "Whittier", "Los Angeles", "Gardena", "Santa Ana", "Ontario",
    "Rancho Cucamonga", "Covina", "Victorville", "Fresno",
    "Bakersfield", "El Cajon"
]

CERTIFICATIONS = [
    "Tesla", "BMW", "Maserati", "Mercedes-Benz", "Audi", "Porsche",
    "Lucid", "Jaguar", "Land Rover", "Infiniti", "Acura", "Honda",
    "Toyota", "Nissan", "Hyundai", "Kia", "Ford", "Chevrolet", "GMC",
    "Buick", "Cadillac", "Dodge", "Ram", "Jeep", "Chrysler", "Subaru",
    "Volkswagen", "Volvo", "Mini", "Alfa Romeo", "Fiat", "Fisker",
    "VinFast", "Hummer"
]

SERVICES = [
    "Auto Body Repair", "Collision Repair", "Paint Refinishing",
    "Aluminum Repair", "Frame Straightening", "Free 24/7 Towing",
    "Online Estimates", "Rental Car Assistance", "Financing Options"
]

DIFFERENTIATORS = [
    "We Fight For You. Not The Insurance Company.",
    "34+ Manufacturer Certifications — Largest in California",
    "OEM Parts Exclusively — Never Aftermarket",
    "Free 24/7 Towing Statewide",
    "Lifetime Written Warranty",
    "13 Locations Across California",
    "Luxury Vehicle Specialists: Tesla, BMW, Maserati, Porsche",
    "4.8★ Google Rating — 127+ Reviews",
    "Free Online Estimates",
    "Works With All Insurance Companies",
]

INSURANCE_PARTNERS = [
    "State Farm", "Geico", "Allstate", "Progressive", "Farmers",
    "USAA", "AAA", "Mercury", "21st Century", "Nationwide"
]

COMPETITORS = [
    {"name": "Caliber Collision", "url": "https://calibercollision.com"},
    {"name": "Gerber Collision", "url": "https://gerbercollision.com"},
    {"name": "Service King", "url": "https://serviceking.com"},
    {"name": "Fix Auto", "url": "https://fixauto.com"},
    {"name": "Hendrick Collision", "url": "https://hendrickcollision.com"},
]

# ── Status + type mappings: ACG Spanish → DENZO-SEO English ──────────────────
STATUS_MAP = {
    "optimizado":          "ready",
    "needs_review":        "ready",
    "lista":               "ready",
    "pendiente-contenido": "draft",
    "draft":               "draft",
    "ready":               "ready",
    "published":           "published",
}

TYPE_MAP = {
    "lujo":       "brand_city",
    "servicio":   "service",
    "blog":       "blog",
    "ubicación":  "location",
    "ubicacion":  "location",
    "seguro":     "insurance",
    "educativo":  "blog",
}


def run():
    print("=== DENZO-SEO Migration: Auto Collision Group ===")
    print(f"Source: {ACG_DB}")
    print(f"Target: {DENZO_DB}")
    print(f"Tenant: {TENANT_ID}")
    print()

    if not os.path.exists(ACG_DB):
        print(f"ERROR: ACG DB not found at {ACG_DB}")
        sys.exit(1)

    acg   = sqlite3.connect(ACG_DB)
    acg.row_factory = sqlite3.Row
    denzo = sqlite3.connect(DENZO_DB)
    denzo.execute("PRAGMA journal_mode=WAL")
    denzo.execute("PRAGMA synchronous=NORMAL")
    denzo.row_factory = sqlite3.Row

    # ── 1. Check existing ─────────────────────────────────────────────────────
    existing = denzo.execute(
        "SELECT tenant_id FROM clients WHERE tenant_id=?", (TENANT_ID,)
    ).fetchone()
    if existing:
        print(f"Tenant '{TENANT_ID}' already exists.")
        ans = input("Delete and re-migrate? [y/N] ").strip().lower()
        if ans != "y":
            print("Aborted.")
            sys.exit(0)
        _delete_tenant(denzo, TENANT_ID)

    # ── 2. Create client ──────────────────────────────────────────────────────
    print("Creating client record...")
    denzo.execute(
        "INSERT INTO clients (tenant_id, name, business_type, website_url, phone, address, "
        "city, state, publisher_type, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (TENANT_ID, CLIENT_NAME, INDUSTRY, WEBSITE_URL, PHONE, ADDRESS,
         PRIMARY_CITY, STATE, "wordpress", "active")
    )

    # ── 3. Create client_context ──────────────────────────────────────────────
    print("Creating client_context...")
    denzo.execute(
        "INSERT INTO client_context (tenant_id, tagline, description, primary_city, "
        "service_cities, certifications, services, differentiators, competitors, "
        "insurance_partners, domain, industry_vertical, pages_domain, "
        "wp_url, wp_user, wp_app_password) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            TENANT_ID,
            TAGLINE,
            "Multi-location certified collision repair group serving all of California. "
            "Advocates for customers, uses OEM parts exclusively, 34+ manufacturer certifications.",
            PRIMARY_CITY,
            json.dumps(SERVICE_CITIES),
            json.dumps(CERTIFICATIONS),
            json.dumps(SERVICES),
            json.dumps(DIFFERENTIATORS),
            json.dumps(COMPETITORS),
            json.dumps(INSURANCE_PARTNERS),
            DOMAIN,
            INDUSTRY,
            PAGES_DOMAIN,
            WP_URL,
            WP_USER,
            WP_APP_PASSWORD,
        )
    )

    # ── 4. Seed agents ────────────────────────────────────────────────────────
    print("Seeding agents...")
    from denzo.agents.registry import AGENT_REGISTRY
    for agent_name, (_, _, layer, color) in AGENT_REGISTRY.items():
        denzo.execute(
            "INSERT OR IGNORE INTO agents (tenant_id, name, layer, color, status) VALUES (?,?,?,?,?)",
            (TENANT_ID, agent_name, layer, color, "idle")
        )

    # ── 5. Migrate keywords ───────────────────────────────────────────────────
    print("Migrating keywords...")
    kw_rows = acg.execute(
        "SELECT keyword, volume, difficulty, intent, location, category, priority FROM keywords"
    ).fetchall()
    kw_saved = 0
    for r in kw_rows:
        existing_kw = denzo.execute(
            "SELECT id FROM keywords WHERE tenant_id=? AND keyword=? AND location=?",
            (TENANT_ID, r["keyword"], r["location"] or "")
        ).fetchone()
        if not existing_kw:
            denzo.execute(
                "INSERT INTO keywords (tenant_id, keyword, volume, difficulty, intent, "
                "location, category, priority) VALUES (?,?,?,?,?,?,?,?)",
                (TENANT_ID, r["keyword"], r["volume"], r["difficulty"],
                 r["intent"], r["location"] or "", r["category"], r["priority"])
            )
            kw_saved += 1
    print(f"  Keywords: {kw_saved}/{len(kw_rows)} migrated")

    # ── 6. Migrate pages ──────────────────────────────────────────────────────
    print("Migrating pages...")
    page_rows = acg.execute(
        "SELECT title, slug, type, location, target_keyword, status, content, "
        "meta_title, meta_description, schema_markup, notes, wp_post_id, wp_url FROM pages"
    ).fetchall()
    pg_saved = 0
    pg_published = 0
    for r in page_rows:
        existing_pg = denzo.execute(
            "SELECT id FROM pages WHERE tenant_id=? AND slug=?",
            (TENANT_ID, r["slug"])
        ).fetchone()
        if not existing_pg:
            raw_status   = r["status"] or "draft"
            mapped_status = STATUS_MAP.get(raw_status, "ready")
            raw_type     = r["type"] or "service"
            mapped_type  = TYPE_MAP.get(raw_type, raw_type)

            # If page was published to WP, capture the publish_url
            publish_url = r["wp_url"] or ""
            publish_ref = str(r["wp_post_id"]) if r["wp_post_id"] else ""
            if publish_url:
                mapped_status = "published"
                pg_published += 1

            denzo.execute(
                "INSERT INTO pages (tenant_id, title, slug, type, location, target_keyword, "
                "status, content, meta_title, meta_description, schema_markup, notes, "
                "publish_url, publish_ref) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    TENANT_ID,
                    r["title"], r["slug"], mapped_type, r["location"],
                    r["target_keyword"], mapped_status,
                    r["content"], r["meta_title"], r["meta_description"],
                    r["schema_markup"], r["notes"],
                    publish_url, publish_ref,
                )
            )
            pg_saved += 1
    print(f"  Pages: {pg_saved}/{len(page_rows)} migrated ({pg_published} already published)")

    # ── 7. Migrate competitors ────────────────────────────────────────────────
    print("Migrating competitors...")
    comp_rows = acg.execute(
        "SELECT name, url, location, strengths, weaknesses, notes FROM competitors"
    ).fetchall()
    comp_saved = 0
    for r in comp_rows:
        existing_c = denzo.execute(
            "SELECT id FROM competitors WHERE tenant_id=? AND name=?",
            (TENANT_ID, r["name"])
        ).fetchone()
        if not existing_c:
            denzo.execute(
                "INSERT INTO competitors (tenant_id, name, url, location, strengths, weaknesses, notes) "
                "VALUES (?,?,?,?,?,?,?)",
                (TENANT_ID, r["name"], r["url"], r["location"],
                 r["strengths"], r["weaknesses"], r["notes"])
            )
            comp_saved += 1
    print(f"  Competitors: {comp_saved}/{len(comp_rows)} migrated")

    # ── 8. Commit ─────────────────────────────────────────────────────────────
    denzo.commit()
    acg.close()
    denzo.close()

    print()
    print("=== Migration complete ===")
    print(f"Client '{CLIENT_NAME}' created as tenant '{TENANT_ID}'")
    print(f"Keywords: {kw_saved} | Pages: {pg_saved} | Competitors: {comp_saved}")
    print(f"Publisher: WordPress → {WP_URL}")
    print()
    print("Next steps in DENZO-SEO:")
    print("  1. Open http://31.97.142.91:5055")
    print("  2. Select 'Auto Collision Group'")
    print("  3. Run Content Optimizer → GEO Optimizer → Internal Linker")
    print("  4. Run WordPress Publisher to sync to autoacg.com")


def _delete_tenant(denzo, tenant_id):
    print(f"Deleting existing tenant '{tenant_id}'...")
    for table in ["keywords", "pages", "competitors", "agents", "activity",
                  "settings", "geo_queries", "client_context"]:
        denzo.execute(f"DELETE FROM {table} WHERE tenant_id=?", (tenant_id,))
    denzo.execute("DELETE FROM clients WHERE tenant_id=?", (tenant_id,))
    denzo.commit()
    print("Deleted.")


if __name__ == "__main__":
    run()
