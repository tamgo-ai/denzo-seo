"""Observable HTTP, tenant and billing contracts, using a fresh DB per test."""

import pytest
from denzo.db import get_db


@pytest.mark.parametrize(
    "path,method",
    [
        ("/clients/bob/pages", "get"),
        ("/clients/bob/pages/1/preview", "get"),
        ("/clients/bob/pages/1/review", "get"),
        ("/clients/bob/pages/1/approve", "post"),
        ("/clients/bob/pages/1/request-changes", "post"),
        ("/clients/bob/pages/1/regenerate", "post"),
        ("/clients/bob/pages/export.csv", "get"),
        ("/clients/bob/competitors", "get"),
        ("/clients/bob/competitors/export.csv", "get"),
        ("/clients/bob/competitors/1/resolve-cannibalization", "post"),
        ("/api/bob/pages/1/approve", "post"),
        ("/api/bob/pages/1/reject", "post"),
        ("/api/bob/pipeline/run", "post"),
        ("/api/bob/pipeline/reset", "post"),
        ("/lite/bob/", "get"),
        ("/settings/", "get"),
        ("/settings/", "post"),
    ],
)
def test_other_tenant_and_global_settings_denied(client, path, method):
    assert getattr(client, method)(path).status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/clients/",
        "/clients/new",
        "/clients/alice/pages",
        "/clients/alice/keywords",
        "/clients/alice/competitors",
        "/clients/alice/brand-voice/",
        "/clients/alice/geo",
        "/lite/alice/",
    ],
)
def test_navigation_does_not_leak_other_clients(client, path):
    result = client.get(path)
    assert result.status_code == 200
    assert b"Private Bob Business" not in result.data


def test_anonymous_api_rejected(app):
    assert app.test_client().post("/api/alice/pages/1/approve").status_code == 401


def test_admin_can_access_other_tenant_and_settings(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=103, role="admin")
    assert client.get("/clients/bob/pages").status_code == 200
    assert client.get("/settings/").status_code == 200


def test_onboarding_sets_owner_and_separate_audience(client, platform_db):
    result = client.post(
        "/clients/create",
        data={
            "name": "New Studio",
            "website_url": "https://new.example",
            "business_type": "agency",
            "target_audience": "Local business owners",
        },
    )
    assert result.status_code == 302
    row = platform_db.execute(
        "SELECT owner_user_id FROM clients WHERE tenant_id=?", ("new-studio",)
    ).fetchone()
    assert row is not None and row["owner_user_id"] == 101
    row = platform_db.execute(
        "SELECT target_audience,service_cities FROM client_context WHERE tenant_id=?",
        ("new-studio",),
    ).fetchone()
    assert row["target_audience"] == "Local business owners"
    assert "business owners" not in row["service_cities"]
    assert (
        platform_db.execute(
            "SELECT COUNT(*) FROM agents WHERE tenant_id=?", ("new-studio",)
        ).fetchone()[0]
        >= 30
    )


def test_api_and_form_approval_share_contract(client, page, platform_db):
    pid = page["id"]
    result = client.post(f"/api/alice/pages/{pid}/approve")
    assert result.status_code == 200
    row = dict(platform_db.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone())
    from denzo.editorial import publishable

    assert publishable(row)
    assert "[PENDING_REVIEW]" not in (row["notes"] or "")
    client.post(
        f"/clients/alice/pages/{pid}/request-changes",
        data={"feedback": "Use the supplied case study"},
    )
    row = dict(platform_db.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone())
    assert row["status"] == "draft" and row["content"] == page["content"]
    assert not publishable(row)


def test_facts_require_attestation_and_belong_to_owner(client, platform_db):
    payload = {
        "fact_statement": "Opened in 2019",
        "fact_source": "Company registration",
        "target_audience": "Homeowners",
    }
    client.post("/clients/alice/brand-voice/", data=payload)
    assert platform_db.execute("SELECT COUNT(*) FROM client_facts").fetchone()[0] == 0
    payload["fact_confirmed"] = "yes"
    assert client.post("/clients/alice/brand-voice/", data=payload).status_code == 200
    fact = dict(platform_db.execute("SELECT * FROM client_facts").fetchone())
    assert fact["verified_by"] == 101 and fact["tenant_id"] == "alice"
    from denzo.context.builder import build_client_context

    prompt = build_client_context("alice").to_prompt_block()
    assert "Opened in 2019" in prompt and "Company registration" in prompt
    assert "Opened in 2019" not in build_client_context("bob").to_prompt_block()


