import json
import pytest
from bs4 import BeautifulSoup


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "not json",
        '{"score":999}',
        '{"score":true}',
        '{"score":"80"}',
        '{"score":80,',
    ],
)
def test_malformed_assessments_never_gain_a_default_score(ctx, page, monkeypatch, raw):
    from denzo.agents.layer3_production.content_optimizer import ContentOptimizer

    agent = ContentOptimizer(ctx)
    monkeypatch.setattr(agent, "call_claude", lambda *a, **k: raw)
    assert agent._score_and_fix(page)[0] is None


def test_inventory_expands_sitemap_indexes_and_preserves_full_paths(ctx, monkeypatch):
    from denzo.agents.layer1_research.site_inventory import SiteInventoryAgent
    from denzo.auditor import safe_fetch

    docs = {
        "https://example.com/sitemap.xml": "<sitemapindex><sitemap><loc>https://example.com/child.xml</loc></sitemap></sitemapindex>",
        "https://example.com/child.xml": "<urlset><url><loc>https://example.com/en/about</loc></url><url><loc>https://example.com/es/about</loc></url></urlset>",
    }
    monkeypatch.setattr(
        safe_fetch,
        "fetch_html",
        lambda url, **kwargs: {"ok": url in docs, "html": docs.get(url, "")},
    )
    agent = SiteInventoryAgent(ctx)
    assert set(agent._crawl_sitemap("https://example.com")) == {
        "https://example.com/en/about",
        "https://example.com/es/about",
    }
    monkeypatch.setattr(
        safe_fetch,
        "fetch_html",
        lambda url, **kwargs: {
            "ok": True,
            "html": "<html><title>About</title><h1>About</h1></html>",
            "final_url": url,
        },
    )
    assert agent._fetch_page_data("https://example.com/en/about")["slug"] == "en/about"
    assert agent._fetch_page_data("https://example.com/es/about")["slug"] == "es/about"


def test_inventory_cannot_reclassify_owned_content(ctx, platform_db, page):
    from denzo.agents.layer1_research.site_inventory import SiteInventoryAgent

    url = "https://example.com/blogs/design-guide.html"
    platform_db.execute(
        "UPDATE pages SET status='published',publish_url=? WHERE id=?",
        (url, page["id"]),
    )
    platform_db.commit()
    data = {
        "source_url": url,
        "slug": "design-guide",
        "title": "Fetched copy",
        "content": "live HTML",
        "meta_description": "remote",
        "target_keyword": "design",
        "content_hash": "hash",
    }
    SiteInventoryAgent(ctx)._insert_existing_page(data)
    saved = platform_db.execute(
        "SELECT * FROM pages WHERE id=?", (page["id"],)
    ).fetchone()
    assert (
        saved["managed"] == 1
        and saved["status"] == "published"
        and saved["content"] == page["content"]
    )


