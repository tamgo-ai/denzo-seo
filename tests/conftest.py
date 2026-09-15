"""Isolated tenant fixtures. No test reads or mutates a server database."""

import pytest


@pytest.fixture
def platform_db(tmp_path, monkeypatch):
    import denzo.db as module
    from denzo.agents.base_agent import close_thread_connection

    close_thread_connection()
    monkeypatch.setattr(module, "DB_PATH", str(tmp_path / "platform.db"))
    monkeypatch.setenv("SECRET_KEY", "test-key-" * 8)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    module.init_db()
    db = module.get_db()
    # Explicit roles: these are customer accounts, not the legacy admin default.
    db.execute(
        "INSERT INTO users(id,username,password_hash,role,plan) VALUES (101,'alice','invalid','client','pro')"
    )
    db.execute(
        "INSERT INTO users(id,username,password_hash,role,plan) VALUES (102,'bob','invalid','client','pro')"
    )
    db.execute(
        "INSERT INTO users(id,username,password_hash,role,plan) VALUES (103,'admin-test','invalid','admin','agency')"
    )
    from denzo.agents.registry import AGENT_REGISTRY

    for tid, owner, name in [
        ("alice", 101, "Alice Studio"),
        ("bob", 102, "Private Bob Business"),
    ]:
        db.execute(
            "INSERT INTO clients(tenant_id,name,owner_user_id,website_url) VALUES (?,?,?,'https://example.com')",
            (tid, name, owner),
        )
        db.execute(
            "INSERT INTO client_context(tenant_id,domain,primary_city,services) VALUES (?,'example.com','Ontario','[\"design\"]')",
            (tid,),
        )
        for agent, (_, _, layer, color) in AGENT_REGISTRY.items():
            db.execute(
                "INSERT INTO agents(tenant_id,name,layer,color) VALUES (?,?,?,?)",
                (tid, agent, layer, color),
            )
    db.commit()
    yield db
    db.close()
    close_thread_connection()


@pytest.fixture
def ctx(platform_db):
    from denzo.context.builder import build_client_context

    return build_client_context("alice")


@pytest.fixture
def page(platform_db):
    db = platform_db
    content = (
        "<h2>Planning your design project</h2><p>"
        + ("Consider the materials, dimensions and how you use the space. " * 8)
        + "</p>"
    )
    pid = db.execute(
        """INSERT INTO pages(tenant_id,title,slug,type,status,content,meta_title,meta_description,quality_score,managed)
      VALUES('alice','Design guide','design-guide','blog','ready',?,'Design guide','A practical guide to planning your design project.',85,1)""",
        (content,),
    ).lastrowid
    db.commit()
    return dict(db.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone())


@pytest.fixture
def app(platform_db, monkeypatch):
    import redis

    def offline(*args, **kwargs):
        raise OSError("No Redis in this isolated test")

    monkeypatch.setattr(redis.Redis, "from_url", offline)
    from denzo import create_app

    app = create_app()
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    return app


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=101, role="client", username="alice")
    return client