@pytest.mark.parametrize(
    "expiry", ["2001-01-01T00:00:00", "2001-01-01T00:00:00+00:00", "invalid"]
)
def test_expired_or_invalid_trial_is_free(platform_db, expiry):
    platform_db.execute(
        "UPDATE users SET plan='trial',trial_ends_at=? WHERE id=101", (expiry,)
    )
    platform_db.commit()
    from denzo.billing.enforce import get_user_plan, agent_entitled

    assert get_user_plan(101) == "free"
    assert agent_entitled("alice", "Pipeline Director")[0] is False


def test_client_page_keyword_limits_are_atomic(platform_db):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    platform_db.execute("UPDATE users SET plan='free' WHERE id=101")
    for i in range(24):
        platform_db.execute(
            "INSERT INTO pages(tenant_id,title,slug,type) VALUES ('alice',?,?,'blog')",
            (str(i), str(i)),
        )
    platform_db.commit()

    def insert(index):
        db = get_db()
        try:
            db.execute(
                "INSERT INTO pages(tenant_id,title,slug,type) VALUES ('alice',?,?,'blog')",
                ("Race " + str(index), "race-" + str(index)),
            )
            db.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(insert, [1, 2])) == [False, True]
    with pytest.raises(sqlite3.IntegrityError, match="client limit"):
        platform_db.execute(
            "INSERT INTO clients(tenant_id,name,owner_user_id) VALUES ('over-limit','Extra',101)"
        )
    platform_db.rollback()
    for i in range(50):
        platform_db.execute(
            "INSERT INTO keywords(tenant_id,keyword) VALUES ('alice',?)", (str(i),)
        )
    platform_db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="keyword limit"):
        platform_db.execute(
            "INSERT INTO keywords(tenant_id,keyword) VALUES ('alice','too many')"
        )
    platform_db.rollback()
    # Discovered external URLs do not consume the generated page quota.
    platform_db.execute(
        "INSERT INTO pages(tenant_id,title,slug,type,managed) VALUES ('alice','Existing','existing','page',0)"
    )
    platform_db.commit()


def test_agent_registry_imports_and_constructs(ctx):
    from denzo.agents.registry import AGENT_REGISTRY, get_agent

    assert len(AGENT_REGISTRY) >= 30
    for name in AGENT_REGISTRY:
        agent = get_agent(name, ctx)
        assert agent.tenant_id == "alice"
        assert isinstance(agent.PREREQUISITES, list)


def test_app_security_settings(app):
    assert not app.debug
    assert app.config["SESSION_COOKIE_HTTPONLY"]
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert len(list(app.url_map.iter_rules())) > 50


def test_trial_cannot_be_restarted(client, platform_db):
    platform_db.execute("UPDATE users SET plan='free',trial_ends_at=NULL WHERE id=101")
    platform_db.commit()
    assert client.post("/upgrade/activate").status_code == 200
    expiry = platform_db.execute(
        "SELECT trial_ends_at FROM users WHERE id=101"
    ).fetchone()[0]
    assert client.post("/upgrade/activate").status_code == 409
    assert (
        platform_db.execute("SELECT trial_ends_at FROM users WHERE id=101").fetchone()[
            0
        ]
        == expiry
    )


def test_lite_review_queue_includes_ready_unapproved_content(client, page):
    result = client.get("/lite/alice/content?status=draft")
    assert result.status_code == 200 and b"Design guide" in result.data
    assert b"Design guide" not in client.get("/lite/alice/content?status=ready").data


def test_free_plan_cannot_report_pipeline_started(client, platform_db):
    platform_db.execute("UPDATE users SET plan='free' WHERE id=101")
    platform_db.commit()
    assert client.post("/api/alice/pipeline/run").status_code == 409


def test_preview_is_sandboxed(client, page):
    response = client.get(f"/clients/alice/pages/{page['id']}/preview")
    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"].startswith("sandbox;")
