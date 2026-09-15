"""One editorial contract for forms, API, agents and publishers."""
import hashlib
import json
from denzo.db import get_db

CONTENT_FIELDS = ('content', 'title', 'meta_title', 'meta_description', 'schema_markup', 'slug', 'type')
MIN_QUALITY = 70


def revision_hash(page):
    page = dict(page)
    data = {k: page.get(k) or '' for k in CONTENT_FIELDS}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def publishable(page):
    page = dict(page)
    return (page.get('status') == 'ready' and bool(page.get('content', '').strip())
            and bool(page.get('managed', 1)) and (page.get('quality_score') or 0) >= MIN_QUALITY
            and page.get('approval_hash') == revision_hash(page))


def transition(tenant_id, page_id, action, user_id, note=''):
    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')
        page = db.execute('SELECT * FROM pages WHERE tenant_id=? AND id=?', (tenant_id, page_id)).fetchone()
        if page is None:
            raise LookupError('Page not found')
        page = dict(page)
        if db.execute("SELECT 1 FROM publication_attempts WHERE tenant_id=? AND page_id=? AND status IN ('reserved','awaiting_deploy')", (tenant_id,page_id)).fetchone():
            raise ValueError('This version is being published. Wait for verification before editing.')
        from denzo.execution import CONTENT_WRITERS
        active = db.execute("SELECT agent_name FROM agent_jobs WHERE tenant_id=? AND status IN ('queued','running')", (tenant_id,)).fetchall()
        if any(r['agent_name'] in CONTENT_WRITERS for r in active):
            raise ValueError('Content is being processed. Wait for the agent to finish before reviewing.')
        notes = page.get('notes') or '' 
        for marker in ('[PENDING_REVIEW]', '[APPROVED]', '[REJECTED]'):
            notes = notes.replace(marker, '')
        if action == 'approve':
            if page['status'] not in ('ready', 'published') or not (page.get('content') or '').strip():
                raise ValueError('Generate and review the page before approval.')
            if (page.get('quality_score') or 0) < MIN_QUALITY:
                raise ValueError('A valid quality assessment of at least 70 is required.')
            if not page.get('managed', 1):
                raise ValueError('This page is not managed by DENZO.')
            db.execute("UPDATE pages SET status='ready',approval_hash=?,approved_at=CURRENT_TIMESTAMP,approved_by=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND id=?",
                       (revision_hash(page), user_id, notes.strip()+' [APPROVED]', tenant_id, page_id))
            db.execute("UPDATE agents SET status='idle',current_task='Approved content available' WHERE tenant_id=? AND name IN ('GitHub Publisher','WordPress Publisher','Pipeline Director') AND status NOT IN ('working','starting')", (tenant_id,))
        elif action in ('regenerate' , 'reject', 'request_changes'):
            # Keep the current content; the generator replaces it only after a successful run.
            db.execute("UPDATE pages SET status='draft',quality_score=NULL,approval_hash=NULL,approved_at=NULL,approved_by=NULL,notes=?,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND id=?",
                       (notes.strip()+f' [PENDING_REVIEW] [{action.upper()}] '+note.strip(), tenant_id, page_id))
            db.execute("UPDATE agents SET status='idle',current_task='Editorial revision requested' WHERE tenant_id=? AND name IN ('Programmatic SEO','Content Optimizer','Visual Content Optimizer','GEO Optimizer','Internal Linker','GitHub Publisher','WordPress Publisher') AND status NOT IN ('working','starting')", (tenant_id,))
        else:
            raise ValueError('Unknown editorial action')
        db.execute("INSERT INTO activity(tenant_id,type,message,agent,level) VALUES (?,'review',?,'editor','info')", (tenant_id,f'{action}: {page["title"]} (user {user_id})'))
        db.commit()
        return {'status': action, 'page_id': page_id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
