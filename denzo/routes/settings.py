"""
Global platform settings — API keys for GEO Monitor, etc.
Stored in settings table with tenant_id='__global__'
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from denzo.auth import login_required
from denzo.db import get_db

bp = Blueprint("settings", __name__, url_prefix="/settings")

GLOBAL_TENANT = "__global__"


def _get_setting(key: str) -> str:
    db = get_db()
    row = db.execute(
        "SELECT value FROM settings WHERE tenant_id=? AND key=?",
        (GLOBAL_TENANT, key)
    ).fetchone()
    db.close()
    return row["value"] if row else ""


def _set_setting(key: str, value: str):
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
        (GLOBAL_TENANT, key, value)
    )
    db.commit()
    db.close()


def get_global_setting(key: str) -> str:
    """Public helper for other modules to read global settings."""
    return _get_setting(key)


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    saved = False

    ALL_KEYS = [
        "apify_api_key",
        "perplexity_api_key",
        "openai_api_key",
        "gemini_api_key",
        "bing_api_key",
        "newsapi_key",
    ]

    if request.method == "POST":
        for key in ALL_KEYS:
            val = request.form.get(key, "").strip()
            if val and not val.startswith("***"):
                _set_setting(key, val)
        saved = True

    def mask(val):
        if not val:
            return ""
        if len(val) <= 8:
            return "***"
        return val[:4] + "***" + val[-4:]

    vals = {k: _get_setting(k) for k in ALL_KEYS}

    return render_template(
        "settings/index.html",
        masked={k: mask(v) for k, v in vals.items()},
        is_set={k: bool(v) for k, v in vals.items()},
        saved=saved,
    )
