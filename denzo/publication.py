"""Publication reservations, daily quotas and verification of deployed revisions."""

import uuid
from denzo.db import get_db
from denzo.editorial import publishable, revision_hash


def reserve_publication(tenant_id, page_id, publisher, max_per_day, url):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM pages WHERE tenant_id=? AND id=?", (tenant_id, page_id)
        ).fetchone()
        if not row or not publishable(row):
            raise ValueError(
                "Page changed, is unapproved, or has no valid quality assessment"
            )
        if db.execute(
            "SELECT 1 FROM publication_attempts WHERE tenant_id=? AND page_id=? AND status IN ('reserved','awaiting_deploy')",
            (tenant_id, page_id),
        ).fetchone():
            raise ValueError("Publication already in progress")
        if db.execute(
            "SELECT 1 FROM publication_attempts WHERE tenant_id=? AND page_id=? AND publisher=? AND revision=? AND status='published'",
            (tenant_id, page_id, publisher, revision_hash(row)),
        ).fetchone():
            db.execute(
                "UPDATE pages SET status='published',deployment_status='verified' WHERE tenant_id=? AND id=?",
                (tenant_id, page_id),
            )
            db.commit()
            raise ValueError("This revision is already published")
        schedule = db.execute(
            "SELECT enabled FROM schedules WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        if schedule and schedule["enabled"]:
            max_per_day = min(1, int(max_per_day))
        count = db.execute(
            "SELECT COUNT(*) FROM publication_attempts WHERE tenant_id=? AND status IN ('reserved','awaiting_deploy','published') AND created_at>=datetime('now','-24 hours')",
            (tenant_id,),
        ).fetchone()[0]
        if count >= max(0, int(max_per_day)):
            raise ValueError("Daily publishing limit reached")
        attempt = uuid.uuid4().hex
        db.execute(
            "INSERT INTO publication_attempts(id,tenant_id,page_id,publisher,revision,status,url) VALUES (?,?,?,?,?,'reserved',?)",
            (attempt, tenant_id, page_id, publisher, revision_hash(row), url),
        )
        db.commit()
        return attempt, dict(row)
    finally:
        db.close()


def committed(attempt, url, ref=None):
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM publication_attempts WHERE id=?", (attempt,)
        ).fetchone()
        db.execute(
            "UPDATE publication_attempts SET status='awaiting_deploy',url=? WHERE id=?",
            (url, attempt),
        )
        db.execute(
            "UPDATE pages SET status='publishing',deployment_status='pending',publish_url=?,publish_ref=COALESCE(?,publish_ref),updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND id=? AND approval_hash=?",
            (url, ref, row["tenant_id"], row["page_id"], row["revision"]),
        )
        db.commit()
    finally:
        db.close()


def failed(attempt, message):
    db = get_db()
    try:
        db.execute(
            "UPDATE publication_attempts SET status='error',error=?,completed_at=CURRENT_TIMESTAMP WHERE id=?",
            (message[:500], attempt),
        )
        db.commit()
    finally:
        db.close()


def verify_publication(attempt):
    from bs4 import BeautifulSoup
    from denzo.auditor.safe_fetch import fetch_html

    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM publication_attempts WHERE id=?", (attempt,)
        ).fetchone()
        if (
            not row
            or row["status"] not in ("awaiting_deploy", "reserved")
            or not row["url"]
        ):
            return False
        record = dict(row)
        db.execute(
            "UPDATE publication_attempts SET last_checked_at=CURRENT_TIMESTAMP WHERE id=?",
            (attempt,),
        )
        db.commit()
    finally:
        db.close()
    try:
        response = fetch_html(record["url"])
        if not response.get("ok"):
            return False
        soup = BeautifulSoup(response["html"], "html.parser")
        marker = soup.find(
            "meta", attrs={"name": "denzo-revision", "content": record["revision"]}
        )
        if not marker:
            return False
    except (ValueError, OSError):
        return False
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE publication_attempts SET status='published',completed_at=CURRENT_TIMESTAMP,error=NULL WHERE id=?",
            (attempt,),
        )
        db.execute(
            "UPDATE pages SET status='published',deployment_status='verified',published_at=COALESCE(published_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND id=? AND approval_hash=?",
            (record["tenant_id"], record["page_id"], record["revision"]),
        )
        db.commit()
    finally:
        db.close()
    return True


def reconcile_publications(tenant_id=None, limit=20):
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id FROM publication_attempts WHERE status IN ('reserved','awaiting_deploy') AND (? IS NULL OR tenant_id=?) ORDER BY COALESCE(last_checked_at,''),created_at LIMIT ?",
            (tenant_id, tenant_id, limit),
        ).fetchall()
    finally:
        db.close()
    return sum(verify_publication(r["id"]) for r in rows)
