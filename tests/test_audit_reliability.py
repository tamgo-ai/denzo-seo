import json
import sqlite3
import socket
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from denzo.auditor import pagespeed_real, performance_estimator, scoring, queue, safe_fetch


def psi(score):
    return {'lighthouseResult': {'categories': {'performance': {'score': score}},
            'audits': {'total-blocking-time': {'numericValue': 0}, 'cumulative-layout-shift': {'numericValue': 0}}},
            'loadingExperience': {'metrics': {'CUMULATIVE_LAYOUT_SHIFT_SCORE': {'percentile': 3, 'category': 'FAST'}}}}


@pytest.mark.parametrize('raw,expected', [(0.95,95),(0,0),(1,100),(None,None),(95,None)])
def test_lighthouse_scale_and_zero(raw, expected, monkeypatch):
    parsed = pagespeed_real._parse_psi_response(psi(raw))
    assert parsed['score'] == expected
    assert parsed['field_data']['cumulative_layout_shift_score']['percentile'] == 0.03
    monkeypatch.setattr(performance_estimator, 'get_lighthouse_performance', lambda _: parsed)
    result = performance_estimator.estimate_performance('https://example.com', '', '')
    assert result['score'] == expected
    if expected is not None:
        assert result['lab_data']['tbt']['numeric_value'] == 0


def modules():
    return {name: {'score': 95, 'source': 'lighthouse_local'} for name in scoring.BASE_WEIGHTS}


def test_unavailable_is_never_a_bad_score():
    data = modules()
    data['technical'] = {'score': 0, 'error': 'fetch failed'}
    result = scoring.score_results(data)
    assert result['overall_score'] == 95
    assert result['module_scores']['technical'] is None
    assert result['coverage'] == 70
    assert not result['commercial_ready']


def test_concurrent_local_weights_do_not_leak():
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda local: scoring.score_results(modules(), local), [True,False]*50))
    for n, result in enumerate(results):
        assert result['scoring_weights']['technical'] == 40
        assert result['commercial_ready']
    assert scoring.BASE_WEIGHTS['local_seo'] == 0


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    filename = tmp_path/'audits.db'
    def connect():
        db = sqlite3.connect(filename, timeout=10)
        db.row_factory = sqlite3.Row
        return db
    with connect() as db:
        db.execute('''CREATE TABLE site_audits (audit_id TEXT PRIMARY KEY,url TEXT,domain TEXT,status TEXT,
          progress INTEGER,current_step TEXT,error_message TEXT,overall_score REAL,report_json TEXT,
          module_scores TEXT,updated_at TEXT,fetch_method TEXT,page_status TEXT,page_title TEXT,
          html_size_kb INTEGER,analysis_time_ms INTEGER)''')
    monkeypatch.setattr(queue, 'get_db', connect)
    return connect


def test_persistent_idempotency_rate_limit_and_lease_fencing(audit_db,monkeypatch):
    monkeypatch.setenv('DENZO_MAX_RUNNING_AUDITS','2')
    first = queue.enqueue('https://example.com/', 'stable-1', 'client', 2)
    assert queue.enqueue('https://example.com/', 'stable-1', 'client', 2) == first
    with pytest.raises(ValueError):
        queue.enqueue('https://other.com/', 'stable-1', 'client', 2)
    second = queue.enqueue('https://other.com/', 'stable-2', 'client', 2)
    with pytest.raises(OverflowError):
        queue.enqueue('https://third.com/', 'stable-3', 'client', 2)
    job = queue.claim()
    other = queue.claim()
    assert {job['audit_id'], other['audit_id']} == {first, second}
    assert queue.claim() is None
    with audit_db() as db:
        db.execute('UPDATE audit_jobs SET lease_until=0 WHERE audit_id=?', (job['audit_id'],))
    replacement = queue.claim()
    assert replacement['lease_token'] != job['lease_token']
    result = dict(scoring.score_results(modules()), checked_at='2026-09-13T00:00:00Z')
    assert not queue.finish(job, result)
    assert queue.finish(replacement, result)
    assert queue.read_progress(first)['event'] == 'complete'


