"""
Site Auditor — public SEO+GEO analysis tool.
No login required. Paste a URL, get a comprehensive audit report.
"""
import os
import uuid
import json
import time
import re
import math
import threading
from datetime import datetime
from urllib.parse import urlparse
from flask import Blueprint, request, jsonify, render_template, Response, send_file, redirect
from denzo.db import get_db

bp = Blueprint('site_auditor', __name__, url_prefix='/auditor')

# In-memory progress store for running analyses (SSE pushes from here)
_progress_store: dict[str, dict] = {}
_progress_lock = threading.Lock()

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


def _new_audit_id() -> str:
    return str(uuid.uuid4())[:12]


def _set_progress(audit_id: str, data: dict):
    with _progress_lock:
        # Always store so SSE stream can read it before cleanup
        _progress_store[audit_id] = data
        # Mark completed/error for deferred cleanup (SSE needs to read it first)
        if data.get('event') in ('complete', 'error'):
            data['_cleanup_after'] = time.time() + 30  # Keep for 30s so SSE can consume


def _get_progress(audit_id: str) -> dict:
    with _progress_lock:
        # Deferred cleanup: remove entries marked for cleanup after their TTL expires
        now = time.time()
        stale_keys = [k for k, v in _progress_store.items()
                      if not k.startswith('rate:') and v.get('_cleanup_after', float('inf')) < now]
        for k in stale_keys:
            _progress_store.pop(k, None)
        return _progress_store.get(audit_id, {})


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
        return _PROCESSING_HTML.replace('__STEP__', step).replace('__PROGRESS__', str(progress))

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
            data = _get_progress(audit_id)
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
    """Validate, rate-limit, and enqueue an analysis. Returns (audit_id, error, status_code)."""
    url = (url or '').strip()
    if not url:
        return None, 'URL is required', 400

    # Basic URL validation
    if len(url) > 500 or '<' in url or '>' in url:
        return None, 'Invalid URL', 400

    # Normalize URL
    if not url.startswith('http'):
        url = 'https://' + url

    # Rate limiting: max per hour per IP (configurable; raised for Droppin batch).
    max_per_hour = int(os.environ.get('AUDIT_RATE_LIMIT_PER_HOUR', '120'))
    now = time.time()
    with _progress_lock:
        # Clean old entries (older than 1 hour)
        for ip in list(_progress_store.keys()):
            if ip.startswith('rate:'):
                if now - _progress_store[ip] > 3600:
                    del _progress_store[ip]
        rate_key = f'rate:{client_ip}'
        count = _progress_store.get(rate_key, 0)
        if count >= max_per_hour:
            return None, f'Rate limit exceeded. Max {max_per_hour} analyses per hour.', 429
        _progress_store[rate_key] = count + 1

    audit_id = _new_audit_id()
    parsed_url = urlparse(url)
    domain = re.sub(r'^www\.', '', parsed_url.hostname or 'site')

    # Insert pending record
    db = get_db()
    db.execute(
        """INSERT INTO site_audits (audit_id, url, domain, status, progress, current_step)
           VALUES (?,?,?,'pending',0,'Queued')""",
        (audit_id, url, domain)
    )
    db.commit()

    # Launch analysis in background thread
    _set_progress(audit_id, {'event': 'started', 'progress': 0, 'current_step': 'Starting analysis...'})

    thread = threading.Thread(target=_run_analysis, args=(audit_id, url, domain), daemon=True)
    thread.start()

    return audit_id, None, None


@bp.route('/analyze', methods=['POST'])
def analyze():
    """Start a new site analysis. Returns audit_id for progress tracking."""
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    client_ip = request.remote_addr or 'unknown'

    audit_id, error, code = _normalize_and_enqueue(url, client_ip)
    if error:
        return jsonify({'error': error}), code

    return jsonify({'audit_id': audit_id, 'progress_url': f'/auditor/progress/{audit_id}'})


@bp.route('/go')
def go():
    """One-click entry: enqueue an analysis for ?url= and redirect to its report.
    Ideal for postcard QR codes — e.g. /auditor/go?url=https://example.com."""
    url = request.args.get('url', '')
    client_ip = request.remote_addr or 'unknown'

    audit_id, error, code = _normalize_and_enqueue(url, client_ip)
    if error:
        return render_template('site_auditor/index.html', error=error), code

    return redirect(f'/auditor/report/{audit_id}')


def _run_analysis(audit_id: str, url: str, domain: str):
    """Run all 5 analysis modules and save results. Called in background thread."""
    start_time = time.time()

    try:
        db = get_db()
        # Update status
        db.execute("UPDATE site_audits SET status='running', progress=5, current_step='Fetching page...' WHERE audit_id=?", (audit_id,))
        db.commit()
        _set_progress(audit_id, {'event': 'running', 'progress': 5, 'current_step': 'Fetching page...'})

        # Import inside thread to avoid circular imports
        from denzo.auditor.analyzer import SiteAnalyzer
        analyzer = SiteAnalyzer(url, domain, progress_callback=lambda p, step: _set_progress(
            audit_id, {'event': 'running', 'progress': p, 'current_step': step}
        ))

        result = analyzer.run_full_analysis()

        elapsed_ms = int((time.time() - start_time) * 1000)
        overall = result.get('overall_score', 0)
        module_scores = json.dumps(result.get('module_scores', {}))
        report_json = json.dumps(result, ensure_ascii=False)

        db.execute("""UPDATE site_audits SET status='completed', progress=100,
            report_json=?, overall_score=?, module_scores=?,
            fetch_method=?, page_title=?, page_status=?, html_size_kb=?, analysis_time_ms=?,
            updated_at=CURRENT_TIMESTAMP
            WHERE audit_id=?""",
            (report_json, overall, module_scores,
             result.get('fetch_method', ''), result.get('page_title', ''),
             result.get('page_status', 0), result.get('html_size_kb', 0),
             elapsed_ms, audit_id))
        db.commit()

        _set_progress(audit_id, {
            'event': 'complete',
            'progress': 100,
            'current_step': 'Done',
            'redirect': f'/auditor/report/{audit_id}',
            'overall_score': overall,
            'analysis_time_ms': elapsed_ms
        })

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        try:
            db = get_db()
            db.execute("UPDATE site_audits SET status='error', error_message=?, analysis_time_ms=?, updated_at=CURRENT_TIMESTAMP WHERE audit_id=?",
                       (str(e), elapsed_ms, audit_id))
            db.commit()
        except Exception:
            pass
        _set_progress(audit_id, {'event': 'error', 'progress': 0, 'current_step': 'Error', 'error': str(e)})
