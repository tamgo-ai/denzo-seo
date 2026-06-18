"""
Run GitHub Publisher for ACG — Next.js App Router format.
Publishes ready pages as page.jsx under app/[locale]/[type]/[slug]/.
"""
import sys, os

# CRITICAL: Unset DeepSeek proxy so Anthropic SDK hits api.anthropic.com directly.
for var in ['ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
            'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
            'ANTHROPIC_MODEL']:
    os.environ.pop(var, None)

sys.path.insert(0, '/root/denzo-seo')

from denzo.context.builder import build_client_context
from denzo.agents.layer4_publishing.github_publisher import GitHubPublisher
from denzo.agents.base_agent import db_execute, db_write

ctx = build_client_context('auto-collision-group')
if not ctx:
    print("ERROR: Could not build client context for ACG")
    sys.exit(1)

print(f"Client: {ctx.client_name}")
print(f"Publisher: github (format: {ctx.github_format})")
print(f"Repo: {ctx.github_repo} → branch: {ctx.github_branch}")
print(f"Domain: {ctx.pages_domain}")
print()

# ── Pre-flight: check which ready pages would collide with protected paths ──
import sqlite3
db_path = '/root/denzo-seo/data/denzo.db'

def check_status():
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    ready = db.execute(
        "SELECT COUNT(*) as cnt FROM pages WHERE tenant_id='auto-collision-group' "
        "AND status='ready' AND content IS NOT NULL AND content != ''"
    ).fetchone()
    pub = db.execute(
        "SELECT COUNT(*) as cnt FROM pages WHERE tenant_id='auto-collision-group' "
        "AND status='published'"
    ).fetchone()
    protected = db.execute(
        "SELECT COUNT(*) as cnt FROM managed_paths WHERE tenant_id='auto-collision-group' "
        "AND publisher='github' AND managed=0"
    ).fetchone()
    db.close()
    return ready['cnt'], pub['cnt'], protected['cnt']

before_ready, before_pub, protected_count = check_status()
print(f"Protected paths (managed=0): {protected_count}")
print(f"Before: {before_ready} ready, {before_pub} published")
print()

# ── Show which pages would be published ──
db = sqlite3.connect(db_path)
db.row_factory = sqlite3.Row
pages = db.execute(
    "SELECT id, title, slug, type FROM pages "
    "WHERE tenant_id='auto-collision-group' AND status='ready' "
    "AND content IS NOT NULL AND content != '' "
    "ORDER BY type, slug LIMIT 5"
).fetchall()
print(f"First 5 pages to publish ({len(pages)} shown):")
for p in pages:
    slug = p['slug']
    ptype = p['type']
    path = f"app/[locale]/{ptype}s/{slug}/page.jsx"
    print(f"  [{ptype:10s}] {p['title'][:60]:60s} → {path}")
db.close()
print()

# ── Execute publisher ──
print("Starting publisher...")
print()

agent = GitHubPublisher(ctx)
agent.run()

after_ready, after_pub, _ = check_status()
print(f"\nAfter: {after_ready} ready, {after_pub} published")
print(f"Published this run: {after_pub - before_pub}")
print("Done.")
