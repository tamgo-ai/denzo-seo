"""
build_client_context(tenant_id) — loads DB rows into a ClientContext dataclass.
This is the ONLY place that reads from DB into a ClientContext.
Agents never read DB directly for client config.
"""
import json
from denzo.agents.base_agent import ClientContext, db_execute


def build_client_context(tenant_id: str) -> ClientContext:
    rows = db_execute(
        """SELECT c.name, c.website_url, c.phone, c.address, c.city, c.state,
                  c.publisher_type, c.is_multilocation, c.brand_tier, c.locations_json,
                  cc.tagline, cc.description, cc.service_cities, cc.primary_city,
                  cc.certifications, cc.services, cc.differentiators,
                  cc.competitors, cc.insurance_partners, cc.domain,
                  cc.industry_vertical, cc.github_repo, cc.github_branch,
                  cc.github_token, cc.github_format, cc.github_path_prefix, cc.pages_domain,
                  cc.wp_url, cc.wp_user, cc.wp_app_password, cc.dont_sell
           FROM clients c
           LEFT JOIN client_context cc ON c.tenant_id = cc.tenant_id
           WHERE c.tenant_id = ?""",
        (tenant_id,)
    )
    if not rows:
        raise ValueError(f"No client found: tenant_id='{tenant_id}'")

    r = rows[0]

    def jlist(val):
        try:
            return json.loads(val) if val else []
        except Exception:
            return []

    return ClientContext(
        tenant_id           = tenant_id,
        client_name         = r["name"] or "",
        website_url         = r["website_url"] or "",
        phone               = r["phone"] or "",
        address             = r["address"] or "",
        primary_city        = r["primary_city"] or r["city"] or "",
        state               = r["state"] or "CA",
        tagline             = r["tagline"] or "",
        description         = r["description"] or "",
        service_cities      = jlist(r["service_cities"]),
        certifications      = jlist(r["certifications"]),
        services            = jlist(r["services"]),
        differentiators     = jlist(r["differentiators"]),
        competitors         = jlist(r["competitors"]),
        insurance_partners  = jlist(r["insurance_partners"]),
        domain              = r["domain"] or r["website_url"] or "",
        industry_vertical   = r["industry_vertical"] or "general",
        brand_tier          = r["brand_tier"] or "mid",
        is_multilocation    = bool(r["is_multilocation"]) if r["is_multilocation"] is not None else False,
        locations_json      = r["locations_json"] or "",
        publisher_type      = r["publisher_type"] or "github",
        github_repo         = r["github_repo"] or "",
        github_branch       = r["github_branch"] or "main",
        github_token        = r["github_token"] or "",
        github_format       = r["github_format"] or "html",
        github_path_prefix  = r["github_path_prefix"] or "",
        pages_domain        = r["pages_domain"] or "",
        dont_sell           = jlist(r["dont_sell"]),
        wp_url              = r["wp_url"] or "",
        wp_user             = r["wp_user"] or "",
        wp_app_password     = r["wp_app_password"] or "",
    )
