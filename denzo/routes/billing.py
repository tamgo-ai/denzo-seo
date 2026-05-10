"""
Stripe billing blueprint — Checkout, Customer Portal, webhook.

Activated only when STRIPE_SECRET_KEY is set in env. When Stripe is not
configured, the routes still register but return a friendly "billing not
configured" response so links don't 404.

Routes:
  POST /billing/checkout/<plan>    Create a Checkout Session for a logged-in user
  POST /billing/portal             Create a Customer Portal session
  POST /billing/webhook            Stripe webhook endpoint (no auth — verified by signature)
"""
import os
import json
import logging
from datetime import datetime

from flask import Blueprint, request, redirect, url_for, jsonify, session, flash, abort

from denzo.auth import login_required
from denzo.db import get_db
from denzo.billing.plans import PLANS, PLAN_FREE, stripe_configured

logger = logging.getLogger(__name__)
bp = Blueprint("billing", __name__, url_prefix="/billing")


# ── Lazy stripe import — keeps the app boot working without `pip install stripe` ──
_stripe = None
_stripe_configured = False

def _get_stripe():
    """Lazy-load the stripe SDK. Returns None if not installed or unconfigured."""
    global _stripe, _stripe_configured
    if _stripe_configured:
        return _stripe
    _stripe_configured = True

    if not stripe_configured():
        return None
    try:
        import stripe  # type: ignore
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        _stripe = stripe
        return stripe
    except ImportError:
        logger.warning("STRIPE_SECRET_KEY is set but `stripe` package is not installed. Run `pip install stripe`.")
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _success_url() -> str:
    base = os.getenv("BILLING_SUCCESS_URL") or request.url_root.rstrip("/") + "/upgrade/success"
    return base + "?session_id={CHECKOUT_SESSION_ID}"

def _cancel_url() -> str:
    return os.getenv("BILLING_CANCEL_URL") or request.url_root.rstrip("/") + "/upgrade"


def _ensure_subscription_row(user_id: int) -> dict:
    db = get_db()
    row = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        db.execute(
            "INSERT INTO subscriptions (user_id, plan, status) VALUES (?, 'free', 'inactive')",
            (user_id,),
        )
        db.commit()
        row = db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    db.close()
    return dict(row)


