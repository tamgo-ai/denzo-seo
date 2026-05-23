"""
One-shot data cleanup: fixes Spanish contamination, markdown in content,
orphaned clients, and missing quality scores across ALL tenants.
Idempotent — safe to run multiple times.
"""
import json, re, sys
sys.path.insert(0, '/root/denzo-seo')

from denzo.agents.base_agent import db_execute, db_write

# ── Category/intent/type translation maps ────────────────────────────────────

CAT_MAP = {
    "lujo": "luxury", "servicio": "service", "seguro": "insurance",
    "seguros": "insurance", "marca": "brand", "ubicacion": "location",
    "ubicación": "location", "comparacion": "comparison",
    "comparación": "comparison", "pregunta": "question",
    "competidor": "competitor_gap", "local": "location",
}

INTENT_MAP = {
    "transaccional": "transactional", "informacional": "informational",
    "navegacional": "navigational", "comercial": "commercial",
    "urgencia": "commercial", "emergencia": "commercial",
    "compra": "transactional", "local+marca": "commercial",
    "local+diferenciador": "commercial",
}

PAGE_TYPE_MAP = {
    "lujo": "luxury", "servicio": "service", "seguro": "insurance",
    "ubicacion": "location", "ubicación": "location",
}


def main():
    total_fixes = 0

    # ── 1. Fix keyword categories ────────────────────────────────────────────
    rows = db_execute("SELECT id, category FROM keywords WHERE category IN ({})".format(
        ",".join(f"'{k}'" for k in CAT_MAP.keys())
    ))
    for r in (rows or []):
        new_cat = CAT_MAP.get(r["category"], r["category"])
        db_write("UPDATE keywords SET category=? WHERE id=?", (new_cat, r["id"]))
        total_fixes += 1
    print(f"[keywords] Fixed {len(rows or [])} Spanish categories → English")

    # ── 2. Fix keyword intents ───────────────────────────────────────────────
    rows = db_execute("SELECT id, intent FROM keywords WHERE intent IN ({})".format(
        ",".join(f"'{k}'" for k in INTENT_MAP.keys())
    ))
    for r in (rows or []):
        new_intent = INTENT_MAP.get(r["intent"], r["intent"])
        db_write("UPDATE keywords SET intent=? WHERE id=?", (new_intent, r["id"]))
        total_fixes += 1
    print(f"[keywords] Fixed {len(rows or [])} Spanish intents → English")

    # ── 3. Fix page types ────────────────────────────────────────────────────
    rows = db_execute("SELECT id, type FROM pages WHERE type IN ({})".format(
        ",".join(f"'{k}'" for k in PAGE_TYPE_MAP.keys())
    ))
    for r in (rows or []):
        new_type = PAGE_TYPE_MAP.get(r["type"], r["type"])
        db_write("UPDATE pages SET type=? WHERE id=?", (new_type, r["id"]))
        total_fixes += 1
    print(f"[pages] Fixed {len(rows or [])} Spanish types → English")

    # ── 4. Strip markdown code fences from content ───────────────────────────
    rows = db_execute(
        "SELECT id, content FROM pages WHERE content LIKE '%```%'"
    )
    cleaned = 0
    for r in (rows or []):
        content = r["content"]
        # Remove ```html and ``` blocks
        cleaned_content = re.sub(r'```\w*\n?', '', content)
        cleaned_content = cleaned_content.replace('```', '')
        if cleaned_content != content:
            db_write("UPDATE pages SET content=? WHERE id=?", (cleaned_content, r["id"]))
            cleaned += 1
            total_fixes += 1
    print(f"[pages] Stripped markdown fences from {cleaned} pages")

    # ── 5. Assign quality scores to NULL pages ───────────────────────────────
    rows = db_execute(
        "SELECT id, content FROM pages WHERE content IS NOT NULL AND content != '' AND quality_score IS NULL"
    )
    scored = 0
    for r in (rows or []):
        content = r["content"] or ""
        # Quick heuristic score based on content length and HTML structure
        length = len(content)
        has_h2 = 1 if '<h2' in content else 0
        has_img = 1 if '<img' in content else 0
        has_faq = 1 if 'FAQ' in content or 'faq' in content.lower() else 0
        has_schema = 1 if 'itemprop' in content or 'itemscope' in content else 0
        base = 60
        if length > 5000: base += 5
        if length > 8000: base += 5
        if has_h2: base += 5
        if has_img: base += 5
        if has_faq: base += 5
        if has_schema: base += 5
        score = min(90, base)
        db_write("UPDATE pages SET quality_score=? WHERE id=?", (score, r["id"]))
        scored += 1
        total_fixes += 1
    print(f"[pages] Assigned quality scores to {scored} pages")

    # ── 6. Fix orphaned clients — assign to admin ────────────────────────────
    admin = db_execute("SELECT id FROM users WHERE role='admin' LIMIT 1")
    if admin:
        admin_id = admin[0]["id"]
        rows = db_execute(
            "SELECT tenant_id FROM clients WHERE owner_user_id IS NULL"
        )
        for r in (rows or []):
            db_write(
                "UPDATE clients SET owner_user_id=? WHERE tenant_id=?",
                (admin_id, r["tenant_id"])
            )
            total_fixes += 1
        print(f"[clients] Assigned {len(rows or [])} orphaned clients to admin (id={admin_id})")
    else:
        print("[clients] WARNING: No admin user found — can't fix orphans")

    print(f"\n✓ TOTAL FIXES: {total_fixes}")
    print("✓ Data cleanup complete. Safe to re-run — all operations are idempotent.")


if __name__ == "__main__":
    main()
