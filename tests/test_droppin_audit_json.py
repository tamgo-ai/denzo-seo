"""Exercise the real Flask route with a temporary SQLite database, without AI calls."""
import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest
from flask import Flask


@pytest.fixture
def audit_api(tmp_path, monkeypatch):
    database = tmp_path / 'audit.db'
    def connect():
        conn = sqlite3.connect(database)
        conn.row_factory = sqlite3.Row
        return conn
    with connect() as conn:
        conn.execute('CREATE TABLE site_audits (audit_id TEXT, url TEXT, domain TEXT, status TEXT, progress INTEGER, overall_score REAL, module_scores TEXT, report_json TEXT)')
    stub = types.ModuleType('denzo.db')
    stub.get_db = connect
    monkeypatch.setitem(sys.modules, 'denzo.db', stub)
    path = Path(__file__).resolve().parents[1] / 'denzo/routes/site_auditor.py'
    spec = importlib.util.spec_from_file_location('droppin_auditor_test_module', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('AUDIT_SERVICE_TOKEN', 'test-service-secret')
    app = Flask(__name__)
    app.secret_key = 'test-secret'
    app.register_blueprint(module.bp)
    client = app.test_client()
    client.environ_base['HTTP_AUTHORIZATION'] = 'Bearer test-service-secret'
    return client, connect


@pytest.mark.parametrize('status,score,report,expected', [
    ('pending', None, {}, 'pending'),
    ('running', None, {}, 'running'),
    ('completed', 82, {'page_status': 200, 'issues': ['Missing description']}, 'completed'),
    ('completed', 0, {'page_status': 200}, 'completed'),
    ('completed', 0, {'error': 'Fetch failed'}, 'failed'),
    ('completed', 0, {'page_status': 503}, 'failed'),
    ('completed', None, {}, 'failed'),
    ('completed', 101, {}, 'failed'),
    ('error', None, {}, 'failed'),
])
def test_polling_contract(audit_api, status, score, report, expected):
    client, connect = audit_api
    with connect() as conn:
        conn.execute('INSERT INTO site_audits VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     ('audit-1', 'https://example.com', '', status, 100, score, None, json.dumps(report)))
    response = client.get('/auditor/report/audit-1/json')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == expected
    assert data['overall_score'] == (score if expected == 'completed' else None)
    assert data['url'] == 'https://example.com'
    if expected == 'completed':
        assert data['report'] == report
    assert response.headers['Cache-Control'] == 'no-store'


def test_unknown_audit_is_404(audit_api):
    client, _ = audit_api
    assert client.get('/auditor/report/missing/json').status_code == 404
