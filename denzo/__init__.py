import os
from flask import Flask, request
from flask_sock import Sock
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

sock = Sock()


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="../static")
    app.secret_key = os.getenv("SECRET_KEY", "denzo-dev-secret")

    sock.init_app(app)

    from denzo.db import init_db

    @app.context_processor
    def inject_sidebar_clients():
        """Always inject clients list into every template for the sidebar."""
        try:
            from denzo.db import get_db
            db = get_db()
            rows = db.execute("""
                SELECT c.tenant_id, c.name, ag.name AS active_agent_name
                FROM clients c
                LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working'
                GROUP BY c.tenant_id
                ORDER BY c.name
            """).fetchall()
            clients = [
                {"tenant_id": r["tenant_id"], "name": r["name"], "active_agent": r["active_agent_name"]}
                for r in rows
            ]
        except Exception:
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

    for bp in [public_bp, auth_bp, dash_bp, clients_bp, pipeline_bp, keywords_bp, pages_bp, competitors_bp, settings_bp, api_bp, audit_bp, images_bp, brand_voice_bp, data_intel_bp, geo_bp, reviews_bp, lite_bp]:
        app.register_blueprint(bp)

    with app.app_context():
        init_db()
        _reset_stale_agents()

    return app


def _reset_stale_agents():
    """
    On startup, any agent left in 'working' status is orphaned (server restarted
    while it was running). Reset them to 'idle' so they can be relaunched.
    """
    try:
        from denzo.db import get_db
        db = get_db()
        result = db.execute(
            "UPDATE agents SET status='idle', current_task=NULL, last_message=NULL "
            "WHERE status='working'"
        )
        if result.rowcount:
            db.execute(
                "INSERT INTO activity (tenant_id, type, message, agent, level) "
                "SELECT tenant_id, 'system', "
                "'Agent reset to idle after server restart (was stuck in working).', "
                "name, 'warning' FROM agents WHERE status='idle' AND last_run_at IS NOT NULL "
                "LIMIT 0"  # no-op — just commit the update above
            )
        db.commit()
        db.close()
    except Exception:
        pass
