"""
migrate_noho.py — Migrate NoHo Collision Center into DENZO-SEO as a tenant.

Reads: /root/noho-collision-center/seo-dashboard/seo.db
Writes: /root/denzo-seo/data/denzo.db

Run: python3 /root/denzo-seo/scripts/migrate_noho.py
"""
import json
import sqlite3
import sys
import os

# Ensure denzo package is importable regardless of working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

NOHO_DB   = "/root/noho-collision-center/seo-dashboard/seo.db"
DENZO_DB  = "/root/denzo-seo/data/denzo.db"
TENANT_ID = "noho-collision-center"

# ── NoHo business constants ───────────────────────────────────────────────────
CLIENT_NAME    = "NoHo Collision Center"
WEBSITE_URL    = "https://www.nohocollisioncenter.com"
PHONE          = "818-821-0425"
ADDRESS        = "12525 Sherman Way, North Hollywood, CA 91605"
PRIMARY_CITY   = "North Hollywood"
STATE          = "CA"
DOMAIN         = "nohocollisioncenter.com"
PAGES_DOMAIN   = "https://www.nohocollisioncenter.com"
GITHUB_REPO    = "pdx-prog/noho-web"
GITHUB_BRANCH  = "Tamgo-ai"
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")
GITHUB_FORMAT  = "nextjs"
INDUSTRY       = "auto_body_shop"

SERVICE_CITIES = [
    "North Hollywood", "Burbank", "Studio City", "Sherman Oaks",
    "Van Nuys", "Glendale", "Toluca Lake", "Reseda",
    "Panorama City", "Woodland Hills", "Chatsworth", "Canoga Park"
]
CERTIFICATIONS = [
    "BMW", "Alfa Romeo", "Chrysler", "Dodge", "Fiat", "Genesis",
    "Hyundai", "Jeep", "Kia", "Mazda", "Mopar", "Nissan", "Ram", "SRT"
]
SERVICES = [
    "Auto Body Repair", "Collision Repair", "Paint & Color Matching",
    "Frame Straightening", "Bumper Repair", "Dent Repair (PDR)", "Hail Damage Repair"
]
DIFFERENTIATORS = [
    "Expert Collision Repair You Can Trust",
    "BMW Specialist — Factory Certified",
    "14 Manufacturer Certifications",
    "OEM Parts Exclusively — Never Aftermarket",
    "Lifetime Written Warranty",
    "No Hidden Fees, No Unnecessary Delays",
    "Works With All Insurance Companies",
]
INSURANCE_PARTNERS = [
    "State Farm", "Allstate", "Geico", "Progressive", "Farmers",
    "AAA", "Mercury", "21st Century"
]
TAGLINE = "Expert Collision Repair You Can Trust"

COMPETITORS = [
    {"name": "Caliber Collision", "url": "https://calibercollision.com"},
    {"name": "Service King", "url": "https://serviceking.com"},
    {"name": "Fix Auto North Hollywood", "url": "https://fixauto.com"},
    {"name": "Gerber Collision Glendale", "url": "https://gerbercollision.com"},
]

# ── Next.js assets config ─────────────────────────────────────────────────────
NEXTJS_ASSETS = {
    "primary_color":   "#0b3950",
    "secondary_color": "#ea6018",
    "default_hero":    "/images/home/body.jpg",
    "brand_logo_map": {
        "bmw":        "/marcas/bmw-auto-body-repair-north-hollywood.png",
        "alfa-romeo": "/marcas/ALFAROMEO.png",
        "chrysler":   "/marcas/CHRYSLER.png",
        "dodge":      "/marcas/DODGE.png",
        "fiat":       "/marcas/FIAT.png",
        "genesis":    "/marcas/GENESIS.png",
        "hyundai":    "/marcas/HYUNDAI.png",
        "jeep":       "/marcas/JEEP.png",
        "kia":        "/marcas/KIA.png",
        "mazda":      "/marcas/MAZDA.png",
        "mopar":      "/marcas/MOPAR.png",
        "nissan":     "/marcas/NISSAN.png",
        "ram":        "/marcas/RAM.png",
        "srt":        "/marcas/SRT.png",
    },
    "cert_map": {
        "alfa-romeo": "/certifications/alfaromeo.jpg",
        "chrysler":   "/certifications/chrysler.jpg",
        "dodge":      "/certifications/dodge.jpg",
        "fiat":       "/certifications/fiat.jpg",
        "genesis":    "/certifications/genesis.jpg",
        "hyundai":    "/certifications/hyundai.jpg",
        "jeep":       "/certifications/jeep.jpg",
        "kia":        "/certifications/kia.jpg",
        "mazda":      "/certifications/mazda.jpg",
        "mopar":      "/certifications/mopar.jpg",
        "nissan":     "/certifications/nissan.jpg",
        "ram":        "/certifications/ram.jpg",
    },
    "brand_hero_map": {
        "bmw": "/bmw-autoBodyRepair/NOHO 8SF.png",
    },
    "service_hero_map": {
        "paint-restoration": "/images/home/spray.jpg",
        "spray-painting":    "/images/spray-painting/banner.png",
        "detailing":         "/images/detailing-repair/banner.jpg",
        "body-repair":       "/Heros/hero-body-repair.jpg",
    },
    "stats": [
        {"value": "14",       "label": "Manufacturer Certifications"},
        {"value": "OEM",      "label": "Parts Only — Always"},
        {"value": "Lifetime", "label": "Written Warranty"},
        {"value": "Free",     "label": "Estimates & Inspections"},
    ],
    "trust_items": [
        "All Insurance Accepted",
        "No Hidden Fees",
        "Rental Car Assistance",
        "4.8 ★ Google Rating",
        "Serving San Fernando Valley",
    ],
    "cta_label": "Get Free Estimate",
    "cta_link":  "/contact-us",
    "gallery_default": [
        ["/images/home/body.jpg",  "Auto body repair North Hollywood"],
        ["/images/home/spray.jpg", "Paint restoration North Hollywood"],
        ["/images/home/engine.jpg","Collision repair shop"],
    ],
}