def test_real_image_dimensions_and_nested_hero_classes(ctx, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from denzo.auditor import safe_fetch
    from denzo.media import image_dimensions
    from denzo.agents.layer3_production.visual_content_optimizer import (
        VisualContentOptimizer,
    )

    image_dimensions.cache_clear()
    binary = BytesIO()
    Image.new("RGB", (320, 180)).save(binary, format="PNG")
    monkeypatch.setattr(
        safe_fetch,
        "fetch_bytes",
        lambda *a, **k: {"ok": True, "body": binary.getvalue()},
    )
    html = '<section class="hero banner"><div class="container nested"><img src="/hero.png" alt=""></div></section><img src="/detail.png" alt="Design example">'
    fixed = VisualContentOptimizer(ctx)._apply_visual_fixes(
        html, {}, {"/hero.png": "Invented decorative description"}, ["add_dimensions"]
    )
    images = BeautifulSoup(fixed, "html.parser").find_all("img")
    assert images[0]["width"] == "320" and images[0]["height"] == "180"
    assert (
        images[0]["alt"] == ""
        and images[0]["loading"] == "eager"
        and images[0]["fetchpriority"] == "high"
    )
    assert images[1]["loading"] == "lazy"
    assert images[0]["src"] == "/hero.png"
    image_dimensions.cache_clear()


@pytest.mark.parametrize(
    "fmt,expected",
    [
        ("html", "https://example.com/blogs/design-guide.html"),
        ("nextjs", "https://example.com/en/blogs/design-guide"),
    ],
)
def test_rendered_canonicals_match_publisher_paths(ctx, page, fmt, expected):
    from denzo.urls import public_page_url, quality_document
    from denzo.agents.base_agent import validate_page_quality

    ctx.github_format = fmt
    assert public_page_url(page, ctx) == expected
    assert (
        validate_page_quality(
            quality_document(page["content"], page, ctx), base_url=expected
        )
        == []
    )
    if fmt == "nextjs":
        from denzo.agents.layer4_publishing.nextjs_renderer import render_nextjs_page

        rendered = render_nextjs_page(page, ctx)
        assert expected in rendered and "denzo-revision" in rendered
        assert '{"Design guide"}' in rendered


def test_schema_and_fragments_cannot_inject_scripts(ctx, page):
    from denzo.urls import schema_json, quality_document
    from denzo.html_content import sanitize_fragment
    from denzo.agents.base_agent import validate_page_quality

    raw = '<div class="container"><div><p>Keep this</p></div></div><script>alert(1)</script><img src="javascript:alert(1)" onerror="alert(2)"><iframe src="https://evil.example"></iframe>'
    clean = sanitize_fragment(raw)
    assert "Keep this" in clean
    assert "script" not in clean and "onerror" not in clean and "iframe" not in clean
    encoded = schema_json({"description": "</script><script>alert(1)</script>"})
    assert "</script>" not in encoded
    document = quality_document(page["content"], page, ctx).replace(
        "</head>", '<meta name="robots" content="noindex"></head>'
    )
    assert "Page is marked noindex" in validate_page_quality(document)


def test_linker_inserts_only_text_links_to_real_targets(
    ctx, platform_db, page, monkeypatch
):
    from denzo.agents.layer3_production.internal_linker import InternalLinker

    target = platform_db.execute(
        "INSERT INTO pages(tenant_id,title,slug,type,status,content) VALUES ('alice','Materials','materials','service','ready','<p>Choosing materials</p>')"
    ).lastrowid
    original = '<p title="materials">Choose materials <a href="/old">materials</a> <strong>carefully</strong>.</p>'
    platform_db.execute("UPDATE pages SET content=? WHERE id=?", (original, page["id"]))
    platform_db.commit()
    agent = InternalLinker(ctx)
    monkeypatch.setattr(
        agent,
        "_plan_batch",
        lambda *args: [
            {
                "page_id": page["id"],
                "add_links_to": [
                    {
                        "target_id": target,
                        "target_slug": "/invented",
                        "anchor_text": "materials",
                    }
                ],
            }
        ],
    )
    agent.run()
    html = platform_db.execute(
        "SELECT content FROM pages WHERE id=?", (page["id"],)
    ).fetchone()[0]
    soup = BeautifulSoup(html, "html.parser")
    assert soup.p["title"] == "materials"
    assert soup.find("a", href="https://example.com/services/materials.html")
    assert not soup.select("a a") and not soup.find("a", href="/invented")


def test_gsc_bound_property_query_filter_and_aggregate_dimensions(ctx, monkeypatch):
    from denzo.agents.utils import gsc_client

    calls = []
    monkeypatch.setattr(
        gsc_client, "get_bound_site", lambda tid: "sc-domain:example.com"
    )
    monkeypatch.setattr(
        gsc_client,
        "authed_request",
        lambda *a, **k: calls.append((a, k)) or {"rows": [{"clicks": 42}]},
    )
    assert (
        gsc_client.query_search_analytics(
            "alice",
            None,
            "2026-01-01",
            "2026-01-28",
            dimensions=[],
            query_filter="design",
        )[0]["clicks"]
        == 42
    )
    assert "sc-domain%3Aexample.com" in calls[0][0][2]
    body = calls[0][1]["body"]
    assert (
        body["dimensions"] == []
        and body["dimensionFilterGroups"][0]["filters"][0]["expression"] == "design"
    )


def test_roi_totals_do_not_sum_truncated_top_queries(ctx, monkeypatch):
    from denzo.agents.utils import gsc_client, google_oauth
    from denzo.agents.layer5_monitoring.roi_attribution import ROIAttribution

    monkeypatch.setattr(google_oauth, "is_connected", lambda *args: True)
    monkeypatch.setattr(gsc_client, "sync_last_n_days", lambda *a, **k: {})
    monkeypatch.setattr(
        gsc_client,
        "top_queries",
        lambda *a, **k: [{"clicks": 1, "impressions": 10, "position": 1}],
    )
    monkeypatch.setattr(gsc_client, "top_pages", lambda *a, **k: [])
    monkeypatch.setattr(
        gsc_client, "get_bound_site", lambda *args: "sc-domain:example.com"
    )
    monkeypatch.setattr(
        gsc_client,
        "query_search_analytics",
        lambda *a, **k: [{"clicks": 300, "impressions": 10000, "position": 9}],
    )
    result = ROIAttribution(ctx)._collect_gsc_metrics()
    assert (
        result["total_clicks"] == 300
        and result["total_impressions"] == 10000
        and result["avg_position"] == 9
    )


def test_geo_mentions_are_separate_from_retrieved_citations():
    from denzo.agents.layer5_monitoring.geo_monitor import _analyze_citation

    result = _analyze_citation(
        "Visit Alice Studio.", "Alice Studio", "https://example.com", []
    )
    assert result["mentioned"] and not result["citation_verified"]
    result = _analyze_citation(
        "Visit Alice Studio.",
        "Alice Studio",
        "https://example.com",
        [],
        ["https://example.com.evil.test/"],
    )
    assert not result["citation_verified"]
    result = _analyze_citation(
        "Visit Alice Studio.",
        "Alice Studio",
        "https://example.com",
        [],
        ["https://www.example.com/about"],
    )
    assert result["citation_verified"]
    assert not _analyze_citation("A business response.", "", "https://example.com", [])[
        "mentioned"
    ]


def test_geo_baseline_persists_verified_citations_and_excludes_failed_engines(
    ctx, platform_db, monkeypatch
):
    from denzo.agents.layer5_monitoring import geo_baseline

    agent = geo_baseline.GEOBaselineAgent(ctx)
    monkeypatch.setattr(geo_baseline.time, "sleep", lambda *args: None)
    monkeypatch.setattr(agent, "_generate_seed_queries", lambda: ["design studio"])
    monkeypatch.setattr(agent, "_get_api_key", lambda name: "test-key")
    monkeypatch.setattr(
        agent,
        "_query_perplexity",
        lambda query: {
            "success": True,
            "cited": True,
            "mentioned": True,
            "citations": ["https://example.com/about"],
            "text_snippet": "Alice Studio",
        },
    )
    monkeypatch.setattr(
        agent,
        "_query_chatgpt",
        lambda query: {
            "success": True,
            "cited": False,
            "mentioned": True,
            "text_snippet": "Alice Studio",
        },
    )
    monkeypatch.setattr(agent, "_query_gemini", lambda query: {"success": False})
    agent.run()
    rows = platform_db.execute(
        "SELECT * FROM geo_queries WHERE tenant_id='alice' ORDER BY ai_model"
    ).fetchall()
    assert len(rows) == 2 and all(row["baseline"] == 1 for row in rows)
    assert rows[0]["client_mentioned"] == 1 and rows[0]["citation_verified"] == 0
    assert rows[1]["citation_verified"] == 1 and json.loads(
        rows[1]["citations_json"]
    ) == ["https://example.com/about"]
    summary = json.loads(
        platform_db.execute(
            "SELECT value FROM settings WHERE tenant_id='alice' AND key='geo_baseline'"
        ).fetchone()[0]
    )
    assert (
        summary["total_checks"] == 2
        and summary["citations_found"] == 1
        and summary["citation_rate"] == 50
    )


def test_nextjs_heading_decodes_entities_without_duplicating_the_heading(ctx, page):
    from denzo.agents.layer4_publishing.nextjs_renderer import render_nextjs_page

    ctx.github_format = "nextjs"
    page.update(
        slug="2026/design-guide",
        content='<h1>Design &amp; materials</h1><div class="container"><div><p>Preserve this detail.</p></div></div>',
    )
    rendered = render_nextjs_page(page, ctx)
    assert '{"Design & materials"}' in rendered
    assert rendered.count("Design &") == 1
    assert "Preserve this detail." in rendered and "<div><p>" in rendered
    assert "function DenzoPage2026DesignGuide()" in rendered


@pytest.mark.parametrize(
    "schema,expected",
    [
        ({"@type": "Article"}, False),
        ({"@type": "LocalBusiness"}, False),
        ({"@type": "JobPosting"}, False),
        (
            {
                "@type": "JobPosting",
                "title": "Designer",
                "datePosted": "2026-09-15",
                "hiringOrganization": {"name": "Studio"},
            },
            True,
        ),
        (
            {
                "@type": "VideoObject",
                "publication": {"@type": "BroadcastEvent", "isLiveBroadcast": True},
            },
            True,
        ),
    ],
)
def test_google_indexing_eligibility(schema, expected):
    from denzo.agents.layer4_publishing.indexation_accelerator import (
        google_indexing_eligible,
    )

    assert google_indexing_eligible(json.dumps(schema)) is expected


def test_no_accepted_submission_is_never_marked_indexed(
    ctx, page, platform_db, monkeypatch
):
    from denzo.agents.layer4_publishing.indexation_accelerator import (
        IndexationAccelerator,
    )

    platform_db.execute(
        "UPDATE pages SET status='published',deployment_status='verified',publish_url='https://example.com/blogs/design-guide.html' WHERE id=?",
        (page["id"],),
    )
    platform_db.commit()
    agent = IndexationAccelerator(ctx)
    monkeypatch.setattr(agent, "_publish_key_file", lambda key: False)
    monkeypatch.setattr(agent, "_ping_sitemap", lambda url: None)
    agent.run()
    assert "SUBMITTED" not in (
        platform_db.execute(
            "SELECT notes FROM pages WHERE id=?", (page["id"],)
        ).fetchone()[0]
        or ""
    )
    assert (
        platform_db.execute(
            "SELECT status FROM agents WHERE tenant_id='alice' AND name='Indexation Accelerator'"
        ).fetchone()[0]
        == "error"
    )
