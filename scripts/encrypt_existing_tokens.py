#!/usr/bin/env python3
"""
Encrypt existing plaintext tokens in the database.

Run once after deploying the encryption changes (Fase 1.2).
Safe to run multiple times — skips already-encrypted tokens.

Usage:
    python3 scripts/encrypt_existing_tokens.py
    python3 scripts/encrypt_existing_tokens.py --dry-run   # preview only

Backup created at data/denzo.db.backup-<timestamp> before any changes.
"""

import os
import shutil
import sys
import time

# Ensure we can import from the project
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from dotenv import load_dotenv

load_dotenv()

from denzo.crypto import encrypt_token, is_encryption_available

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "denzo.db")
DB_PATH = os.path.abspath(DB_PATH)
DRY_RUN = "--dry-run" in sys.argv


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    if not is_encryption_available():
        print("WARNING: DENZO_ENCRYPTION_KEY not configured. Tokens will remain plaintext.")
        if not DRY_RUN:
            yn = input("Continue anyway? [y/N] ")
            if yn.lower() != "y":
                print("Aborted.")
                sys.exit(0)

    # Backup
    if not DRY_RUN:
        backup_path = f"{DB_PATH}.backup-{int(time.time())}"
        print(f"Creating backup: {backup_path}")
        shutil.copy2(DB_PATH, backup_path)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── client_context: github_token + wp_app_password ──────────────────────────
    rows = cur.execute(
        "SELECT id, tenant_id, github_token, wp_app_password, encrypted "
        "FROM client_context"
    ).fetchall()

    cc_updates = 0
    for row in rows:
        gh = row["github_token"]
        wp = row["wp_app_password"]
        encrypted = bool(row["encrypted"])

        needs_update = False
        new_gh, new_wp = gh, wp

        if gh and not gh.startswith("gAAAAAB") and not encrypted:
            new_gh = encrypt_token(gh)
            needs_update = True
        if wp and not wp.startswith("gAAAAAB") and not encrypted:
            new_wp = encrypt_token(wp)
            needs_update = True

        if needs_update:
            if DRY_RUN:
                print(f"  [DRY RUN] {row['tenant_id']}: encrypt github_token={bool(gh)}, wp_app_password={bool(wp)}")
            else:
                cur.execute(
                    "UPDATE client_context SET github_token=?, wp_app_password=?, encrypted=1 WHERE id=?",
                    (new_gh, new_wp, row["id"]),
                )
            cc_updates += 1

    print(f"client_context: {cc_updates} rows to encrypt ({len(rows)} total)")

    # ── oauth_tokens: access_token + refresh_token ──────────────────────────────
    oa_rows = cur.execute(
        "SELECT id, tenant_id, provider, access_token, refresh_token, encrypted "
        "FROM oauth_tokens"
    ).fetchall()

    oa_updates = 0
    for row in oa_rows:
        at_token = row["access_token"]
        rt_token = row["refresh_token"]
        encrypted = bool(row["encrypted"])

        needs_update = False
        new_at, new_rt = at_token, rt_token

        if at_token and not at_token.startswith("gAAAAAB") and not encrypted:
            new_at = encrypt_token(at_token)
            needs_update = True
        if rt_token and not rt_token.startswith("gAAAAAB") and not encrypted:
            new_rt = encrypt_token(rt_token)
            needs_update = True

        if needs_update:
            if DRY_RUN:
                print(f"  [DRY RUN] {row['tenant_id']}/{row['provider']}: encrypt access_token, refresh_token={bool(rt_token)}")
            else:
                cur.execute(
                    "UPDATE oauth_tokens SET access_token=?, refresh_token=?, encrypted=1 WHERE id=?",
                    (new_at, new_rt, row["id"]),
                )
            oa_updates += 1

    print(f"oauth_tokens: {oa_updates} rows to encrypt ({len(oa_rows)} total)")

    if not DRY_RUN:
        conn.commit()
        print(f"\nDone. {cc_updates + oa_updates} token(s) encrypted.")
    else:
        print(f"\n[DRY RUN] No changes made. {cc_updates + oa_updates} token(s) would be encrypted.")
        print("Remove --dry-run to apply.")

    conn.close()


if __name__ == "__main__":
    main()
