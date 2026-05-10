import csv
import io
from flask import Blueprint, render_template, request, abort, Response, redirect, url_for, flash, jsonify
from denzo.auth import login_required, can_access_tenant
from denzo.db import get_db


def _is_dark(hex_color: str) -> bool:
    """Return True if a hex color is dark (luminance < 0.35)."""
    try:
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return luminance < 0.35
    except Exception:
        return False

bp = Blueprint("pages", __name__, url_prefix="/clients/<tenant_id>")

PAGE_SIZE = 50


def _get_sidebar_clients():
    db = get_db()
    rows = db.execute("""
        SELECT c.tenant_id, c.name, ag.name AS active_agent_name
        FROM clients c
        LEFT JOIN agents ag ON ag.tenant_id = c.tenant_id AND ag.status = 'working'
        GROUP BY c.tenant_id
        ORDER BY c.name
    """).fetchall()
    clients = [
        {"tenant_id": r["tenant_id"], "name": r["name"], "active_agent": r["active_agent_name"]}
        for r in rows
    ]
    db.close()
    return clients


@bp.route("/pages")
@login_required
def index(tenant_id):
    if not can_access_tenant(tenant_id):
        abort(403)
    db = get_db()

    client = db.execute(
        "SELECT name FROM clients WHERE tenant_id=?", (tenant_id,)
    ).fetchone()
    if not client:
        db.close()
        abort(404)

    status_filter = request.args.get("status", "").strip()
    type_filter   = request.args.get("type", "").strip()
    page          = max(1, int(request.args.get("page", 1)))

    conditions = ["tenant_id = ?"]
    params = [tenant_id]

    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)
    if type_filter:
        conditions.append("type = ?")
        params.append(type_filter)

    where = " AND ".join(conditions)

    total = db.execute(
        f"SELECT COUNT(*) FROM pages WHERE {where}", params
    ).fetchone()[0]

    offset = (page - 1) * PAGE_SIZE
    pages_rows = db.execute(
        f"SELECT * FROM pages WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, offset]
    ).fetchall()

    # Status counts
    counts = db.execute(
        """SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status='draft'     THEN 1 ELSE 0 END) AS draft,
            SUM(CASE WHEN status='ready'     THEN 1 ELSE 0 END) AS ready,
            SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
           FROM pages WHERE tenant_id=?""",
        (tenant_id,)
    ).fetchone()

    # Distinct page types
    types = db.execute(
        "SELECT DISTINCT type FROM pages WHERE tenant_id=? AND type IS NOT NULL ORDER BY type",
        (tenant_id,)
    ).fetchall()

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    clients = _get_sidebar_clients()
    db.close()

    return render_template(
        "pages/index.html",
        pages=[dict(p) for p in pages_rows],
        client=dict(client),
        tenant_id=tenant_id,
        active_tenant=tenant_id,
        clients=clients,
        counts=dict(counts) if counts else {"total": 0, "draft": 0, "ready": 0, "published": 0},
        total=total,
        page=page,
        total_pages=total_pages,
        page_types=[r["type"] for r in types],
        filters={"status": status_filter, "type": type_filter},
    )


