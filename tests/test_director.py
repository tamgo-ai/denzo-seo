"""Real state-machine transitions with database-backed prerequisites."""

import pytest
from denzo.agents.director import (
    PipelineDirector,
    DISCOVERY_AGENTS,
    LAYER_1,
    LAYER_2,
    LAYER_2B,
    LAYER_3,
    LAYER_4,
)


def ready_for_content(db):
    db.execute(
        "INSERT INTO settings(tenant_id,key,value) VALUES ('alice','world_state','{}')"
    )
    names = DISCOVERY_AGENTS + LAYER_1 + LAYER_2 + LAYER_2B
    for name in names:
        db.execute(
            "UPDATE agents SET status='done' WHERE tenant_id='alice' AND name=?",
            (name,),
        )
    db.commit()


def test_discovery_precedes_production(ctx):
    director = PipelineDirector(ctx)
    state = director._assess_state()
    assert state["keywords"]["total"] == 0
    assert director._evaluate(state) == ["Site Inventory"]
    assert not director._is_pipeline_complete(state)


def test_production_then_serial_optimizers_then_approval(ctx, platform_db, page):
    ready_for_content(platform_db)
    director = PipelineDirector(ctx)
    assert director._evaluate(director._assess_state()) == ["Programmatic SEO"]
    platform_db.execute(
        "UPDATE agents SET status='done' WHERE tenant_id='alice' AND name='Programmatic SEO'"
    )
    platform_db.commit()
    for name in LAYER_4:
        assert director._evaluate(director._assess_state()) == [name]
        platform_db.execute(
            "UPDATE agents SET status='working' WHERE tenant_id='alice' AND name=?",
            (name,),
        )
        platform_db.commit()
        assert director._evaluate(director._assess_state()) == []
        platform_db.execute(
            "UPDATE agents SET status='done' WHERE tenant_id='alice' AND name=?",
            (name,),
        )
        platform_db.commit()
    # A quality score alone does not authorize publishing.
    assert director._evaluate(director._assess_state()) == []
    from denzo.editorial import transition

    transition("alice", page["id"], "approve", 101)
    assert director._evaluate(director._assess_state()) == ["GitHub Publisher"]
    ctx.publisher_type = "wordpress"
    assert director._evaluate(director._assess_state()) == ["WordPress Publisher"]


def test_errors_are_not_completion_and_retries_stop(ctx, platform_db, monkeypatch):
    director = PipelineDirector(ctx)
    platform_db.execute(
        "UPDATE agents SET status='error',run_count=3 WHERE tenant_id='alice' AND name='Site Inventory'"
    )
    platform_db.commit()
    state = director._assess_state()
    assert not director._all_done(DISCOVERY_AGENTS, state["agents"])
    assert director._evaluate(state) == []
    assert director._check_deadlock(state["agents"])
    monkeypatch.setattr(director, "_generate_strategy", lambda: None)
    director.run()
    assert (
        platform_db.execute(
            "SELECT status FROM agents WHERE tenant_id='alice' AND name='Pipeline Director'"
        ).fetchone()[0]
        == "error"
    )
