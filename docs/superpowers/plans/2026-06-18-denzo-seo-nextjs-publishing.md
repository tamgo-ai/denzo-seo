# DENZO SEO Next.js App Router Publishing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar las 72 páginas `ready` de Auto Collision Group como page.jsx nativos de Next.js App Router en `pdx-prog/acg-web`, protegiendo las páginas existentes del repo.

**Architecture:** Next.js Renderer v2 genera page.jsx simplificados (sin hero/stats/gallery) bajo `app/[locale]/[type]/[slug]/`. El GitHub Publisher actualizado detecta formato nextjs y publica en la ruta correcta. Discovery script escanea el repo antes de publicar para proteger páginas existentes (managed=0).

**Tech Stack:** Python 3.12, SQLite (denzo.db), GitHub Contents API, Next.js 16 App Router + next-intl

---

### Task 1: Actualizar credenciales de GitHub en DB

**Files:**
- Modify: DB `client_context` table (vía SQL directo)

- [ ] **Step 1: Actualizar github_token y github_format para ACG**

```bash
cd /root/denzo-seo && python3 -c "
import sqlite3
from denzo.crypto import encrypt_token
import os

# Unset DeepSeek proxy
for var in ['ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
            'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
            'ANTHROPIC_MODEL']:
    os.environ.pop(var, None)

db = sqlite3.connect('data/denzo.db')
db.row_factory = sqlite3.Row

# Check current state
row = db.execute(\"SELECT github_token, github_format, encrypted FROM client_context WHERE tenant_id='auto-collision-group'\").fetchone()
print(f'Current format: {row[\"github_format\"]}')
print(f'Current token: {row[\"github_token\"][:30]}...')
print(f'Encrypted: {row[\"encrypted\"]}')

# Update to the working token + nextjs format
NEW_TOKEN = '<YOUR_GITHUB_PAT_HERE>'
encrypted_token = encrypt_token(NEW_TOKEN)

db.execute('''
    UPDATE client_context 
    SET github_token = ?, github_format = 'nextjs', encrypted = 1
    WHERE tenant_id = 'auto-collision-group'
''', (encrypted_token,))
db.commit()

# Verify
from denzo.context.builder import build_client_context
ctx = build_client_context('auto-collision-group')
print(f'New format: {ctx.github_format}')
print(f'New token valid: {ctx.github_token.startswith(\"ghp_\")}')
print(f'Repo: {ctx.github_repo}')
print(f'Branch: {ctx.github_branch}')
db.close()
"
```

Expected output:
```
Current format: html
Current token: gAAAAABqMFm6_5anLDnq4dOvI...
Encrypted: 1
New format: nextjs
New token valid: True
Repo: pdx-prog/acg-web
Branch: Raúl-Dev
```

- [ ] **Step 2: Verificar acceso al repo con el nuevo token**

```bash
cd /root/denzo-seo && python3 -c "
import os, sys
for var in ['ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
            'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
            'ANTHROPIC_MODEL']:
    os.environ.pop(var, None)
sys.path.insert(0, '.')

from denzo.context.builder import build_client_context
import requests

ctx = build_client_context('auto-collision-group')

session = requests.Session()
session.headers.update({
    'Authorization': f'Bearer {ctx.github_token}',
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28'
})

# Check repo
r = session.get(f'https://api.github.com/repos/{ctx.github_repo}', timeout=15)
print(f'Repo: HTTP {r.status_code}')
if r.status_code == 200:
    print(f'  Name: {r.json()[\"full_name\"]}')
    print(f'  Default branch: {r.json()[\"default_branch\"]}')

# Check branch
r2 = session.get(f'https://api.github.com/repos/{ctx.github_repo}/branches/{ctx.github_branch}', timeout=15)
print(f'Branch {ctx.github_branch}: HTTP {r2.status_code}')

# Verify write permission
r3 = session.get(f'https://api.github.com/repos/{ctx.github_repo}/collaborators/tamgo-ai/permission', timeout=15)
if r3.status_code == 200:
    print(f'Permission: {r3.json().get(\"permission\")}')
"
```

Expected: Repo HTTP 200, Branch HTTP 200, Permission: write/admin

---

### Task 2: Discovery script — Proteger páginas existentes del repo

**Files:**
- Create: `scripts/run_acg_discovery.py`

