import base64
import json
import pytest
from types import SimpleNamespace
from denzo.editorial import transition, revision_hash
from denzo.publication import reserve_publication, committed, verify_publication


def row(db, pid):
    return dict(db.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone())


def response(status=200, data=None, headers=None, text=""):
    import requests

    def check():
        if status >= 400:
            raise requests.HTTPError(str(status))

    return SimpleNamespace(
        status_code=status,
        json=lambda: data,
        headers=headers or {},
        text=text,
        raise_for_status=check,
    )


def test_only_exact_live_revision_is_published(platform_db, page, monkeypatch):
    from denzo.auditor import safe_fetch

    transition("alice", page["id"], "approve", 101)
    attempt, _ = reserve_publication(
        "alice", page["id"], "github", 1, "https://example.com/blogs/design-guide.html"
    )
    committed(attempt, "https://example.com/blogs/design-guide.html", "main:path")
    assert row(platform_db, page["id"])["status"] == "publishing"
    assert row(platform_db, page["id"])["published_at"] is None
    monkeypatch.setattr(
        safe_fetch,
        "fetch_html",
        lambda url: {"ok": True, "html": '<meta name="denzo-revision" content="old">'},
    )
    assert not verify_publication(attempt)
    monkeypatch.setattr(
        safe_fetch,
        "fetch_html",
        lambda url: {
            "ok": True,
            "html": f'<meta name="denzo-revision" content="{revision_hash(page)}">',
        },
    )
    assert verify_publication(attempt)
    assert row(platform_db, page["id"])["published_at"]
    assert row(platform_db, page["id"])["status"] == "published"
    transition("alice", page["id"], "approve", 101)
    with pytest.raises(ValueError, match="already published"):
        reserve_publication(
            "alice",
            page["id"],
            "github",
            1,
            "https://example.com/blogs/design-guide.html",
        )


def test_daily_quota_survives_separate_publisher_runs(platform_db, page):
    transition("alice", page["id"], "approve", 101)
    reserve_publication("alice", page["id"], "github", 1, "https://example.com/one")
    pid = platform_db.execute(
        "INSERT INTO pages(tenant_id,title,slug,type,status,content,quality_score,managed) VALUES ('alice','Two','two','blog','ready','content',85,1)"
    ).lastrowid
    platform_db.commit()
    transition("alice", pid, "approve", 101)
    with pytest.raises(ValueError, match="Daily publishing limit"):
        reserve_publication("alice", pid, "wordpress", 1, "https://example.com/two")
    with pytest.raises(ValueError, match="being published"):
        transition("alice", page["id"], "regenerate", 101)


def test_github_auxiliary_files_stay_managed_and_protect_existing(ctx, platform_db):
    from denzo.agents.layer4_publishing.github_publisher import GitHubPublisher

    files = {}
    writes = []

    class Session:
        def get(self, url, **kwargs):
            import hashlib

            data = base64.b64decode(files.get(url, ""))
            sha = hashlib.sha1(
                b"blob " + str(len(data)).encode() + b"\0" + data
            ).hexdigest()
            return (
                response(200, {"sha": sha, "content": files[url]})
                if url in files
                else response(404, {})
            )

        def put(self, url, json, **kwargs):
            files[url] = json["content"]
            writes.append(url)
            return response(201, {"content": {"sha": "new"}})

    publisher = GitHubPublisher(ctx)
    session = Session()
    content = base64.b64encode(b"first sitemap").decode()
    assert publisher._publish_file(
        session, "org/site", "main", "sitemap.xml", content, "update"
    )
    assert publisher._publish_file(
        session, "org/site", "main", "sitemap.xml", content, "update"
    )
    assert len(writes) == 1
    assert publisher._publish_file(
        session,
        "org/site",
        "main",
        "sitemap.xml",
        base64.b64encode(b"changed").decode(),
        "update",
    )
    assert len(writes) == 2
    files["https://api.github.com/repos/org/site/contents/home.html"] = content
    assert not publisher._publish_file(
        session, "org/site", "main", "home.html", content, "update"
    )
    assert len(writes) == 2


