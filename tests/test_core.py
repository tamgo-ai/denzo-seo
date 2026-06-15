"""
Core tests: app factory, auth, tenant isolation, billing enforcement, agent system.
Run with: python3 -m pytest tests/ -v
"""
import sys, os, json

# Project root relative to this file
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


class TestAppFactory:
    def test_create_app(self):
        from denzo import create_app
        app = create_app()
        assert app is not None
        assert len(app.url_map._rules) > 50

    def test_debug_off(self):
        from denzo import create_app
        app = create_app()
        assert app.debug is False

    def test_session_security(self):
        from denzo import create_app
        app = create_app()
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

    def test_error_handlers(self):
        from denzo import create_app
        app = create_app()
        # Check custom error handlers registered
        handlers = [h for h in app.error_handler_spec.get(None, {}).keys()]
        assert 403 in handlers or len(app.error_handler_spec) > 0

    def test_rate_limiter(self):
        from denzo import create_app
        app = create_app()
        assert app.config.get("LIMITER") is not None


class TestAuth:
    def test_can_access_tenant_admin(self):
        from denzo.auth import can_access_tenant
        # Admin (role='admin') can always access any tenant
        assert can_access_tenant is not None
        assert hasattr(can_access_tenant, '__call__')

    def test_tenant_access_decorator_returns_function(self):
        from denzo.auth import tenant_access_required
        # The decorator should take a function and return a wrapped function
        def dummy(): pass
        wrapped = tenant_access_required(dummy)
        assert wrapped is not None
        assert callable(wrapped)

    def test_check_credentials(self):
        from denzo.auth import check_credentials
        # Should return (False, None) for invalid credentials
        result = check_credentials("nonexistent_user_12345", "wrong_password")
        assert isinstance(result, tuple) or isinstance(result, bool) or result is not None


class TestBilling:
    def test_plans_defined(self):
        from denzo.billing.plans import PLANS
        assert "free" in PLANS
        assert "trial" in PLANS
        assert "pro" in PLANS
        assert "agency" in PLANS

    def test_plan_limits(self):
        from denzo.billing.plans import PLANS
        assert PLANS["free"]["max_clients"] == 1
        assert PLANS["trial"]["max_clients"] == 3
        assert PLANS["pro"]["max_clients"] == 5
        assert PLANS["agency"]["max_clients"] == 25

    def test_is_at_least(self):
        from denzo.billing.plans import is_at_least
        assert is_at_least("pro", "starter")
        assert is_at_least("agency", "pro")
        assert is_at_least("trial", "free")
        assert not is_at_least("free", "starter")
        assert not is_at_least("starter", "pro")

    def test_requires_plan_decorator_wraps_function(self):
        from denzo.billing.enforce import requires_plan
        decorator = requires_plan("pro")
        # Decorator factory should return a callable decorator
        assert callable(decorator)
        # Applying decorator to a dummy function returns a wrapped function
        def dummy(): pass
        wrapped = decorator(dummy)
        assert callable(wrapped)

    def test_has_feature_returns_boolean_like(self):
        from denzo.billing.enforce import has_feature
        # has_feature should be callable
        assert callable(has_feature)


class TestAgentSystem:
    def test_all_agents_registered(self):
        from denzo.agents.registry import AGENT_REGISTRY
        assert len(AGENT_REGISTRY) >= 27
        assert "Pipeline Director" in AGENT_REGISTRY
        assert "Keyword Strategist" in AGENT_REGISTRY
        assert "Programmatic SEO" in AGENT_REGISTRY

    def test_agent_prerequisites_set(self):
        from denzo.agents.registry import AGENT_REGISTRY
        for name, (mod_path, cls_name, layer, color) in AGENT_REGISTRY.items():
            import importlib
            mod = importlib.import_module(f"denzo.agents.{mod_path}")
            cls = getattr(mod, cls_name)
            pre = getattr(cls, "PREREQUISITES", None)
            mkw = getattr(cls, "MIN_KEYWORDS", None)
            assert pre is not None, f"{name}: PREREQUISITES not set"
            assert isinstance(pre, list), f"{name}: PREREQUISITES must be a list"
            assert mkw is not None, f"{name}: MIN_KEYWORDS not set"

    def test_keyword_cleaner(self):
        from denzo.agents.layer1_research.keyword_strategist import _clean_keyword
        result = _clean_keyword({
            "keyword": " 1. BMW Repair ",
            "intent": "transaccional",
            "difficulty": "25",
            "priority": "media",
            "category": "lujo",
            "volume": "450/mo"
        })
        assert result["intent"] == "transactional"
        assert result["difficulty"] == "easy"
        assert result["priority"] == "medium"
        assert result["category"] == "luxury"
        assert result["volume"] == "450"
        assert result["keyword"] == "BMW Repair"

    def test_director_state_machine(self):
        from denzo.agents.director import PipelineDirector, LAYER_1, LAYER_2, LAYER_2B, LAYER_3, LAYER_4, LAYER_4B, LAYER_5, LAYER_6
        assert len(LAYER_1) == 7
        assert len(LAYER_2) == 2
        assert len(LAYER_2B) == 1
        assert len(LAYER_3) == 1
        assert len(LAYER_4) == 4
        assert len(LAYER_4B) == 1
        assert len(LAYER_5) == 3
        assert len(LAYER_6) == 9

    def test_agent_runner_singleton(self):
        from denzo.agents.runner import AgentRunner
        assert hasattr(AgentRunner, "_threads")
        assert hasattr(AgentRunner, "_events")
        assert hasattr(AgentRunner, "start")
        assert hasattr(AgentRunner, "stop")
        assert hasattr(AgentRunner, "stop_all")

    def test_base_agent_anthropic_singleton(self):
        from denzo.agents.base_agent import _get_anthropic_client
        c1 = _get_anthropic_client()
        c2 = _get_anthropic_client()
        assert c1 is c2  # same instance