def _record_subscription(user_id: int, **fields) -> None:
    if not fields:
        return
    db = get_db()
    _ensure_subscription_row(user_id)
    cols = ", ".join(f"{k}=?" for k in fields.keys())
    cols += ", updated_at=CURRENT_TIMESTAMP"
    values = list(fields.values()) + [user_id]
    db.execute(f"UPDATE subscriptions SET {cols} WHERE user_id=?", tuple(values))
    db.commit()
    db.close()


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/checkout/<plan_key>", methods=["POST"])
@login_required
def checkout(plan_key):
    user_id = session["user_id"]
    plan = PLANS.get(plan_key)
    if not plan:
        flash("Unknown plan.", "error")
        return redirect(url_for("public.upgrade_page"))

    price_id = plan.get("price_id")
    if not price_id:
        flash(
            f"The {plan['name']} plan is not yet configured for purchase. "
            "An admin must set the corresponding STRIPE_PRICE_ID_* in .env.",
            "error",
        )
        return redirect(url_for("public.upgrade_page"))

    stripe = _get_stripe()
    if stripe is None:
        flash(
            "Billing is not configured on this server yet. "
            "Set STRIPE_SECRET_KEY and install the stripe package.",
            "warning",
        )
        return redirect(url_for("public.upgrade_page"))

    db = get_db()
    user = db.execute("SELECT email, username FROM users WHERE id=?", (user_id,)).fetchone()
    sub  = db.execute("SELECT stripe_customer_id FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
    db.close()

    customer_id = (sub or {}).get("stripe_customer_id") if sub else None

    try:
        ckwargs = {
            "mode":                 "subscription",
            "line_items":           [{"price": price_id, "quantity": 1}],
            "success_url":          _success_url(),
            "cancel_url":           _cancel_url(),
            "client_reference_id":  str(user_id),
            "metadata":             {"user_id": str(user_id), "plan": plan_key},
            "subscription_data":    {"metadata": {"user_id": str(user_id), "plan": plan_key}},
            "allow_promotion_codes": True,
        }
        if customer_id:
            ckwargs["customer"] = customer_id
        elif user and user["email"]:
            ckwargs["customer_email"] = user["email"]

        sess = stripe.checkout.Session.create(**ckwargs)
        return redirect(sess.url, code=303)
    except Exception as e:
        logger.exception("Checkout session creation failed")
        flash(f"Could not start checkout: {e}", "error")
        return redirect(url_for("public.upgrade_page"))


@bp.route("/portal", methods=["POST"])
@login_required
def portal():
    user_id = session["user_id"]
    db = get_db()
    sub = db.execute(
        "SELECT stripe_customer_id FROM subscriptions WHERE user_id=?", (user_id,)
    ).fetchone()
    db.close()
    if not sub or not sub["stripe_customer_id"]:
        flash("You do not have a billing account yet.", "info")
        return redirect(url_for("public.upgrade_page"))

    stripe = _get_stripe()
    if stripe is None:
        flash("Billing is not configured on this server.", "warning")
        return redirect(url_for("public.upgrade_page"))

    try:
        sess = stripe.billing_portal.Session.create(
            customer=sub["stripe_customer_id"],
            return_url=request.url_root.rstrip("/") + "/upgrade",
        )
        return redirect(sess.url, code=303)
    except Exception as e:
        logger.exception("Customer portal creation failed")
        flash(f"Could not open billing portal: {e}", "error")
        return redirect(url_for("public.upgrade_page"))


@bp.route("/webhook", methods=["POST"])
def webhook():
    """Stripe webhook — verified by signature. Updates subscriptions table."""
    stripe = _get_stripe()
    if stripe is None:
        return jsonify({"received": False, "error": "stripe not configured"}), 503

    sig    = request.headers.get("Stripe-Signature", "")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    payload = request.get_data(as_text=False)

    try:
        if secret:
            event = stripe.Webhook.construct_event(payload, sig, secret)
        else:
            # Dev mode — accept unverified payloads but warn.
            logger.warning("STRIPE_WEBHOOK_SECRET not set — accepting unverified webhook payload.")
            event = json.loads(payload.decode())
    except Exception as e:
        logger.warning("Webhook signature verification failed: %s", e)
        return jsonify({"error": "bad signature"}), 400

    et = event.get("type") if isinstance(event, dict) else event["type"]
    obj = (event.get("data") if isinstance(event, dict) else event["data"])["object"]

    try:
        if et == "checkout.session.completed":
            user_id = int((obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id") or 0)
            plan    = (obj.get("metadata") or {}).get("plan", "pro")
            customer_id     = obj.get("customer")
            subscription_id = obj.get("subscription")
            if user_id:
                _record_subscription(
                    user_id,
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                    plan=plan,
                    status="active",
                )

        elif et in ("customer.subscription.updated", "customer.subscription.created"):
            user_id = int((obj.get("metadata") or {}).get("user_id") or 0)
            if user_id:
                cancel_at_end = bool(obj.get("cancel_at_period_end"))
                period_end    = obj.get("current_period_end")
                period_end_iso = (
                    datetime.utcfromtimestamp(period_end).isoformat()
                    if period_end else None
                )
                _record_subscription(
                    user_id,
                    stripe_customer_id=obj.get("customer"),
                    stripe_subscription_id=obj.get("id"),
                    plan=(obj.get("metadata") or {}).get("plan", "pro"),
                    status=obj.get("status", "active"),
                    cancel_at_period_end=1 if cancel_at_end else 0,
                    current_period_end=period_end_iso,
                )

        elif et == "customer.subscription.deleted":
            user_id = int((obj.get("metadata") or {}).get("user_id") or 0)
            if user_id:
                _record_subscription(
                    user_id, status="canceled", plan=PLAN_FREE,
                )

        elif et == "invoice.payment_failed":
            user_id = int((obj.get("subscription_details") or {}).get("metadata", {}).get("user_id") or 0)
            if user_id:
                _record_subscription(user_id, status="past_due")

    except Exception as e:
        logger.exception("Webhook handler failed for event %s", et)
        return jsonify({"received": True, "warning": str(e)}), 200

    return jsonify({"received": True, "type": et})