- [ ] **Step 1: Crear el script de discovery**

```python
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
```

- [ ] **Step 2: Ejecutar el discovery**

```bash
cd /root/denzo-seo && python3 scripts/run_acg_discovery.py
```

Expected: Listado de ~20-25 páginas protegidas (services, locations, certifications, about-us, faq, careers, etc.)

- [ ] **Step 3: Verificar en DB que managed_paths tiene los registros**

```bash
cd /root/denzo-seo && python3 -c "
import sqlite3
db = sqlite3.connect('data/denzo.db')
db.row_factory = sqlite3.Row
rows = db.execute('''
    SELECT path, managed FROM managed_paths 
    WHERE tenant_id='auto-collision-group' AND publisher='github'
    ORDER BY managed, path
''').fetchall()
print(f'Total managed_paths: {len(rows)}')
for r in rows:
    status = '🛡 PROTECTED' if r['managed'] == 0 else '✏️  ours'
    print(f'  {status:20s} {r[\"path\"]}')
db.close()
"
```

Expected: 15-25 paths all showing 🛡 PROTECTED (managed=0)

---

### Task 3: Next.js Renderer v2 — Simplificado para ACG Web

**Files:**
- Modify: `denzo/agents/layer4_publishing/nextjs_renderer.py` (rewrite completo)

- [ ] **Step 1: Reescribir render_nextjs_page()**

