"""
gunicorn_config.py — Production WSGI server for DENZO-SEO.

Usage:
    gunicorn -c gunicorn_config.py 'denzo:create_app()'

Uses gevent worker for flask-sock WebSocket compatibility.
Binds to 127.0.0.1:5055 (reverse proxy nginx in front).
"""

import os

# ── Bind ──────────────────────────────────────────────────────────────────────
bind = f"127.0.0.1:{os.getenv('DENZO_PORT', '5055')}"

# ── Workers ────────────────────────────────────────────────────────────────────
# gevent = async I/O, required for flask-sock WebSocket
worker_class = "gevent"
workers = 4
threads = 1
timeout = 120  # agents can take a while
graceful_timeout = 30

# ── Logging ───────────────────────────────────────────────────────────────────
accesslog = "-"  # stdout
errorlog = "-"
loglevel = "info"

# ── Process naming ─────────────────────────────────────────────────────────────
proc_name = "denzo-seo-web"

# ── Preload ────────────────────────────────────────────────────────────────────
# Preload app so all workers share the Anthropic client singleton.
# Must keep the rate limiter and DB connections thread-safe.
preload_app = True

# ── Hooks ──────────────────────────────────────────────────────────────────────
def on_starting(server):
    """Log startup."""
    print(f"[gunicorn] Starting DENZO-SEO on {bind} with {workers} {worker_class} workers")

def post_fork(server, worker):
    """Each worker gets fresh DB connection."""
    # Ensure SQLite is in WAL mode for each worker
    pass  # db.py handles this on first connect
