"""SQLite-backed audit queue shared by all web/worker processes.

A request key is permanently bound to its URL. Expired leases are retried up to
three times; stale workers cannot overwrite a newer result.
"""
import json
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit
from denzo.db import get_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_jobs (
 audit_id TEXT PRIMARY KEY REFERENCES site_audits(audit_id),
 request_key TEXT NOT NULL UNIQUE, url TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 available_at REAL NOT NULL, lease_token TEXT, lease_until REAL,
 created_at REAL NOT NULL, finished_at REAL
);
UPDATE site_audits SET status='error',error_message='Legacy in-memory job requires a new audit request'
 WHERE status IN ('pending','running') AND NOT EXISTS(SELECT 1 FROM audit_jobs j WHERE j.audit_id=site_audits.audit_id);
CREATE INDEX IF NOT EXISTS audit_jobs_available ON audit_jobs(state,available_at);
CREATE TABLE IF NOT EXISTS audit_worker_heartbeats (worker_id TEXT PRIMARY KEY,seen_at REAL NOT NULL,job_id TEXT);
CREATE TABLE IF NOT EXISTS audit_rate_limits (
 client_key TEXT NOT NULL, window INTEGER NOT NULL, count INTEGER NOT NULL,
 PRIMARY KEY(client_key,window)
);
"""


@contextmanager
def database():
    db = get_db()
    try:
        db.executescript(SCHEMA)
        yield db
    finally:
        db.close()


def enqueue(url, request_key, client_key, limit=10):
    now = time.time()
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT audit_id,url FROM audit_jobs WHERE request_key=?', (request_key,)).fetchone()
        if existing:
            if existing['url'] != url:
                db.rollback()
                raise ValueError('Idempotency key is already bound to a different URL')
            db.commit()
            return existing['audit_id']
        window = int(now // 3600)
        db.execute('DELETE FROM audit_rate_limits WHERE window < ?', (window - 1,))
        row = db.execute('SELECT count FROM audit_rate_limits WHERE client_key=? AND window=?', (client_key, window)).fetchone()
        if row and row['count'] >= limit:
            db.rollback()
            raise OverflowError('Hourly audit limit reached')
        db.execute('INSERT INTO audit_rate_limits VALUES (?,?,1) ON CONFLICT(client_key,window) DO UPDATE SET count=count+1', (client_key, window))
        audit_id = uuid.uuid4().hex
        domain = urlsplit(url).hostname
        db.execute("INSERT INTO site_audits (audit_id,url,domain,status,progress,current_step) VALUES (?,?,?,'pending',0,'Queued')", (audit_id, url, domain))
        db.execute('INSERT INTO audit_jobs (audit_id,request_key,url,available_at,created_at) VALUES (?,?,?,?,?)',
                   (audit_id, request_key, url, now, now))
        db.commit()
        return audit_id


def claim(now=None):
    now = time.time() if now is None else now
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("""UPDATE site_audits SET status='error',error_message='Audit retry limit reached',overall_score=NULL
                      WHERE audit_id IN (SELECT audit_id FROM audit_jobs WHERE state='running' AND lease_until<? AND attempts>=3)""", (now,))
        db.execute("UPDATE audit_jobs SET state='failed',finished_at=? WHERE state='running' AND lease_until<? AND attempts>=3", (now, now))
        from denzo.runtime_limits import setting
        running=db.execute("SELECT COUNT(*) FROM audit_jobs WHERE state='running' AND lease_until>=?", (now,)).fetchone()[0]
        if running>=setting('DENZO_MAX_RUNNING_AUDITS',1,1,4):
            db.commit()
            return None
        row = db.execute("""SELECT * FROM audit_jobs WHERE attempts<3 AND
          ((state='queued' AND available_at<=?) OR (state='running' AND lease_until<?))
          ORDER BY created_at LIMIT 1""", (now, now)).fetchone()
        if not row:
            db.commit()
            return None
        token = uuid.uuid4().hex
        db.execute("UPDATE audit_jobs SET state='running',attempts=attempts+1,lease_token=?,lease_until=? WHERE audit_id=?", (token, now + 660, row['audit_id']))
        db.execute("UPDATE site_audits SET status='running',current_step='Fetching website',progress=5 WHERE audit_id=?", (row['audit_id'],))
        db.commit()
        return dict(row, lease_token=token)


def progress(job, percentage, step):
    with database() as db:
        db.execute("""UPDATE site_audits SET progress=?,current_step=? WHERE audit_id=? AND EXISTS
           (SELECT 1 FROM audit_jobs WHERE audit_id=? AND state='running' AND lease_token=?)""",
           (percentage, step[:200], job['audit_id'], job['audit_id'], job['lease_token']))
        db.commit()


def finish(job, result):
    failed = bool(result.get('error')) or result.get('status') == 'failed'
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        updated = db.execute("""UPDATE audit_jobs SET state=?,finished_at=? WHERE audit_id=?
              AND state='running' AND lease_token=? AND lease_until>?""",
              ('failed' if failed else 'completed', time.time(), job['audit_id'], job['lease_token'], time.time())).rowcount
        if updated:
            db.execute("""UPDATE site_audits SET status=?,progress=100,current_step=?,report_json=?,
               overall_score=?,module_scores=?,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE audit_id=?""",
               ('error' if failed else 'completed', 'Incomplete' if failed or not result.get('commercial_ready') else 'Complete',
                json.dumps(result, ensure_ascii=False, allow_nan=False),
                None if failed else result.get('overall_score'), json.dumps(result.get('module_scores', {})),
                result.get('error'), job['audit_id']))
        db.commit()
        return bool(updated)


def read_progress(audit_id):
    with database() as db:
        row = db.execute('SELECT status,progress,current_step FROM site_audits WHERE audit_id=?', (audit_id,)).fetchone()
        if not row:
            return {}
        return {'event': 'complete' if row['status'] == 'completed' else 'error' if row['status'] == 'error' else 'running',
                'progress': row['progress'], 'current_step': row['current_step'],
                'redirect': f'/auditor/report/{audit_id}'}


def heartbeat(worker_id, job_id=None):
    with database() as db:
        db.execute('INSERT INTO audit_worker_heartbeats VALUES(?,?,?) ON CONFLICT(worker_id) DO UPDATE SET seen_at=excluded.seen_at,job_id=excluded.job_id', (worker_id, time.time(), job_id))
        db.execute('DELETE FROM audit_worker_heartbeats WHERE seen_at<?', (time.time()-86400,))
        db.commit()


def health():
    with database() as db:
        worker = db.execute('SELECT max(seen_at) AS seen_at FROM audit_worker_heartbeats').fetchone()
        pending = db.execute("SELECT count(*) AS n FROM audit_jobs WHERE state IN ('queued','running')").fetchone()
        return {'worker_active': bool(worker['seen_at'] and worker['seen_at'] > time.time()-120), 'pending': pending['n']}
