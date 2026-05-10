"""
Plan tiers + entitlements.

Each plan defines hard limits that the @requires_plan decorator and the
client-side UI enforce. Stripe price IDs come from env vars so a fresh
machine doesn't have to commit secrets.
"""
import os


# Plan keys are stored in users.plan and subscriptions.plan
PLAN_FREE     = "free"
PLAN_STARTER  = "starter"
PLAN_PRO      = "pro"
PLAN_AGENCY   = "agency"
PLAN_TRIAL    = "trial"

PLAN_ORDER = [PLAN_FREE, PLAN_TRIAL, PLAN_STARTER, PLAN_PRO, PLAN_AGENCY]


PLANS = {
    PLAN_FREE: {
        "name":           "Free",
        "price_monthly":  0,
        "price_id":       None,  # no Stripe — local only
        "max_clients":    1,
        "max_pages":      25,
        "max_keywords":   50,
        "agents_unlocked": ["Keyword Strategist", "Competitor Intel", "E-E-A-T Architect"],
        "gbp_oauth":      False,
        "gsc_oauth":      False,
        "white_label":    False,
        "support":        "community",
        "tagline":        "Try Denzo with one client and 25 pages.",
        "features": [
            "1 client tenant",
            "25 generated pages",
            "50 keyword research entries",
            "3 core agents",
            "AI-only GBP analysis",
        ],
    },
    PLAN_TRIAL: {
        "name":           "14-day Trial (Pro)",
        "price_monthly":  0,
        "price_id":       None,
        "max_clients":    3,
        "max_pages":      500,
        "max_keywords":   2000,
        "agents_unlocked": "all",
        "gbp_oauth":      True,
        "gsc_oauth":      True,
        "white_label":    False,
        "support":        "email",
        "tagline":        "Full Pro for 14 days, no card required.",
        "features": [
            "Everything in Pro",
            "14 days, no card up front",
        ],
    },
    PLAN_STARTER: {
        "name":           "Starter",
        "price_monthly":  149,
        "price_id":       os.getenv("STRIPE_PRICE_ID_STARTER"),
        "max_clients":    1,
        "max_pages":      200,
        "max_keywords":   500,
        "agents_unlocked": "all",
        "gbp_oauth":      True,
        "gsc_oauth":      True,
        "white_label":    False,
        "support":        "email",
        "tagline":        "Solo operators and single-location businesses.",
        "features": [
            "1 client tenant",
            "200 generated pages",
            "500 tracked keywords",
            "All 26 agents",
            "Connect Google Business Profile + Search Console",
            "Email support",
        ],
    },
    PLAN_PRO: {
        "name":           "Pro",
        "price_monthly":  399,
        "price_id":       os.getenv("STRIPE_PRICE_ID_PRO"),
        "max_clients":    5,
        "max_pages":      2500,
        "max_keywords":   5000,
        "agents_unlocked": "all",
        "gbp_oauth":      True,
        "gsc_oauth":      True,
        "white_label":    False,
        "support":        "priority",
        "tagline":        "Multi-location operators and small agencies.",
        "features": [
            "5 client tenants",
            "2,500 generated pages",
            "5,000 tracked keywords",
            "All 26 agents",
            "GBP + GSC OAuth on every tenant",
            "Mission Control with inter-agent flow visualization",
            "Priority email support",
        ],
    },
    PLAN_AGENCY: {
        "name":           "Agency",
        "price_monthly":  1299,
        "price_id":       os.getenv("STRIPE_PRICE_ID_AGENCY"),
        "max_clients":    25,
        "max_pages":      25000,
        "max_keywords":   50000,
        "agents_unlocked": "all",
        "gbp_oauth":      True,
        "gsc_oauth":      True,
        "white_label":    True,
        "support":        "dedicated",
        "tagline":        "Agencies running SEO for many clients in parallel.",
        "features": [
            "25 client tenants",
            "25,000 generated pages",
            "50,000 tracked keywords",
            "White-label dashboard (subdomain + logo + colors)",
            "Dedicated success manager",
            "Slack channel access",
        ],
    },
}


def get_plan(plan_key: str) -> dict:
    return PLANS.get(plan_key, PLANS[PLAN_FREE])


def is_at_least(plan_key: str, minimum: str) -> bool:
    """Returns True if plan_key is >= minimum in the plan ladder."""
    try:
        return PLAN_ORDER.index(plan_key) >= PLAN_ORDER.index(minimum)
    except ValueError:
        return False


def stripe_configured() -> bool:
    """Cheap check used by UI to know whether Stripe is wired."""
    return bool(os.getenv("STRIPE_SECRET_KEY"))


def all_priced_plans() -> list[dict]:
    """Plans the public pricing page should display, in order."""
    out = []
    for key in (PLAN_STARTER, PLAN_PRO, PLAN_AGENCY):
        p = dict(PLANS[key])
        p["key"] = key
        out.append(p)
    return out