@bp.route("/pages/<int:page_id>/preview")
@login_required
def preview(tenant_id, page_id):
    db = get_db()
    page_row = db.execute(
        "SELECT * FROM pages WHERE id=? AND tenant_id=?", (page_id, tenant_id)
    ).fetchone()
    db.close()

    if not page_row:
        abort(404)

    content = page_row["content"] or ""
    if not content.strip().startswith("<!DOCTYPE") and not content.strip().startswith("<html"):
        # Wrap bare HTML content in a styled preview
        import json as _json
        from html import escape as _esc
        _cdb = get_db()
        client_row = _cdb.execute(
            "SELECT name, phone FROM clients WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        style_row = _cdb.execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='site_style_guide'",
            (tenant_id,)
        ).fetchone()
        _cdb.close()
        client_name  = _esc(client_row["name"]  if client_row else "")
        client_phone = _esc(client_row["phone"] if client_row else "")

        # Extract brand colors from style guide, fall back to Denzo orange
        brand_color = "#f97316"
        brand_dark  = "#0f172a"
        brand_color_hover = "#ea6a0a"
        if style_row:
            try:
                sg = _json.loads(style_row["value"])
                primaries = sg.get("primary_colors", [])
                darks     = [c for c in primaries if _is_dark(c)]
                lights    = [c for c in primaries if not _is_dark(c)]
                if lights:
                    brand_color       = lights[0]
                    brand_color_hover = lights[0]
                elif primaries:
                    brand_color       = primaries[0]
                    brand_color_hover = primaries[0]
                if darks:
                    brand_dark = darks[0]
                elif len(primaries) > 1:
                    brand_dark = primaries[-1]
            except Exception:
                pass

        content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_esc(page_row['title'] or 'Page Preview')}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --brand:       {brand_color};
      --brand-hover: {brand_color_hover};
      --brand-dark:  {brand_dark};
      --brand-faint: color-mix(in srgb, {brand_color} 10%, white);
      --brand-muted: color-mix(in srgb, {brand_color} 20%, white);
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    /* ── Preview bar ── */
    .denzo-preview-bar {{
      position: sticky; top: 0; z-index: 9999;
      background: #1e293b; color: #f8fafc;
      padding: 10px 20px;
      display: flex; align-items: center; justify-content: space-between;
      font-family: 'Inter', system-ui, sans-serif; font-size: 12px;
      border-bottom: 3px solid var(--brand);
    }}
    .denzo-preview-bar .badge {{
      background: var(--brand); color: #fff;
      padding: 2px 8px; border-radius: 4px; font-weight: 600;
      margin-right: 8px; font-size: 11px; text-transform: uppercase; letter-spacing: .5px;
    }}
    .denzo-preview-bar .meta {{ color: #94a3b8; }}
    .denzo-preview-bar a {{
      color: var(--brand); text-decoration: none; font-weight: 600;
      border: 1px solid var(--brand); padding: 4px 12px; border-radius: 4px;
      transition: background .2s;
    }}
    .denzo-preview-bar a:hover {{ background: var(--brand); color: #fff; }}

    /* ── Page shell ── */
    body {{
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      background: #f8fafc; color: #1e293b; line-height: 1.7;
    }}

    /* Fake nav */
    .denzo-fake-nav {{
      background: #fff; border-bottom: 1px solid #e2e8f0;
      padding: 0 40px; display: flex; align-items: center;
      justify-content: space-between; height: 64px;
    }}
    .denzo-fake-nav .logo {{
      font-weight: 800; font-size: 18px; color: #1e293b; letter-spacing: -.5px;
    }}
    .denzo-fake-nav .nav-cta {{
      background: var(--brand); color: #fff; padding: 8px 20px;
      border-radius: 6px; font-weight: 600; font-size: 14px;
      text-decoration: none; white-space: nowrap;
    }}
    .denzo-fake-nav .nav-links {{
      display: flex; gap: 24px; list-style: none;
    }}
    .denzo-fake-nav .nav-links a {{
      color: #64748b; text-decoration: none; font-size: 14px; font-weight: 500;
    }}

    /* ── Main content wrapper ── */
    .denzo-page-content {{
      max-width: 900px; margin: 0 auto; padding: 0 0 80px;
      background: #fff;
    }}

    /* ── Hero section ── */
    .denzo-page-content .hero-section {{
      background: linear-gradient(135deg, var(--brand-dark) 0%, #1e3a5f 100%);
      padding: 64px 48px; color: #fff; position: relative; overflow: hidden;
    }}
    .denzo-page-content .hero-section::before {{
      content: ''; position: absolute; top: -60px; right: -60px;
      width: 300px; height: 300px;
      background: color-mix(in srgb, var(--brand) 15%, transparent); border-radius: 50%;
    }}
    .denzo-page-content .hero-badge {{
      display: inline-block; background: color-mix(in srgb, var(--brand) 20%, transparent);
      color: var(--brand); border: 1px solid color-mix(in srgb, var(--brand) 40%, transparent);
      padding: 4px 14px; border-radius: 20px; font-size: 13px; font-weight: 600;
      margin-bottom: 20px; letter-spacing: .3px;
    }}
    .denzo-page-content .hero-section h1 {{
      font-size: clamp(28px, 4vw, 46px); font-weight: 800;
      color: #fff !important; line-height: 1.15; margin-bottom: 20px;
      letter-spacing: -.5px; max-width: 16ch;
    }}
    .denzo-page-content .hero-lead {{
      font-size: 18px; color: #cbd5e1; line-height: 1.7;
      max-width: 60ch; margin-bottom: 32px;
    }}
    .denzo-page-content .btn-primary {{
      display: inline-block; background: var(--brand); color: #fff;
      padding: 14px 32px; border-radius: 8px; font-weight: 700; font-size: 16px;
      text-decoration: none; transition: background .2s, transform .1s;
      margin-bottom: 28px;
    }}
    .denzo-page-content .btn-primary:hover {{ background: var(--brand-hover); transform: translateY(-1px); }}
    .denzo-page-content .trust-bar {{
      display: flex; flex-wrap: wrap; gap: 16px; margin-top: 4px;
    }}
    .denzo-page-content .trust-bar span {{
      color: #94a3b8; font-size: 13px; font-weight: 500;
    }}
    .denzo-page-content .trust-bar span::before {{ content: '✓ '; color: #22c55e; }}

    /* ── Stats bar ── */
    .denzo-page-content .stats-bar {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      background: var(--brand); padding: 0;
    }}
    .denzo-page-content .stat {{
      padding: 24px 20px; text-align: center; border-right: 1px solid rgba(255,255,255,.2);
    }}
    .denzo-page-content .stat:last-child {{ border-right: none; }}
    .denzo-page-content .stat strong {{
      display: block; font-size: 32px; font-weight: 800; color: #fff; line-height: 1;
    }}
    .denzo-page-content .stat span {{
      font-size: 12px; color: rgba(255,255,255,.85); text-transform: uppercase;
      letter-spacing: .5px; font-weight: 600; margin-top: 4px; display: block;
    }}

    /* ── Body sections ── */
    .denzo-page-content .section {{ padding: 52px 48px; }}
    .denzo-page-content .section:nth-child(even) {{ background: #f8fafc; }}

    /* ── Typography ── */
    .denzo-page-content h2 {{
      font-size: clamp(22px, 2.5vw, 30px); font-weight: 700;
      color: var(--brand-dark); margin: 0 0 20px;
      padding-bottom: 12px; border-bottom: 3px solid var(--brand);
      display: inline-block;
    }}
    .denzo-page-content h3 {{
      font-size: 17px; font-weight: 700; color: #1e293b; margin: 0 0 8px;
    }}
    .denzo-page-content p {{
      color: #475569; margin-bottom: 16px; font-size: 16px; line-height: 1.75;
    }}
    .denzo-page-content ul, .denzo-page-content ol {{
      color: #475569; padding-left: 20px; margin-bottom: 16px;
    }}
    .denzo-page-content li {{ margin-bottom: 8px; font-size: 16px; line-height: 1.6; }}
    .denzo-page-content a {{ color: var(--brand); text-decoration: none; }}
    .denzo-page-content blockquote {{
      border-left: 4px solid var(--brand); margin: 24px 0;
      padding: 16px 24px; background: var(--brand-faint); border-radius: 0 8px 8px 0;
      font-style: italic; color: var(--brand-dark); font-size: 16px;
    }}

    /* ── Services grid ── */
    .denzo-page-content .services-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 20px; margin-top: 28px;
    }}
    .denzo-page-content .service-card {{
      background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
      padding: 24px; transition: box-shadow .2s, border-color .2s;
    }}
    .denzo-page-content .section:nth-child(even) .service-card {{ background: #fff; }}
    .denzo-page-content .service-card:hover {{
      box-shadow: 0 8px 24px rgba(0,0,0,.08); border-color: var(--brand);
    }}
    .denzo-page-content .service-card h3 {{
      color: var(--brand); margin-bottom: 10px;
    }}
    .denzo-page-content .service-card p {{ margin: 0; font-size: 15px; color: #64748b; }}

    /* ── Process steps ── */
    .denzo-page-content .process-steps {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 24px; margin-top: 28px;
    }}
    .denzo-page-content .step {{
      text-align: center; padding: 8px;
    }}
    .denzo-page-content .step-num {{
      width: 48px; height: 48px; background: var(--brand); color: #fff;
      border-radius: 50%; display: flex; align-items: center; justify-content: center;
      font-weight: 800; font-size: 20px; margin: 0 auto 16px;
    }}
    .denzo-page-content .step h3 {{ margin-bottom: 8px; font-size: 16px; }}
    .denzo-page-content .step p {{ font-size: 14px; color: #64748b; margin: 0; }}

    /* ── FAQ section ── */
    .denzo-page-content [itemtype*="FAQPage"] {{
      margin-top: 16px;
    }}
    .denzo-page-content [itemtype*="Question"] {{
      border: 1px solid #e2e8f0; border-radius: 10px; padding: 20px 24px;
      margin-bottom: 12px; background: #fff;
    }}
    .denzo-page-content [itemprop="name"] {{
      font-weight: 700; color: #1e293b; font-size: 16px; margin: 0 0 10px;
    }}
    .denzo-page-content [itemprop="text"] {{
      color: #64748b; font-size: 15px; margin: 0; line-height: 1.65;
    }}

    /* ── Images — constrained, faces never cut ── */
    .denzo-page-content img {{
      max-width: 100%; width: 100%; display: block;
      max-height: 480px; object-fit: cover; object-position: top center;
      border-radius: 10px; margin: 28px 0;
      box-shadow: 0 4px 20px rgba(0,0,0,.08);
    }}
    .denzo-page-content .hero-section img {{
      max-height: 360px; object-fit: cover; object-position: top center;
      border-radius: 8px; margin: 24px 0 0;
      box-shadow: 0 8px 32px rgba(0,0,0,.3);
    }}

    /* ── Mobile responsive ── */
    @media (max-width: 640px) {{
      .denzo-page-content .hero-section {{ padding: 40px 24px; }}
      .denzo-page-content .section {{ padding: 40px 24px; }}
      .denzo-fake-nav {{ padding: 0 16px; }}
      .denzo-fake-nav .nav-links {{ display: none; }}
      .denzo-page-content .stats-bar {{ grid-template-columns: repeat(2, 1fr); }}
      .denzo-page-content .services-grid {{ grid-template-columns: 1fr; }}
      .denzo-page-content .process-steps {{ grid-template-columns: 1fr; }}
      .denzo-cta-inject {{ flex-direction: column; text-align: center; padding: 28px 24px; }}
    }}

    /* ── Inline CTA bar ── */
    .denzo-cta-inject {{
      background: linear-gradient(135deg, #1e293b 0%, var(--brand-dark) 100%);
      border-radius: 12px; padding: 36px 40px;
      display: flex; align-items: center; justify-content: space-between;
      flex-wrap: wrap; gap: 20px; margin: 44px 0;
    }}
    .denzo-cta-inject .cta-text {{ color: #f1f5f9; }}
    .denzo-cta-inject .cta-text h3 {{
      color: #fff; font-size: 22px; font-weight: 700;
      margin: 0 0 6px; border: none; padding: 0;
    }}
    .denzo-cta-inject .cta-text p {{ color: #94a3b8; margin: 0; font-size: 15px; }}
    .denzo-cta-inject .cta-btn {{
      background: var(--brand); color: #fff; padding: 14px 28px;
      border-radius: 8px; font-weight: 700; font-size: 16px;
      text-decoration: none; white-space: nowrap; flex-shrink: 0;
    }}

    /* ── Footer ── */
    .denzo-fake-footer {{
      background: var(--brand-dark); color: #64748b;
      text-align: center; padding: 24px; font-size: 13px;
      margin-top: 0;
    }}
  </style>
</head>
<body>

  <!-- Preview bar -->
  <div class="denzo-preview-bar">
    <div>
      <span class="badge">Preview</span>
      <span>{_esc(page_row['title'] or '')}</span>
      <span class="meta"> &nbsp;·&nbsp; {_esc(page_row['type'] or '')} &nbsp;·&nbsp; /{_esc(page_row['slug'] or '')}</span>
    </div>
    <a href="javascript:history.back()">✕ Close</a>
  </div>

  <!-- Fake nav -->
  <nav class="denzo-fake-nav">
    <span class="logo">{client_name}</span>
    <ul class="nav-links">
      <li><a href="#">Services</a></li>
      <li><a href="#">About</a></li>
      <li><a href="#">Locations</a></li>
    </ul>
    <a class="nav-cta" href="tel:{client_phone}">📞 {client_phone or 'Call Us'}</a>
  </nav>

  <!-- Page content -->
  <div class="denzo-page-content">
    {content}

    <!-- Injected CTA -->
    <div class="denzo-cta-inject">
      <div class="cta-text">
        <h3>Ready to get started?</h3>
        <p>Contact {client_name} today for a free consultation.</p>
      </div>
      <a class="cta-btn" href="tel:{client_phone}">Call {client_phone or 'Us Now'}</a>
    </div>
  </div>

  <!-- Fake footer -->
  <div class="denzo-fake-footer">
    &copy; {client_name} — Preview generated by DENZO SEO
  </div>

</body>
</html>"""

    return Response(content, mimetype="text/html")


@bp.route("/pages/<int:page_id>/review")
@login_required
def review(tenant_id, page_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    page_row = db.execute(
        "SELECT * FROM pages WHERE id=? AND tenant_id=?", (page_id, tenant_id)
    ).fetchone()
    clients = _get_sidebar_clients()
    db.close()

    if not client or not page_row:
        abort(404)

    return render_template(
        "pages/review.html",
        client=dict(client),
        page=dict(page_row),
        tenant_id=tenant_id,
        active_tenant=tenant_id,
        clients=clients,
    )


@bp.route("/pages/<int:page_id>/approve", methods=["POST"])
@login_required
def approve_page(tenant_id, page_id):
    db = get_db()
    page_row = db.execute(
        "SELECT * FROM pages WHERE id=? AND tenant_id=?", (page_id, tenant_id)
    ).fetchone()
    if not page_row:
        db.close()
        return jsonify({"error": "Page not found"}), 404

    db.execute(
        "UPDATE pages SET status='ready', updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
        (page_id, tenant_id)
    )
    db.execute(
        "INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
        (tenant_id, "review", f"Page '{page_row['title']}' approved.", "editor", "success")
    )
    db.commit()
    db.close()
    flash(f"✓ Page approved and marked ready.", "success")
    return redirect(url_for("pages.index", tenant_id=tenant_id))


@bp.route("/pages/<int:page_id>/request-changes", methods=["POST"])
@login_required
def request_changes(tenant_id, page_id):
    note = request.form.get("note", "").strip()
    db = get_db()
    page_row = db.execute(
        "SELECT * FROM pages WHERE id=? AND tenant_id=?", (page_id, tenant_id)
    ).fetchone()
    if not page_row:
        db.close()
        return jsonify({"error": "Page not found"}), 404

    existing_notes = page_row["notes"] or ""
    separator = "\n---\n" if existing_notes else ""
    new_notes = f"{existing_notes}{separator}{note}" if note else existing_notes

    db.execute(
        "UPDATE pages SET status='draft', notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
        (new_notes, page_id, tenant_id)
    )
    db.execute(
        "INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
        (tenant_id, "review", f"Changes requested for '{page_row['title']}': {note[:80]}", "editor", "warning")
    )
    db.commit()
    db.close()
    flash("Changes noted. Page returned to draft.", "warning")
    return redirect(url_for("pages.index", tenant_id=tenant_id))


@bp.route("/pages/<int:page_id>/regenerate", methods=["POST"])
@login_required
def regenerate_page(tenant_id, page_id):
    note = request.form.get("note", "").strip()
    db = get_db()
    page_row = db.execute(
        "SELECT * FROM pages WHERE id=? AND tenant_id=?", (page_id, tenant_id)
    ).fetchone()
    if not page_row:
        db.close()
        return jsonify({"error": "Page not found"}), 404

    # Keep notes so agents know WHY it was regenerated
    existing_notes = page_row["notes"] or ""
    separator = "\n---\n" if existing_notes else ""
    regen_note = f"[REGEN REQUEST] {note}" if note else "[REGEN REQUEST — editor requested new version]"
    new_notes = f"{existing_notes}{separator}{regen_note}"

    db.execute(
        "UPDATE pages SET status='pending', content='', quality_score=NULL, notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
        (new_notes, page_id, tenant_id)
    )
    db.execute(
        "INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
        (tenant_id, "review", f"Regeneration requested for '{page_row['title']}'.", "editor", "info")
    )
    db.commit()
    db.close()
    flash("Page queued for regeneration.", "info")
    return redirect(url_for("pages.index", tenant_id=tenant_id))


@bp.route("/pages/export.csv")
@login_required
def export_pages_csv(tenant_id):
    db = get_db()
    client = db.execute("SELECT name FROM clients WHERE tenant_id=?", (tenant_id,)).fetchone()
    if not client:
        db.close()
        abort(404)

    status_filter = request.args.get("status", "").strip()
    type_filter   = request.args.get("type", "").strip()

    conditions = ["tenant_id = ?"]
    params = [tenant_id]
    if status_filter:
        conditions.append("status = ?"); params.append(status_filter)
    if type_filter:
        conditions.append("type = ?"); params.append(type_filter)

    rows = db.execute(
        f"SELECT title, slug, type, location, target_keyword, status, "
        f"quality_score, meta_title, meta_description, publish_url, created_at "
        f"FROM pages WHERE {' AND '.join(conditions)} ORDER BY created_at DESC",
        params
    ).fetchall()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "title", "slug", "type", "location", "target_keyword", "status",
        "quality_score", "meta_title", "meta_description", "publish_url", "created_at"
    ])
    for r in rows:
        writer.writerow([
            r["title"], r["slug"], r["type"], r["location"], r["target_keyword"],
            r["status"], r["quality_score"], r["meta_title"], r["meta_description"],
            r["publish_url"], r["created_at"]
        ])

    filename = f"{tenant_id}-pages.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
