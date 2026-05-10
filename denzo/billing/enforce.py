"""
Plan enforcement — decorator + helpers to gate features by subscription plan.

Usage in a route:

    @bp.route("/whatever")
    @login_required
    @requires_plan("pro")
    def my_view():
        ...

The decorator checks the user's effective plan (free/trial/starter/pro/agency)
and either lets the view through, returns a JSON 402 (for /api/* paths), or
redirects to /upgrade with a flash explaining what they need.
"""
from functools import wraps

from flask import session, redirect, url_for, flash, request, jsonify

from denzo.db import get_db
from denzo.billing.plans import PLANS, PLAN_FREE, is_at_least, get_plan


def get_user_plan(user_id: int | None = None) -> str:
    """Resolve the effective plan for the logged-in user.

    Sources, in priority order:
      1. subscriptions.plan (set by Stripe webhook on payment success).
      2. users.plan (legacy column — used for trial flag mostly).
      3. 'free' as final fallback.
    """
    if user_id is None:
        user_id = session.get("user_id")
    if not user_id:
        return PLAN_FREE

    db = get_db()
    sub = db.execute(
        "SELECT plan, status FROM subscriptions WHERE user_id=?", (user_id,)
    ).fetchone()
    if sub and sub["status"] in ("active", "trialing"):
        db.close()
        return sub["plan"]

    user = db.execute(
        "SELECT plan FROM users WHERE id=?", (user_id,)
    ).fetchone()
    db.close()
    if user and user["plan"]:
        return user["plan"]
    return PLAN_FREE


def get_user_entitlements(user_id: int | None = None) -> dict:
    """Resolve the plan + its limits for templates/JSON."""
    plan_key = get_user_plan(user_id)
    plan     = get_plan(plan_key)
    return {
        "plan":          plan_key,
        "plan_name":     plan["name"],
        "max_clients":   plan["max_clients"],
        "max_pages":     plan["max_pages"],
        "max_keywords":  plan["max_keywords"],
        "gbp_oauth":     plan["gbp_oauth"],
        "gsc_oauth":     plan["gsc_oauth"],
        "white_label":   plan["white_label"],
    }


def requires_plan(minimum: str):
    """Decorator factory — gate a view behind a minimum plan tier."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user_id = session.get("user_id")
            if not user_id:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Unauthorized"}), 401
                return redirect(url_for("auth.login", next=request.path))

            current = get_user_plan(user_id)
            if not is_at_least(current, minimum):
                if request.path.startswith("/api/"):
                    return jsonify({
                        "error": "plan_required",
                        "current_plan":  current,
                        "required_plan": minimum,
                        "upgrade_url":   "/upgrade",
                    }), 402
                flash(
                    f"This feature requires the {get_plan(minimum)['name']} plan or higher. "
                    f"You're on {get_plan(current)['name']}.",
                    "warning",
                )
                return redirect(url_for("public.upgrade_page"))
            return f(*args, **kwargs)
        return wrapper
    return decorator


def has_feature(user_id: int, feature: str) -> bool:
    """Boolean helper — returns True if the user's plan includes feature.

    Known features: 'gbp_oauth', 'gsc_oauth', 'white_label'.
    """
    plan = get_plan(get_user_plan(user_id))
    return bool(plan.get(feature))
