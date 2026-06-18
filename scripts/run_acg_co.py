"""
Run Content Optimizer for ACG — improves low-quality pages using Claude.
Must be run AFTER the Anthropic client fix (base_url hardcoded to api.anthropic.com).
"""
import sys, os

# CRITICAL: Unset DeepSeek proxy so Anthropic SDK hits api.anthropic.com directly.
for var in ['ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
            'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
            'ANTHROPIC_MODEL']:
    os.environ.pop(var, None)

sys.path.insert(0, '/root/denzo-seo')

from denzo.context.builder import build_client_context
from denzo.agents.layer3_production.content_optimizer import ContentOptimizer
import sqlite3

ctx = build_client_context('auto-collision-group')
if not ctx:
    print("ERROR: Could not build client context for ACG")
    sys.exit(1)

print(f"Client: {ctx.client_name}")
print()

db_path = '/root/denzo-seo/data/denzo.db'

def check_status():
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    ready = db.execute(
        "SELECT COUNT(*) as cnt FROM pages WHERE tenant_id='auto-collision-group' "
        "AND status='ready' AND content IS NOT NULL AND content != ''"
    ).fetchone()['cnt']
    scored = db.execute(
        "SELECT COUNT(*) as cnt FROM pages WHERE tenant_id='auto-collision-group' "
        "AND status='ready' AND quality_score IS NOT NULL"
    ).fetchone()['cnt']
    db.close()
    return ready, scored

before_ready, before_scored = check_status()
print(f"Before: {before_ready} ready, {before_scored} scored ({before_ready - before_scored} unscored)")
print()

agent = ContentOptimizer(ctx)
agent.run()

after_ready, after_scored = check_status()
print(f"\nAfter: {after_ready} ready, {after_scored} scored")
print(f"Scored this run: {after_scored - before_scored}")
print("Done.")