```python
"""
nextjs_renderer.py v2 — Genera page.jsx para Next.js App Router con i18n [locale].
Compatible con pdx-prog/acg-web (next-intl, layout compartido, Tailwind globals.css).

La página generada es un Server Component mínimo:
  - metadata export (SEO + OpenGraph)
  - Contenido HTML via dangerouslySetInnerHTML
  - Schema JSON-LD
  - Sin dependencia de next-intl (inglés directo)
"""
import json
import re
import hashlib
from denzo.agents.base_agent import ClientContext


def _component_name(slug: str) -> str:
    """Convert slug to PascalCase component name."""
    parts = re.split(r'[-_]', slug.strip("/"))
    return "".join(p.capitalize() for p in parts if p) or "DenzoPage"


def _extract_h1(content_html: str, fallback: str) -> str:
    """Extract H1 text from HTML content, return fallback if none found."""
    m = re.search(r'<h1[^>]*>(.*?)</h1>', content_html, re.DOTALL | re.IGNORECASE)
    if m:
        return re.sub(r'<[^>]+>', '', m.group(1)).strip()
    return fallback


def _clean_html_for_jsx(html: str) -> str:
    """
    Clean AI-generated HTML for embedding in JSX.
    - Remove wrapper divs/sections with class names from old template
    - Remove hero sections, CTA sections (layout provides those)
    - Remove H1 (we inject it as JSX)
    - Normalize whitespace
    """
    # Strip hero/CTA sections — layout provides them
    html = re.sub(
        r'<section[^>]*class="[^"]*(?:hero-section|cta-section)[^"]*"[^>]*>.*?</section>',
        '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Strip standalone H1 tags (will be rendered as JSX)
    html = re.sub(r'<h1[^>]*>.*?</h1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove script tags (schema is injected separately)
    html = re.sub(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>.*?</script>',
                  '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Strip wrapper classes from common patterns
    for wrapper_class in ['section-content', 'section-alt', 'two-col', 'col-text', 
                          'col-image', 'container', 'page-wrap', 'site-header',
                          'site-footer', 'breadcrumb']:
        html = re.sub(
            rf'<div[^>]*class="[^"]*{wrapper_class}[^"]*"[^>]*>',
            '', html, flags=re.IGNORECASE)
        html = re.sub(rf'</div>', '', html, count=1, flags=re.IGNORECASE) if wrapper_class in html else html
    
    # Clean up excessive newlines
    html = re.sub(r'\n{3,}', '\n\n', html)
    
    return html.strip()


def render_nextjs_page(page: dict, ctx: ClientContext, assets: dict = None) -> str:
    """
    Generate a Next.js App Router page.jsx as a Server Component.
    
    Output structure:
      export const metadata = { ... }
      export default function ComponentName() {
        return (
          <article className="prose ...">
            <h1>...</h1>
            <div dangerouslySetInnerHTML={{ __html: "..." }} />
            <script type="application/ld+json" ... />
          </article>
        );
      }
    """
    assets = assets or {}
    
    # Page data
    slug = (page.get("slug") or "").strip("/")
    meta_title = page.get("meta_title") or page.get("title") or ctx.client_name
    meta_desc = page.get("meta_description") or ""
    keyword = page.get("target_keyword") or ""
    content_html = page.get("content") or ""
    title = page.get("title") or meta_title
    page_type = page.get("type") or "page"
    location = page.get("location") or ctx.primary_city or ""
    
    # Domain resolution
    domain = ctx.pages_domain or (f"https://www.{ctx.domain}" if ctx.domain else "")
    canonical = f"{domain}/en/{page_type}s/{slug}" if domain else f"/en/{page_type}s/{slug}"
    
    # H1 from content
    h1_text = _extract_h1(content_html, title)
    
    # Clean content for JSX embedding
    clean_html = _clean_html_for_jsx(content_html)
    
    # Component name
    fn_name = _component_name(slug)
    
    # Primary color from assets
    primary = assets.get("primary_color", "#0b3950")
    
    # Schema.org (from page if exists, otherwise generate)
    schema_raw = page.get("schema_markup", "")
    if schema_raw:
        try:
            schema_obj = json.loads(schema_raw)
        except Exception:
            schema_obj = {
                "@context": "https://schema.org",
                "@type": "LocalBusiness",
                "name": ctx.client_name,
                "url": canonical,
                "telephone": ctx.phone,
                "description": meta_desc,
                "areaServed": location,
            }
    else:
        schema_obj = {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": ctx.client_name,
            "url": canonical,
            "telephone": ctx.phone,
            "description": meta_desc,
            "areaServed": location,
        }
    
    schema_json = json.dumps(schema_obj)
    
    # Escape HTML content for JSX embedding
    # The content goes inside a JSX string that gets passed to dangerouslySetInnerHTML
    # We need to escape backslashes, backticks, and ${} since it's inside a JSX template literal
    escaped_html = clean_html.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
    
    return f"""export const metadata = {{
  title: {json.dumps(meta_title)},
  description: {json.dumps(meta_desc)},
  keywords: {json.dumps(keyword)},
  alternates: {{
    canonical: {json.dumps(canonical)},
  }},
  openGraph: {{
    title: {json.dumps(meta_title)},
    description: {json.dumps(meta_desc)},
    url: {json.dumps(canonical)},
    siteName: {json.dumps(ctx.client_name)},
    locale: 'en_US',
    type: 'website',
  }},
  twitter: {{
    card: 'summary_large_image',
    title: {json.dumps(meta_title)},
    description: {json.dumps(meta_desc)},
  }},
}};

export default function {fn_name}() {{
  return (
    <article className="max-w-4xl mx-auto px-6 py-14
      [&_p]:mb-5 [&_p]:leading-relaxed [&_p]:text-gray-700 [&_p]:text-[1.05rem]
      [&_h2]:text-2xl [&_h2]:font-bold [&_h2]:text-[{primary}] [&_h2]:mt-12 [&_h2]:mb-4 [&_h2]:pb-2 [&_h2]:border-b [&_h2]:border-gray-100
      [&_h3]:text-xl [&_h3]:font-semibold [&_h3]:text-[{primary}] [&_h3]:mt-8 [&_h3]:mb-3
      [&_ul]:mb-6 [&_ul]:pl-6 [&_ul]:space-y-2
      [&_li]:text-gray-700 [&_li]:leading-relaxed [&_li]:list-disc
      [&_ol]:mb-6 [&_ol]:pl-6 [&_ol]:space-y-2 [&_ol_li]:list-decimal
      [&_strong]:font-semibold [&_strong]:text-gray-900
      [&_blockquote]:border-l-4 [&_blockquote]:border-[{primary}] [&_blockquote]:pl-5 [&_blockquote]:italic [&_blockquote]:text-gray-600 [&_blockquote]:my-6
      [&_a]:text-[{primary}] [&_a]:underline [&_a]:font-medium
      [&_details]:mb-4 [&_details]:border [&_details]:border-gray-200 [&_details]:rounded-lg [&_details]:overflow-hidden
      [&_summary]:cursor-pointer [&_summary]:font-semibold [&_summary]:text-[{primary}] [&_summary]:px-5 [&_summary]:py-4 [&_summary]:bg-gray-50
      [&_details_p]:px-5 [&_details_p]:py-4 [&_details_p]:mb-0
      [&_table]:w-full [&_table]:border-collapse [&_table]:mb-6
      [&_th]:bg-[{primary}] [&_th]:text-white [&_th]:px-4 [&_th]:py-2 [&_th]:text-left
      [&_td]:border [&_td]:border-gray-200 [&_td]:px-4 [&_td]:py-2">
      
      <h1 className="text-3xl md:text-4xl font-bold text-[{primary}] mb-8 pb-4 border-b-2 border-gray-200">
        {json.dumps(h1_text)}
      </h1>
      
      <div dangerouslySetInnerHTML={{{{ __html: `{escaped_html}` }}}} />
      
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{{{ __html: {json.dumps(schema_json)} }}}}
      />
    </article>
  );
}}
"""
```