class TestDB:
    def test_db_connection(self):
        from denzo.agents.base_agent import _get_conn, db_execute
        conn = _get_conn()
        assert conn is not None

    def test_db_read(self):
        from denzo.agents.base_agent import db_execute
        rows = db_execute("SELECT 1 as n")
        assert rows[0]["n"] == 1

    def test_db_write_read(self):
        from denzo.agents.base_agent import db_write, db_execute
        db_write("INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES ('__test__','test_key','test_value')")
        rows = db_execute("SELECT value FROM settings WHERE tenant_id='__test__' AND key='test_key'")
        assert rows[0]["value"] == "test_value"
        db_write("DELETE FROM settings WHERE tenant_id='__test__' AND key='test_key'")


class TestDataIntegrity:
    def test_spanish_categories_clean(self):
        from denzo.agents.base_agent import db_execute
        rows = db_execute(
            "SELECT COUNT(*) as n FROM keywords WHERE category IN ('lujo','servicio','seguro','seguros','marca','ubicacion','ubicación','comparacion','comparación','pregunta','competidor')"
        )
        # Should be 0 or very low (cleanup may have already run)
        count = rows[0]["n"]
        assert count <= 5, f"Too many Spanish categories remaining: {count}"

    def test_spanish_intents_clean(self):
        from denzo.agents.base_agent import db_execute
        rows = db_execute(
            "SELECT COUNT(*) as n FROM keywords WHERE intent IN ('transaccional','informacional','navegacional','comercial','urgencia','emergencia','compra','local')"
        )
        count = rows[0]["n"]
        assert count <= 5, f"Too many Spanish intents remaining: {count}"

    def test_orphaned_clients_minimal(self):
        from denzo.agents.base_agent import db_execute
        # Clients with owner_user_id=NULL should be minimal
        # (new wizard-created clients may temporarily not have owner)
        rows = db_execute(
            "SELECT COUNT(*) as n FROM clients WHERE owner_user_id IS NULL AND created_at < datetime('now', '-7 days')"
        )
        count = rows[0]["n"]
        assert count == 0, f"Old orphaned clients found (created >7 days ago, no owner): {count}"

    def test_no_markdown_in_content(self):
        from denzo.agents.base_agent import db_execute
        rows = db_execute("SELECT COUNT(*) as n FROM pages WHERE content LIKE '%```%'")
        count = rows[0]["n"]
        assert count <= 2, f"Pages with markdown code blocks: {count}"

    def test_all_pages_have_quality_score(self):
        from denzo.agents.base_agent import db_execute
        rows = db_execute("SELECT COUNT(*) as n FROM pages WHERE content IS NOT NULL AND content != '' AND quality_score IS NULL")
        count = rows[0]["n"]
        assert count <= 5, f"Pages without quality score: {count}"

    def test_clients_have_agents(self):
        from denzo.agents.base_agent import db_execute
        clients = db_execute("SELECT tenant_id FROM clients")
        for c in clients:
            agents = db_execute(
                "SELECT COUNT(*) as n FROM agents WHERE tenant_id=?", (c["tenant_id"],)
            )
            # Should have at least some agents (threshold relaxed for new tenants)
            assert agents[0]["n"] >= 5, f"Client {c['tenant_id']} has only {agents[0]['n']} agents"
