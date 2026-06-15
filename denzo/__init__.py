import os
from flask import Flask, request
from flask_sock import Sock
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

sock = Sock()


def create_app():
    # ── Structured logging (replaces print() across all agents) ────────────
    from denzo.logging_config import setup_logging
    setup_logging()

    app = Flask(__name__, template_folder="templates", static_folder="../static")
    _secret = os.getenv("SECRET_KEY")
    if not _secret:
        raise RuntimeError(
            "SECRET_KEY env var not set. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    if len(_secret) < 32:
        raise RuntimeError("SECRET_KEY must be at least 32 characters long.")
    app.secret_key = _secret

    # Session security
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = 86400  # 24h

    sock.init_app(app)

    from denzo.db import init_db

    @app.before_request
    def _refresh_session_plan():
        """Keep session plan in sync with DB on every request."""
        from flask import session as _sess
        if "user_id" in _sess and request.endpoint not in ("static", "api.ws_logs"):
            try:
                from denzo.billing.enforce import get_user_plan
                _sess["plan"] = get_user_plan(_sess["user_id"])
            except Exception:
                pass

    @app.context_processor
    def inject_sidebar_clients():
        """Inject clients list into every template for the sidebar.
        Admins see all clients; client users see only their own."""
        try:
            from flask import session
            from denzo.db import get_db
            db = get_db()
            user_id = session.get("user_id")
            role = session.get("role", "client")
            if role == "admin":
                rows = db.execute("""
                    SELECT c.tenant_id, c.name, ag.name AS active_agent_name
                    FROM clients c
                    LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working'
                    GROUP BY c.tenant_id
                    ORDER BY c.name
                """).fetchall()
            else:
                rows = db.execute("""
                    SELECT c.tenant_id, c.name, ag.name AS active_agent_name
                    FROM clients c
                    LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working'
                    WHERE c.owner_user_id = ?
                    GROUP BY c.tenant_id
                    ORDER BY c.name
                """, (user_id,)).fetchall()
            clients = [
                {"tenant_id": r["tenant_id"], "name": r["name"], "active_agent": r["active_agent_name"]}
                for r in rows
            ]
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("sidebar clients error: %s", e)
            clients = []
        # Detect active_tenant from URL path: /clients/<tenant_id>/...
        active_tenant = None
        path_parts = request.path.split("/")
        if len(path_parts) >= 3 and path_parts[1] == "clients" and path_parts[2] not in ("", "new"):
            active_tenant = path_parts[2]
        return dict(clients=clients, active_tenant=active_tenant)


    from denzo.routes.auth        import bp as auth_bp
    from denzo.routes.dashboard   import bp as dash_bp
    from denzo.routes.clients     import bp as clients_bp
    from denzo.routes.pipeline    import bp as pipeline_bp
    from denzo.routes.keywords    import bp as keywords_bp
    from denzo.routes.pages       import bp as pages_bp
    from denzo.routes.competitors import bp as competitors_bp
    from denzo.routes.settings    import bp as settings_bp
    from denzo.routes.api         import bp as api_bp
    from denzo.routes.audit       import bp as audit_bp
    from denzo.routes.images      import bp as images_bp
    from denzo.routes.brand_voice import bp as brand_voice_bp
    from denzo.routes.data_intel  import bp as data_intel_bp
    from denzo.routes.geo         import bp as geo_bp
    from denzo.routes.reviews     import bp as reviews_bp
    from denzo.routes.lite        import bp as lite_bp
    from denzo.routes.public      import bp as public_bp
    from denzo.routes.reporting   import bp as reporting_bp
    from denzo.routes.oauth       import bp as oauth_bp
    from denzo.routes.mission_control import bp as mission_control_bp
    from denzo.routes.billing     import bp as billing_bp
    from denzo.routes.jarvis      import bp as jarvis_bp

    for bp in [public_bp, auth_bp, dash_bp, clients_bp, pipeline_bp, keywords_bp, pages_bp, competitors_bp, settings_bp, api_bp, audit_bp, images_bp, brand_voice_bp, data_intel_bp, geo_bp, reviews_bp, lite_bp, reporting_bp, oauth_bp, mission_control_bp, billing_bp, jarvis_bp]:
        app.register_blueprint(bp)

    # ── Rate limiting ─────────────────────────────────────────────────────────
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    # Always try Redis first (production), fall back to memory (dev)
    _redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/1")
    _limiter_storage = "memory://"
    try:
        import redis as _redis_check
        _r = _redis_check.Redis.from_url(_redis_url, socket_connect_timeout=1)
        _r.ping()
        _r.close()
        _limiter_storage = _redis_url
        print(f"[DENZO] Rate limiter: Redis ({_redis_url})")
    except Exception:
        print("[DENZO] Rate limiter: memory:// (Redis not available — install redis-server for production)")

    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["200 per day", "60 per hour"],
        storage_uri=_limiter_storage,
    )
    # Stricter limits on auth endpoints
    limiter.limit("10 per minute")(app.view_functions.get("auth.login"))
    # Anti-abuse: wizard account creation limit
    limiter.limit("3 per hour")(app.view_functions.get("public.wizard_complete"))
    # Also rate-limit wizard start (the AI analysis call)
    limiter.limit("5 per hour")(app.view_functions.get("public.wizard_start"))
    app.config["LIMITER"] = limiter

    # ── Custom error handlers ──────────────────────────────────────────────────
    from flask import render_template as _render

    @app.errorhandler(403)
    def _forbidden(e):
        return _render("errors/403.html"), 403

    @app.errorhandler(404)
    def _not_found(e):
        return _render("errors/404.html"), 404

    @app.errorhandler(500)
    def _server_error(e):
        return _render("errors/500.html"), 500

    with app.app_context():
        init_db()
        _reset_stale_agents()

    return app


def _reset_stale_agents():
    """Reset agents stuck in 'working' for >15 minutes from a previous server crash.
    Only resets agents that have been in 'working' state for a significant time,
    avoiding killing agents that were just started during a quick restart."""
    import logging
    try:
        from denzo.db import get_db
        db = get_db()
        # Only reset agents stuck in 'working' for more than 15 minutes
        # This avoids killing agents that were legitimately started just before a restart
        stale = db.execute(
            "SELECT tenant_id, name FROM agents WHERE status='working' "
            "AND updated_at < datetime('now', '-15 minutes')"
        ).fetchall()
        if stale:
            for row in stale:
                db.execute(
                    "UPDATE agents SET status='idle', current_task='Reset after server restart', "
                    "last_message=NULL WHERE tenant_id=? AND name=?",
                    (row["tenant_id"], row["name"])
                )
                db.execute(
                    "INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
                    (row["tenant_id"], "system",
                     "Agent reset to idle after server restart (was stuck in working >15 min).",
                     row["name"], "warning")
                )
            db.commit()
            logging.getLogger(__name__).warning("Reset %d stale agents (working >15 min)", len(stale))
        else:
            # Quick check: agents that are 'working' but <15 min — leave them alone
            recent = db.execute(
                "SELECT COUNT(*) AS n FROM agents WHERE status='working' "
                "AND updated_at >= datetime('now', '-15 minutes')"
            ).fetchone()
            if recent and recent["n"] > 0:
                logging.getLogger(__name__).info(
                    "%d agent(s) in 'working' state (<15 min) — not resetting (may be legit)", recent["n"]
                )
        db.close()
    except Exception as e:
        logging.getLogger(__name__).error("stale agent reset error: %s", e)
