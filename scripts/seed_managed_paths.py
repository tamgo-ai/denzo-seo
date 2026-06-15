#!/usr/bin/env python3
"""
Seed managed_paths from existing published pages.

One-time run after Fase 1 DB migration. Scans all published pages
and registers their paths in managed_paths with managed=1 (ours).

Run: python3 scripts/seed_managed_paths.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "denzo.db")
DB_PATH = os.path.abspath(DB_PATH)


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get all published pages
    pages = cur.execute(
        "SELECT id, tenant_id, slug, type, publish_url, content, origin, managed "
        "FROM pages WHERE status='published'"
    ).fetchall()

    print(f"Found {len(pages)} published pages")

    seeded = 0
    skipped = 0

    for page in pages:
        # Determine publisher type
        tenant_rows = cur.execute(
            "SELECT publisher_type FROM clients WHERE tenant_id=?",
            (page["tenant_id"],)
        ).fetchone()

        publisher = tenant_rows["publisher_type"] if tenant_rows else "github"

        # Determine path from publish_url or slug+type
        path = None
        if page["publish_url"]:
            # Extract path from URL
            from urllib.parse import urlparse
            parsed = urlparse(page["publish_url"])
            path = parsed.path.lstrip("/")
        if not path:
            ptype = page["type"] or "page"
            slug = page["slug"] or ""
            if publisher == "github":
                path = f"{ptype}s/{slug}.html"
            else:
                path = slug

        if not path:
            skipped += 1
            continue

        # Compute content hash
        content = page["content"] or ""
        import hashlib
        import re
        normalized = re.sub(r'\s+', ' ', content.strip())
        content_hash = hashlib.sha256(normalized.encode('utf-8')).hexdigest()

        # Insert into managed_paths
        try:
            cur.execute(
                """INSERT OR REPLACE INTO managed_paths
                   (tenant_id, publisher, path, page_id, managed, content_hash)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (page["tenant_id"], publisher, path, page["id"], content_hash)
            )
            seeded += 1
        except Exception as e:
            print(f"  Error seeding {page['tenant_id']}/{path}: {e}")
            skipped += 1

    conn.commit()
    conn.close()

    print(f"Done: {seeded} paths seeded, {skipped} skipped")


if __name__ == "__main__":
    main()