def test_wordpress_blog_uses_posts_and_connector_metadata(
    ctx, platform_db, page, monkeypatch
):
    from denzo.agents.layer4_publishing import wordpress_publisher as module
    from denzo.auditor import safe_fetch

    ctx.publisher_type = "wordpress"
    ctx.wp_url = "https://example.com"
    ctx.wp_user = "editor"
    ctx.wp_app_password = "fixture"
    schema = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": "Design guide",
        }
    )
    platform_db.execute(
        "UPDATE pages SET schema_markup=? WHERE id=?", (schema, page["id"])
    )
    platform_db.execute("UPDATE pages SET quality_score=85 WHERE id=?", (page["id"],))
    platform_db.commit()
    transition("alice", page["id"], "approve", 101)
    sent = []

    def get(url, **kwargs):
        return (
            response(
                data={
                    "seo_metadata": True,
                    "publish_posts": True,
                    "publish_pages": True,
                }
            )
            if url.endswith("capabilities")
            else response(data=[])
        )

    def post(url, **kwargs):
        sent.append((url, kwargs["json"]))
        return response(
            201, {"id": 55, "link": "https://example.com/blog/design-guide/"}
        )

    monkeypatch.setattr(module.requests, "get", get)
    monkeypatch.setattr(module.requests, "post", post)
    monkeypatch.setattr(
        safe_fetch,
        "fetch_html",
        lambda url: {
            "ok": True,
            "html": '<meta name="denzo-revision" content="'
            + sent[0][1]["meta"]["denzo_revision"]
            + '">',
        },
    )
    module.WordPressPublisher(ctx).run()
    assert len(sent) == 1 and sent[0][0].endswith("/wp/v2/posts")
    assert sent[0][1]["meta"]["denzo_schema"] == schema
    assert sent[0][1]["meta"]["denzo_description"] == page["meta_description"]
    assert row(platform_db, page["id"])["status"] == "published"
    assert (
        row(platform_db, page["id"])["publish_url"]
        == "https://example.com/blog/design-guide/"
    )


def test_wordpress_missing_connector_cannot_report_success(
    ctx, platform_db, monkeypatch
):
    from denzo.agents.layer4_publishing import wordpress_publisher as module

    ctx.publisher_type = "wordpress"
    ctx.wp_url = "https://example.com"
    ctx.wp_user = "editor"
    ctx.wp_app_password = "fixture"
    monkeypatch.setattr(module.requests, "get", lambda *a, **k: response(404, {}))
    module.WordPressPublisher(ctx).run()
    assert (
        platform_db.execute(
            "SELECT status FROM agents WHERE tenant_id='alice' AND name='WordPress Publisher'"
        ).fetchone()[0]
        == "error"
    )


def test_wordpress_ambiguous_timeout_cannot_create_twice(
    ctx, platform_db, page, monkeypatch
):
    import requests
    from denzo.agents.layer4_publishing import wordpress_publisher as module
    from denzo.auditor import safe_fetch

    ctx.publisher_type = "wordpress"
    ctx.wp_url = "https://example.com"
    ctx.wp_user = "editor"
    ctx.wp_app_password = "fixture"
    transition("alice", page["id"], "approve", 101)
    monkeypatch.setattr(
        module.requests,
        "get",
        lambda url, **kwargs: (
            response(data={"seo_metadata": True, "publish_posts": True})
            if url.endswith("capabilities")
            else response(data=[])
        ),
    )
    calls = []

    def timeout(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout("uncertain commit")

    monkeypatch.setattr(module.requests, "post", timeout)
    monkeypatch.setattr(safe_fetch, "fetch_html", lambda url: {"ok": False, "html": ""})
    publisher = module.WordPressPublisher(ctx)
    publisher.run()
    publisher.run()
    assert len(calls) == 1
    assert row(platform_db, page["id"])["status"] == "publishing"


def test_youtube_transfers_bytes_and_reuses_completed_session(ctx, page, monkeypatch):
    import requests
    from denzo.agents.layer4_publishing.video_engine import VideoEngine
    from denzo.agents.utils import google_oauth
    from denzo.auditor import safe_fetch

    monkeypatch.setattr(google_oauth, "get_access_token", lambda *args: "fixture")
    blob = b"\x00\x00\x00\x10ftypisom" + b"video bytes"
    monkeypatch.setattr(
        safe_fetch, "fetch_bytes", lambda *a, **k: {"ok": True, "body": blob}
    )
    posts = []
    puts = []

    def post(url, **kwargs):
        posts.append((url, kwargs))
        return response(
            headers={
                "Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture"
            }
        )

    def put(url, **kwargs):
        puts.append(kwargs)
        return (
            response(308) if not kwargs["data"] else response(201, {"id": "actual-id"})
        )

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "put", put)
    engine = VideoEngine(ctx)
    url = engine._upload_to_youtube(
        {"title": "Design guide"}, "https://cdn.example/video.mp4", page
    )
    assert url == "https://www.youtube.com/watch?v=actual-id"
    assert puts[-1]["data"] == blob
    assert "uploadType=resumable" in posts[0][0]
    assert (
        engine._upload_to_youtube(
            {"title": "Design guide"}, "https://cdn.example/video.mp4", page
        )
        == url
    )
    assert len(posts) == 1