- [ ] **Step 2: Verificar que el renderer compila correctamente**

```bash
cd /root/denzo-seo && python3 -c "
import os, sys
for var in ['ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
            'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_MODEL']:
    os.environ.pop(var, None)
sys.path.insert(0, '.')

from denzo.context.builder import build_client_context
from denzo.agents.layer4_publishing.nextjs_renderer import render_nextjs_page

ctx = build_client_context('auto-collision-group')

# Test with sample data
sample_page = {
    'slug': 'test-collision-repair-near-me',
    'title': 'Collision Repair Near Me in Whittier CA',
    'meta_title': 'Collision Repair Near Me — Auto Collision Group Whittier',
    'meta_description': 'Certified collision repair in Whittier CA. OEM parts, lifetime warranty. Free estimates!',
    'target_keyword': 'collision repair near me Whittier CA',
    'content': '<h1>Collision Repair Near Me in Whittier CA</h1><p>When your vehicle needs expert collision repair in Whittier, trust Auto Collision Group.</p><h2>Why Choose ACG?</h2><p>We use OEM parts exclusively.</p>',
    'type': 'service',
    'location': 'Whittier',
    'schema_markup': '{\"@context\": \"https://schema.org\", \"@type\": \"LocalBusiness\", \"name\": \"Auto Collision Group\"}',
}

result = render_nextjs_page(sample_page, ctx)
print(result[:1500])
print('...')
print(f'Total length: {len(result)} chars')
"
```

Expected: Valid JSX string with metadata, H1, content, schema. No Python errors.

---

### Task 4: Actualizar GitHub Publisher para paths Next.js

**Files:**
- Modify: `denzo/agents/layer4_publishing/github_publisher.py:385-412` (sección de formato)

- [ ] **Step 1: Actualizar la lógica de path en el método run()**

En el archivo `github_publisher.py`, reemplazar el bloque dentro del `for page in pages:` que determina `file_path` para el formato nextjs:

```python
# En github_publisher.py, dentro del for page in pages:, reemplazar líneas 389-396:

if fmt == "nextjs":
    file_content = render_nextjs_page(page_dict, ctx, nextjs_assets)
    # Path: app/[locale]/[type]/[slug]/page.jsx
    ptype_plural = f"{ptype}s" if not ptype.endswith('s') else ptype
    file_path    = f"app/[locale]/{ptype_plural}/{slug}/page.jsx"
    public_url   = f"{_base}/en/{ptype_plural}/{slug}" if _base else f"/en/{ptype_plural}/{slug}"
else:
    # HTML format (existing logic)
    file_path  = f"{path_prefix}{ptype}s/{slug}.html"
    public_url = f"{_base}/{ptype}s/{slug}.html" if _base else file_path
    file_content = _build_html_page(
        title=title,
        meta_description=meta_desc,
        content=content,
        style_guide=style_guide,
        ctx=ctx,
        canonical_url=public_url,
    )
```

- [ ] **Step 2: Ajustar el quality gate para formato nextjs**

El quality gate debe ejecutarse sobre el contenido HTML crudo (antes del wrapper JSX) para el formato nextjs. Modificar la sección de quality gate:

```python
# Justo antes del quality gate (antes de la llamada a validate_page_quality):
if fmt == "nextjs":
    # Para JSX, validamos el HTML crudo (sin el wrapper JSX)
    # validate_page_quality espera HTML — le pasamos el content original
    raw_content_for_qc = f"<h1>{title}</h1>\n{content}"
    issues = validate_page_quality(raw_content_for_qc, ptype, base_url=public_url, domain=ctx.pages_domain or ctx.domain)
else:
    issues = validate_page_quality(file_content, ptype, base_url=public_url, domain=ctx.pages_domain or ctx.domain)
```

- [ ] **Step 3: Verificar que la lógica de protection/discovery se ejecuta antes de publicar**

El discovery ya se ejecutó en Task 2. El publisher usa `_check_path_ownership()` que consulta `managed_paths`. Para la primera ejecución con nextjs, todas las rutas serán `'unknown'` (no en managed_paths) o `'ours'`. Como ya poblamos managed_paths con las páginas existentes del repo, el publisher las respetará.

Verificar que el método `_check_path_ownership` se llama en `_publish_file` (ya lo hace, línea 213).

---

### Task 5: Actualizar run_acg_publisher.py

**Files:**
- Modify: `scripts/run_acg_publisher.py`

- [ ] **Step 1: Añadir paso de discovery previo y mejor logging**

```python
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
```

- [ ] **Step 2: Dry-run con 2 páginas (ajustar MAX_PAGES_PER_DAY temporalmente)**

```bash
cd /root/denzo-seo && python3 -c "
import sqlite3
db = sqlite3.connect('data/denzo.db')
# Set publish velocity to 2 for testing
db.execute(\"INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES ('auto-collision-group', 'publish_velocity', '{\\\"max_per_day\\\": 2, \\\"min_delay\\\": 1, \\\"max_delay\\\": 2}')\")
db.commit()
print('Velocity set to 2 pages for testing')
db.close()
"
```

```bash
cd /root/denzo-seo && python3 scripts/run_acg_publisher.py
```

Expected: 2 pages published, showing the file paths like `app/[locale]/services/.../page.jsx`

---

### Task 6: Verificar páginas publicadas en GitHub

**Files:**
- No modifications — verification only

- [ ] **Step 1: Verificar que los archivos se subieron correctamente**

```bash
cd /root/denzo-seo && python3 -c "
import os, sys, requests
for var in ['ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
            'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
            'ANTHROPIC_MODEL']:
    os.environ.pop(var, None)
sys.path.insert(0, '.')

from denzo.context.builder import build_client_context

ctx = build_client_context('auto-collision-group')

session = requests.Session()
session.headers.update({
    'Authorization': f'Bearer {ctx.github_token}',
    'Accept': 'application/vnd.github+json',
})

# Check published pages in DB
import sqlite3
db = sqlite3.connect('data/denzo.db')
db.row_factory = sqlite3.Row
published = db.execute('''
    SELECT title, slug, type, publish_url 
    FROM pages 
    WHERE tenant_id='auto-collision-group' AND status='published'
    ORDER BY published_at DESC LIMIT 5
''').fetchall()

print('Recently published:')
for p in published:
    print(f'  [{p[\"type\"]:10s}] {p[\"title\"][:60]}')
    print(f'           URL: {p[\"publish_url\"]}')
    # Verify file exists on GitHub
    ptype = p['type']
    slug = p['slug']
    file_path = f\"app/[locale]/{ptype}s/{slug}/page.jsx\"
    r = session.get(f'https://api.github.com/repos/{ctx.github_repo}/contents/{file_path}?ref={ctx.github_branch}')
    print(f'           GitHub: HTTP {r.status_code} — {file_path}')
    print()

db.close()
"
```

Expected: HTTP 200 for each published file path

- [ ] **Step 2: Revisar el contenido de un page.jsx publicado**