def test_audit_worker_concurrency_has_a_shared_limit(audit_db,monkeypatch):
    monkeypatch.setenv('DENZO_MAX_RUNNING_AUDITS','1')
    queue.enqueue('https://example.com/','first','client')
    queue.enqueue('https://other.com/','second','client')
    first=queue.claim()
    assert first and queue.claim() is None
    assert queue.finish(first,{'status':'failed','error':'Test failure'})
    assert queue.claim()['audit_id']!=first['audit_id']


@pytest.mark.parametrize('url', ['http://localhost','http://127.0.0.1','http://169.254.169.254/latest/meta-data','file:///etc/passwd','https://user:password@example.com','https://example.com:22'])
def test_private_targets_are_rejected(url, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',80))])
    with pytest.raises(ValueError):
        safe_fetch.validate_url(url)


def test_redirect_to_private_address_never_connects(monkeypatch):
    called = []
    def validate(url):
        if 'private' in url:
            raise ValueError('private target')
        return url, '93.184.216.34'
    class Connection:
        def __init__(self,*a,**k): called.append(a[0])
        def request(self,*a,**k): pass
        def getresponse(self):
            return SimpleNamespace(status=302, getheaders=lambda: [], getheader=lambda _: 'http://private/')
        def close(self): pass
    monkeypatch.setattr(safe_fetch,'validate_url',validate)
    monkeypatch.setattr(safe_fetch.http.client,'HTTPConnection',Connection)
    monkeypatch.setattr(safe_fetch.socket,'create_connection',lambda *a,**k: object())
    with pytest.raises(ValueError):
        safe_fetch.fetch_html('http://example.com/')
    assert called == ['93.184.216.34']


def test_partial_report_has_no_grade_or_hidden_claims():
    from denzo.auditor.report_builder import build_report_html
    report = build_report_html({'overall_score': 70,'methodology_version': scoring.METHODOLOGY_VERSION,'commercial_ready':False},'id','droppin')
    assert 'Audit incomplete' in report
    assert '70/100' not in report
    assert 'leaking customers' not in report


def test_service_authentication_key_binding_and_health(audit_db, monkeypatch):
    from flask import Flask
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('audit_service_auth_test',
        Path(__file__).resolve().parents[1] / 'denzo/routes/site_auditor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_get_db', audit_db)
    monkeypatch.setattr(safe_fetch, 'validate_url', lambda url: (url, '93.184.216.34'))
    monkeypatch.setenv('AUDIT_SERVICE_TOKEN', 'test-service-secret')
    monkeypatch.delenv('AUDIT_PUBLIC_ENABLED', raising=False)
    monkeypatch.delenv('PAGESPEED_API_KEY', raising=False)
    app = Flask(__name__)
    app.secret_key = 'local-fixture'
    app.register_blueprint(module.bp)
    client = app.test_client()
    payload = {'url': 'https://example.com/'}
    auth = {'Authorization': 'Bearer test-service-secret'}
    assert client.post('/auditor/analyze', json=payload).status_code == 401
    assert client.post('/auditor/analyze', json=payload, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert client.post('/auditor/analyze', json=payload, headers=auth).status_code == 400
    headers = dict(auth, **{'Idempotency-Key': 'request-stable-123456'})
    first = client.post('/auditor/analyze', json=payload, headers=headers)
    assert first.status_code == 200
    assert client.post('/auditor/analyze', json=payload, headers=headers).json == first.json
    assert client.post('/auditor/analyze', json={'url': 'https://other.example/'}, headers=headers).status_code == 400
    assert client.get('/auditor/go?url=https://unrequested.example/').status_code == 302
    assert queue.health()['pending'] == 1
    assert client.get('/auditor/health').status_code == 401
    assert client.get('/auditor/health', headers=auth).status_code == 503
    queue.heartbeat('test-worker')
    monkeypatch.setenv('PAGESPEED_API_KEY', 'local-fixture-key')
    assert client.get('/auditor/health', headers=auth).status_code == 200
