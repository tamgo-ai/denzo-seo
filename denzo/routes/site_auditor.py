"""
Site Auditor — versioned analysis with an authenticated, durable work queue.
Public analysis is disabled unless explicitly enabled by the operator.
"""
import os
import uuid
import json
import time
import re
import math
import hmac
import html
from urllib.parse import urlparse
from flask import Blueprint, request, jsonify, render_template, Response, send_file, redirect, session, g
from denzo.db import get_db as _get_db

bp = Blueprint('site_auditor', __name__, url_prefix='/auditor')

def get_db():
    db = _get_db()
    if not hasattr(g, 'audit_connections'):
        g.audit_connections = []
    g.audit_connections.append(db)
    return db


@bp.teardown_request
def close_audit_connections(_error=None):
    for db in g.pop('audit_connections', []):
        db.close()


# Standalone "still running" page with auto-refresh. Used by /auditor/report/<id>
# while an analysis is pending/running, so a postcard QR can land straight on it.
_PROCESSING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="3">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Analyzing… · DENZO Site Auditor</title>
<style>
  body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;
       background:#08090b;color:#edf0f5;font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;}
  .spinner{width:44px;height:44px;border:3px solid rgba(255,255,255,.12);border-top-color:#6366f1;
           border-radius:50%;animation:spin 0.9s linear infinite;}
  @keyframes spin{to{transform:rotate(360deg)}}
  .step{margin-top:1.4rem;color:#7b8290;font-size:0.95rem;}
  .pct{margin-top:0.4rem;color:#a78bfa;font-size:0.8rem;font-weight:600;}
</style>
</head>
<body>
  <div class="spinner"></div>
  <p class="step">__STEP__</p>
  <p class="pct">__PROGRESS__%</p>
</body>
</html>"""


@bp.route('/')
def index():
    """Main analyzer page — URL input form."""
    db = get_db()
    recent = db.execute(
        "SELECT * FROM site_audits WHERE status='completed' ORDER BY created_at DESC LIMIT 6"
    ).fetchall()
    return render_template('site_auditor/index.html', recent_audits=recent)


@bp.route('/history')
def history():
    """List all past audits, newest first."""
    db = get_db()
    domain_filter = request.args.get('url', '')
    rows = []
    if domain_filter:
        rows = db.execute(
            "SELECT * FROM site_audits WHERE url LIKE ? AND status='completed' ORDER BY created_at DESC LIMIT 50",
            (f'%{domain_filter}%',)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM site_audits WHERE status='completed' ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return render_template('site_auditor/history.html', audits=rows, filter_url=domain_filter)


def _report_mode(host: str) -> str:
    """droppin = lead-gen locked report; full = internal technical report."""
    return 'droppin' if 'droppin' in (host or '').lower() else 'full'


@bp.route('/report/<audit_id>')
def report(audit_id: str):
    """View a completed audit report."""
    db = get_db()
    audit = db.execute("SELECT * FROM site_audits WHERE audit_id=?", (audit_id,)).fetchone()
    if not audit:
        return render_template('site_auditor/index.html', error="Audit not found"), 404
    if audit['status'] == 'error':
        return render_template('site_auditor/index.html', error=audit['error_message'] or 'Analysis failed'), 500
    if audit['status'] != 'completed':
        step = audit['current_step'] or 'Analyzing your site...'
        progress = audit['progress'] if audit['progress'] is not None else 0
        return _PROCESSING_HTML.replace('__STEP__', html.escape(step)).replace('__PROGRESS__', str(progress))

    result = json.loads(audit['report_json']) if audit['report_json'] else {}
    mode = _report_mode(request.host)
    from denzo.auditor.report_builder import build_report_html
    report_html = build_report_html(result, audit_id, mode)
    return Response(report_html, mimetype='text/html')


@bp.route('/report/<audit_id>/json')
def report_json(audit_id: str):
    """Machine-readable audit result — used by the Droppin outreach pipeline.

    The status is validated before it is returned: an inaccessible page, an
    invalid score, or a report that could not be read all surface as
    ``failed`` so the pipeline never turns them into a sales score.
    """
    db = get_db()
    try:
        audit = db.execute("SELECT * FROM site_audits WHERE audit_id=?", (audit_id,)).fetchone()
    finally:
        db.close()
    if not audit:
        return jsonify({'error': 'Audit not found'}), 404

    try:
        details = json.loads(audit['report_json']) if audit['report_json'] else {}
    except (TypeError, ValueError):
        details = {'error': 'Audit report could not be read'}
    if not isinstance(details, dict):
        details = {'error': 'Invalid audit report'}

    status = audit['status']
    score = audit['overall_score'] if status == 'completed' else None
    valid_score = isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score) and 0 <= score <= 100
    page_status = details.get('page_status')
    inaccessible = isinstance(page_status, (int, float)) and not 200 <= page_status < 400
    if status == 'error' or (status == 'completed' and (details.get('error') or inaccessible or not valid_score)):
        status = 'failed'
        score = None
        details.setdefault('error', 'The page could not be reliably audited')

    if status == 'completed' and details.get('methodology_version') == 'droppin-audit-v3' and not details.get('commercial_ready'):
        status = 'partial'
        score = None

    payload = {
        'audit_id': audit['audit_id'],
        'url': audit['url'],
        'domain': audit['domain'],
        'status': status,
        'progress': audit['progress'],
        'overall_score': score,
        'module_scores': json.loads(audit['module_scores']) if audit['module_scores'] else {},
        'report': details,
    }
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route('/report/<audit_id>/download')
def download(audit_id: str):
    """Download standalone HTML report."""
    db = get_db()
    audit = db.execute("SELECT * FROM site_audits WHERE audit_id=?", (audit_id,)).fetchone()
    if not audit:
        return "Not found", 404

    result = json.loads(audit['report_json']) if audit['report_json'] else {}
    domain = audit['domain'] or 'site'
    filename = f"audit-{domain}-{audit['created_at'][:10]}.html"
    from denzo.auditor.report_builder import build_report_html
    report_html = build_report_html(result, audit_id, 'full', inline_assets=True)
    return Response(
        report_html,
        mimetype='text/html',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@bp.route('/report/<audit_id>/llms.txt')
def download_llms(audit_id: str):
    """Download generated llms.txt file."""
    db = get_db()
    audit = db.execute("SELECT * FROM site_audits WHERE audit_id=?", (audit_id,)).fetchone()
    if not audit:
        return "Not found", 404
    report = json.loads(audit['report_json']) if audit['report_json'] else {}
    llms_gen = report.get('llms_generated', {})
    llms_txt = llms_gen.get('llms_txt', '')
    if not llms_txt:
        return "No llms.txt generated for this audit", 404
    domain = audit['domain'] or 'site'
    return Response(llms_txt, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename="llms-{domain}.txt"'})


@bp.route('/report/<audit_id>/llms-full.txt')
def download_llms_full(audit_id: str):
    """Download generated llms-full.txt file."""
    db = get_db()
    audit = db.execute("SELECT * FROM site_audits WHERE audit_id=?", (audit_id,)).fetchone()
    if not audit:
        return "Not found", 404
    report = json.loads(audit['report_json']) if audit['report_json'] else {}
    llms_gen = report.get('llms_generated', {})
    llms_full = llms_gen.get('llms_full_txt', '')
    if not llms_full:
        return "No llms-full.txt generated for this audit", 404
    domain = audit['domain'] or 'site'
    return Response(llms_full, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename="llms-full-{domain}.txt"'})


@bp.route('/compare/<audit_a>/<audit_b>')
def compare(audit_a: str, audit_b: str):
    """Side-by-side comparison of two audits."""
    db = get_db()
    a = db.execute("SELECT * FROM site_audits WHERE audit_id=?", (audit_a,)).fetchone()
    b = db.execute("SELECT * FROM site_audits WHERE audit_id=?", (audit_b,)).fetchone()
    if not a or not b:
        return "One or both audits not found", 404

    # Parse module_scores JSON for template
    a_scores = json.loads(a['module_scores']) if a['module_scores'] else {}
    b_scores = json.loads(b['module_scores']) if b['module_scores'] else {}

    return render_template('site_auditor/compare.html', audit_a=a, audit_b=b, scores_a=a_scores, scores_b=b_scores)


@bp.route('/progress/<audit_id>')
def progress(audit_id: str):
    """SSE progress stream for a running analysis."""
    def stream():
        last_progress = -1
        # 600 iterations × 0.5s max = 300s = 5 minutes
        for _ in range(600):  # max 5 minutes
            from denzo.auditor.queue import read_progress
            data = read_progress(audit_id)
            if not data:
                yield f"data: {json.dumps({'event': 'waiting', 'progress': 0})}\n\n"
                time.sleep(0.5)
                continue

            progress = data.get('progress', 0)
            if progress != last_progress:
                yield f"data: {json.dumps(data)}\n\n"
                last_progress = progress

            if data.get('event') in ('complete', 'error'):
                return

            time.sleep(0.3)

    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def _normalize_and_enqueue(url: str, client_ip: str):
    from denzo.auditor.safe_fetch import validate_url
    from denzo.auditor.queue import enqueue
    token = os.environ.get('AUDIT_SERVICE_TOKEN', '')
    supplied = request.headers.get('Authorization', '')
    service = bool(token) and hmac.compare_digest(supplied, 'Bearer ' + token)
    if supplied and not service:
        return None, 'Invalid service credentials', 401
    if not service and not session.get('user_id') and os.environ.get('AUDIT_PUBLIC_ENABLED') != 'true':
        return None, 'Audit access requires authentication', 401
    if not service and request.headers.get('Origin') and request.headers['Origin'].rstrip('/') != request.host_url.rstrip('/'):
        return None, 'Invalid request origin', 403
    key = request.headers.get('Idempotency-Key', '')
    if service and not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', key):
        return None, 'A stable Idempotency-Key is required', 400
    try:
        normalized, _ = validate_url(url)
        limit = int(os.environ.get('AUDIT_SERVICE_LIMIT_PER_HOUR' if service else 'AUDIT_RATE_LIMIT_PER_HOUR', '1200' if service else '10'))
        audit_id = enqueue(normalized, ('service:' if service else 'public:') + (key or uuid.uuid4().hex),
                           'service' if service else client_ip, max(1, limit))
        return audit_id, None, None
    except OverflowError:
        return None, 'Hourly audit limit reached; try again later', 429
    except ValueError as exc:
        return None, str(exc), 400
    except OSError:
        return None, 'Website hostname could not be resolved', 400


@bp.route('/analyze', methods=['POST'])
def analyze():
    """Start a new site analysis. Returns audit_id for progress tracking."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('url'), str):
        return jsonify({'error': 'A website URL is required'}), 400
    url = data['url'].strip()
    client_ip = request.remote_addr or 'unknown'

    audit_id, error, code = _normalize_and_enqueue(url, client_ip)
    if error:
        return jsonify({'error': error}), code

    return jsonify({'audit_id': audit_id, 'progress_url': f'/auditor/progress/{audit_id}'})


@bp.route('/go')
def go():
    # GET must never create work. Printed QRs link to an existing frozen report.
    return redirect('/auditor/')


@bp.route('/health')
def health():
    token = os.environ.get('AUDIT_SERVICE_TOKEN', '')
    if not token or not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer '+token):
        return jsonify({'error': 'Authentication required'}), 401
    from denzo.auditor.queue import health as queue_health
    from denzo.auditor.scoring import METHODOLOGY_VERSION
    data = queue_health()
    data.update(methodology_version=METHODOLOGY_VERSION, pagespeed_configured=bool(os.environ.get('PAGESPEED_API_KEY')))
    return jsonify(data), 200 if data['worker_active'] and data['pagespeed_configured'] else 503
