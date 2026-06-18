"""
Discovery script — scans pdx-prog/acg-web app/[locale]/ for existing pages
and populates managed_paths with managed=0 (protected, never overwrite).
"""
import os, sys, requests, json, base64, hashlib

# Unset DeepSeek proxy
for var in ['ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
            'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
            'ANTHROPIC_MODEL']:
    os.environ.pop(var, None)

sys.path.insert(0, '/root/denzo-seo')

from denzo.context.builder import build_client_context
from denzo.agents.base_agent import db_execute, db_write

ctx = build_client_context('auto-collision-group')
TENANT = ctx.tenant_id
REPO = ctx.github_repo
BRANCH = ctx.github_branch
TOKEN = ctx.github_token

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
})

def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def scan_dir(path: str, depth: int = 0):
    """Recursively scan a directory in the GitHub repo. Returns list of file paths."""
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            print(f"  {'  ' * depth}⚠ {path}: HTTP {r.status_code}")
            return []
        items = r.json()
        if not isinstance(items, list):
            print(f"  {'  ' * depth}⚠ {path}: not a list ({type(items).__name__})")
            return []
        files = []
        for item in items:
            if item['type'] == 'dir':
                files.extend(scan_dir(item['path'], depth + 1))
            elif item['type'] == 'file':
                files.append(item)
        return files
    except Exception as e:
        print(f"  {'  ' * depth}✗ {path}: {e}")
        return []

print(f"Scanning: {REPO} → app/[locale]/ (branch: {BRANCH})")
print()

# Scan the app/[locale] directory
all_files = scan_dir("app/[locale]")

# Filter for page.jsx files ONLY
page_files = [f for f in all_files if f['name'] == 'page.jsx' or f['name'] == 'page.tsx']

print(f"\nFound {len(page_files)} page files under app/[locale]/:")
print()

# Build managed_paths entries
protected = 0
newly_protected = 0

for pf in page_files:
    path = pf['path']
    # Determine page type from path
    # app/[locale]/services/auto-body-repair/page.jsx → type=service, slug=auto-body-repair
    # app/[locale]/locations/[slug]/page.jsx → type=location, slug=[slug] (dynamic)
    # app/[locale]/page.jsx → type=page, slug=home
    parts = path.split('/')
    # parts = ['app', '[locale]', ...rest, 'page.jsx']
    if len(parts) <= 3:
        # app/[locale]/page.jsx → home
        page_type = 'home'
        slug = 'home'
    elif len(parts) == 4:
        # app/[locale]/about-us/page.jsx → type=page, slug=about-us
        page_type = 'page'
        slug = parts[2]
    elif len(parts) == 5:
        # app/[locale]/services/auto-body-repair/page.jsx → type=service, slug=auto-body-repair
        # app/[locale]/locations/[slug]/page.jsx → type=location, slug=[slug]
        page_type = parts[2]  # 'services', 'locations', 'certifications'
        slug = parts[3]       # 'auto-body-repair', '[slug]'
    else:
        page_type = parts[2]
        slug = '/'.join(parts[3:-1])

    slug = slug.rstrip('/')

    # Get file content hash for idempotency
    content = ""
    if pf.get('download_url'):
        try:
            r = session.get(pf['download_url'], timeout=10)
            if r.status_code == 200:
                content = r.text
        except Exception:
            pass

    content_hash = compute_content_hash(content) if content else ""

    # Check if already in managed_paths
    existing = db_execute(
        "SELECT managed FROM managed_paths WHERE tenant_id=? AND publisher='github' AND path=?",
        (TENANT, path)
    )

    if existing:
        status = 'theirs' if existing[0]['managed'] == 0 else 'ours'
        print(f"  {'✓' if status == 'theirs' else '⚠'} [{page_type:15s}] {slug:40s} path={path} (existing: {status})")
        if status == 'theirs':
            protected += 1
    else:
        # Mark as protected (managed=0)
        db_write(
            "INSERT OR REPLACE INTO managed_paths (tenant_id, publisher, path, page_id, managed, content_hash) "
            "VALUES (?, 'github', ?, NULL, 0, ?)",
            (TENANT, path, content_hash)
        )
        print(f"  🛡 [{page_type:15s}] {slug:40s} path={path} → PROTECTED (managed=0)")
        newly_protected += 1
        protected += 1

print()
print(f"Total protected pages: {protected} ({newly_protected} newly added)")
print(f"These pages will NEVER be overwritten by DENZO SEO.")
print("Done.")