# ── Status mapping: NoHo → DENZO-SEO ─────────────────────────────────────────
STATUS_MAP = {
    "lista":     "ready",      # NoHo uses Spanish status names
    "publicada": "published",
    "borrador":  "draft",
    "draft":     "draft",
    "ready":     "ready",
    "published": "published",
}


def run():
    print(f"=== DENZO-SEO Migration: NoHo Collision Center ===")
    print(f"Source: {NOHO_DB}")
    print(f"Target: {DENZO_DB}")
    print(f"Tenant: {TENANT_ID}")
    print()

    if not os.path.exists(NOHO_DB):
        print(f"ERROR: NoHo DB not found at {NOHO_DB}")
        sys.exit(1)

    noho  = sqlite3.connect(NOHO_DB)
    noho.row_factory = sqlite3.Row
    denzo = sqlite3.connect(DENZO_DB)
    denzo.execute("PRAGMA journal_mode=WAL")
    denzo.execute("PRAGMA synchronous=NORMAL")
    denzo.row_factory = sqlite3.Row

    # ── 1. Check if tenant already exists ────────────────────────────────────
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

    # ── 2. Create client record ───────────────────────────────────────────────
    print("Creating client record...")
    denzo.execute(
        "INSERT INTO clients (tenant_id, name, business_type, website_url, phone, address, "
        "city, state, publisher_type, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (TENANT_ID, CLIENT_NAME, INDUSTRY, WEBSITE_URL, PHONE, ADDRESS,
         PRIMARY_CITY, STATE, "github", "active")
    )

    # ── 3. Create client_context ──────────────────────────────────────────────
    print("Creating client_context...")
    denzo.execute(
        "INSERT INTO client_context (tenant_id, tagline, description, primary_city, "
        "service_cities, certifications, services, differentiators, competitors, "
        "insurance_partners, domain, industry_vertical, github_repo, github_branch, "
        "github_token, github_format, pages_domain) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            TENANT_ID,
            TAGLINE,
            "Certified collision repair center serving North Hollywood and the San Fernando Valley.",
            PRIMARY_CITY,
            json.dumps(SERVICE_CITIES),
            json.dumps(CERTIFICATIONS),
            json.dumps(SERVICES),
            json.dumps(DIFFERENTIATORS),
            json.dumps(COMPETITORS),
            json.dumps(INSURANCE_PARTNERS),
            DOMAIN,
            INDUSTRY,
            GITHUB_REPO,
            GITHUB_BRANCH,
            GITHUB_TOKEN,
            GITHUB_FORMAT,
            PAGES_DOMAIN,
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
    kw_rows = noho.execute(
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
    page_rows = noho.execute(
        "SELECT title, slug, type, location, target_keyword, status, content, "
        "meta_title, meta_description, schema_markup, notes FROM pages"
    ).fetchall()
    pg_saved = 0
    for r in page_rows:
        existing_pg = denzo.execute(
            "SELECT id FROM pages WHERE tenant_id=? AND slug=?",
            (TENANT_ID, r["slug"])
        ).fetchone()
        if not existing_pg:
            mapped_status = STATUS_MAP.get(r["status"] or "draft", "ready")
            denzo.execute(
                "INSERT INTO pages (tenant_id, title, slug, type, location, target_keyword, "
                "status, content, meta_title, meta_description, schema_markup, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    TENANT_ID,
                    r["title"], r["slug"], r["type"], r["location"],
                    r["target_keyword"], mapped_status,
                    r["content"], r["meta_title"], r["meta_description"],
                    r["schema_markup"], r["notes"],
                )
            )
            pg_saved += 1
    print(f"  Pages: {pg_saved}/{len(page_rows)} migrated")

    # ── 7. Migrate competitors ────────────────────────────────────────────────
    print("Migrating competitors...")
    comp_rows = noho.execute(
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

    # ── 8. Save nextjs_assets settings ───────────────────────────────────────
    print("Saving Next.js assets config...")
    denzo.execute(
        "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
        "VALUES (?, 'nextjs_assets', ?, CURRENT_TIMESTAMP)",
        (TENANT_ID, json.dumps(NEXTJS_ASSETS))
    )

    # ── 9. Commit ─────────────────────────────────────────────────────────────
    denzo.commit()
    noho.close()
    denzo.close()

    print()
    print("=== Migration complete ===")
    print(f"Client '{CLIENT_NAME}' created as tenant '{TENANT_ID}'")
    print(f"Keywords: {kw_saved} | Pages: {pg_saved} | Competitors: {comp_saved}")
    print(f"Publisher: GitHub ({GITHUB_FORMAT}) → {GITHUB_REPO}@{GITHUB_BRANCH}")
    print()
    print("Next steps in DENZO-SEO:")
    print("  1. Open http://31.97.142.91:5055")
    print("  2. Select 'NoHo Collision Center'")
    print("  3. Run Content Optimizer → GEO Optimizer → Internal Linker")
    print("  4. Run GitHub Publisher to push to noho-web")


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