```bash
# Pick the first published page and view its content
cd /root/denzo-seo && python3 -c "
import os, sys, requests, base64
for var in ['ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
            'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
            'ANTHROPIC_MODEL']:
    os.environ.pop(var, None)
sys.path.insert(0, '.')

from denzo.context.builder import build_client_context
ctx = build_client_context('auto-collision-group')

import sqlite3
db = sqlite3.connect('data/denzo.db')
db.row_factory = sqlite3.Row
p = db.execute('''
    SELECT title, slug, type FROM pages 
    WHERE tenant_id='auto-collision-group' AND status='published'
    ORDER BY published_at DESC LIMIT 1
''').fetchone()
db.close()

session = requests.Session()
session.headers.update({
    'Authorization': f'Bearer {ctx.github_token}',
    'Accept': 'application/vnd.github+json',
})

ptype = p['type']
slug = p['slug']
file_path = f'app/[locale]/{ptype}s/{slug}/page.jsx'
r = session.get(f'https://api.github.com/repos/{ctx.github_repo}/contents/{file_path}?ref={ctx.github_branch}')
content = base64.b64decode(r.json()['content']).decode()
print(content[:2000])
print('...')
"
```

Expected: Valid JSX with metadata export, default function component, H1, dangerouslySetInnerHTML, schema script.

---

### Task 7: Publicación completa de las 72 páginas

**Files:**
- No modifications — execution only

- [ ] **Step 1: Restaurar velocity normal y publicar todas**

```bash
cd /root/denzo-seo && python3 -c "
import sqlite3
db = sqlite3.connect('data/denzo.db')
# Remove test velocity setting
db.execute(\"DELETE FROM settings WHERE tenant_id='auto-collision-group' AND key='publish_velocity'\")
db.commit()
print('Velocity reset to defaults (30 pages/day)')
db.close()
"
```

```bash
cd /root/denzo-seo && python3 scripts/run_acg_publisher.py
```

Expected: ~30 pages published (daily limit). Las restantes quedarán en `ready` para la siguiente ejecución.

- [ ] **Step 2: Ejecutar segunda tanda para las páginas restantes** (siguiente día simulado)

```bash
# Override daily limit para publicar el resto
cd /root/denzo-seo && python3 -c "
import sqlite3
db = sqlite3.connect('data/denzo.db')
# Temp override: set limit to 100 for final batch
db.execute(\"INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES ('auto-collision-group', 'publish_velocity', '{\\\"max_per_day\\\": 100, \\\"min_delay\\\": 3, \\\"max_delay\\\": 8}')\")
db.commit()
db.close()
"
```

```bash
cd /root/denzo-seo && python3 scripts/run_acg_publisher.py
```

Expected: Remaining ~42 pages published. Total ~72 published.

- [ ] **Step 3: Estado final**

```bash
cd /root/denzo-seo && python3 -c "
import sqlite3
db = sqlite3.connect('data/denzo.db')
db.row_factory = sqlite3.Row
print('=== FINAL STATUS ===')
rows = db.execute(\"SELECT status, type, COUNT(*) as cnt FROM pages WHERE tenant_id='auto-collision-group' GROUP BY status, type ORDER BY status, type\").fetchall()
for r in rows:
    print(f'  {r[\"status\"]:20s} {r[\"type\"]:15s} {r[\"cnt\"]}')
print()
pub = db.execute(\"SELECT COUNT(*) as cnt FROM pages WHERE tenant_id='auto-collision-group' AND status='published'\").fetchone()
ready = db.execute(\"SELECT COUNT(*) as cnt FROM pages WHERE tenant_id='auto-collision-group' AND status='ready'\").fetchone()
print(f'Published: {pub[\"cnt\"]}')
print(f'Remaining ready: {ready[\"cnt\"]}')
db.close()
"
```

---

### Task 8 (opcional): Restaurar velocity a defaults

- [ ] **Step 1: Limpiar configuración temporal**

```bash
cd /root/denzo-seo && python3 -c "
import sqlite3
db = sqlite3.connect('data/denzo.db')
db.execute(\"DELETE FROM settings WHERE tenant_id='auto-collision-group' AND key='publish_velocity'\")
db.commit()
print('Velocity settings cleaned — back to defaults (30 pages/day)')
db.close()
"
```

---

## Resumen de ejecución

Orden recomendado:
1. **Task 1** → Token y formato actualizados
2. **Task 2** → Discovery (proteger páginas existentes)
3. **Task 3** → Renderer v2 (verificar compilación)
4. **Task 4** → Publisher paths nextjs
5. **Task 5** → Script de ejecución + dry-run con 2 páginas
6. **Task 6** → Verificar en GitHub
7. **Task 7** → Publicar las 72 (en 2 tandas)
8. **Task 8** → Limpiar settings temporales
