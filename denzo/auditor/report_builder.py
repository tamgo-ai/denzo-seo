"""
Report Builder v3 — premium standalone HTML reports matching audit-acg-v2.html quality.
Beautiful light theme, navigation tabs, rich section cards, comparison grids,
fix boxes with code, impact boxes, metric tables with status colors.
"""
import html
import json
from datetime import datetime, timezone
from denzo.auditor.analyzer import MODULE_WEIGHTS


def build_report_html(result: dict, audit_id: str) -> str:
    url = result.get('url',''); domain = result.get('domain','')
    overall = result.get('overall_score',0)
    module_scores = result.get('module_scores',{})
    findings = result.get('findings',[])
    fetch_method = result.get('fetch_method','')
    page_title = result.get('page_title','')
    html_size_kb = result.get('html_size_kb',0)
    word_count = result.get('word_count',0)
    text_html_ratio = result.get('text_html_ratio',0)
    image_count = result.get('image_count',0)
    schema_blocks = result.get('schema_blocks',0)
    schema_types = result.get('schema_types',[])
    sitemap_url = result.get('sitemap_url','')
    sitemap_total = result.get('sitemap_total_urls',0)
    llms_status = result.get('llms_status','')
    faq_count = result.get('faq_count',0)
    li_count = result.get('li_count',0)
    internal_links = result.get('internal_links',0)

    results = result.get('results',{})
    tech = results.get('technical',{})
    sitemap_r = results.get('sitemap',{})
    robots_r = results.get('robots',{})
    llms_r = results.get('llms',{})
    geo_r = results.get('geo',{})
    images_r = results.get('images',{})
    perf_r = results.get('performance',{})

    now = datetime.now(timezone.utc).strftime('%B %d, %Y at %H:%M UTC')
    sc = '#16a34a' if overall >= 70 else '#d97706' if overall >= 40 else '#dc2626'

    critical=[f for f in findings if f['severity']=='critical']
    high=[f for f in findings if f['severity']=='high']
    medium=[f for f in findings if f['severity']=='medium']
    low=[f for f in findings if f['severity'] in ('low','info')]
    passing=[f for f in findings if f['severity']=='pass']
    fixed=[f for f in findings if f['severity']=='fixed']

    # ── Module score bars ──
    mods=[('technical','Technical SEO',MODULE_WEIGHTS.get('technical',30),tech.get('score',0)),('geo','Content & Authority',MODULE_WEIGHTS.get('geo',22),geo_r.get('score',0)),('content','Content Quality',MODULE_WEIGHTS.get('content',10),results.get('content',{}).get('score',0)),('performance','Core Web Vitals',MODULE_WEIGHTS.get('performance',15),perf_r.get('score',0)),('images','Image Optimization',MODULE_WEIGHTS.get('images',8),images_r.get('score',0)),('local_seo','Local SEO & GBP',MODULE_WEIGHTS.get('local_seo',0),results.get('local_seo',{}).get('score',0)),('sitemap','Sitemap',MODULE_WEIGHTS.get('sitemap',8),sitemap_r.get('score',0)),('robots','Robots.txt',MODULE_WEIGHTS.get('robots',7),robots_r.get('score',0)),('llms','AI Crawlers',MODULE_WEIGHTS.get('llms',0),llms_r.get('score',0))]
    bars=''
    for mod,label,w,s in mods:
        bc='#16a34a' if s>=70 else '#d97706' if s>=40 else '#dc2626'
        bars+=f'<div class="score-row"><span class="score-label">{label}</span><div class="score-bar"><div class="score-fill" style="width:{s}%;background:{bc};"></div></div><span class="score-val" style="color:{bc};">{s}/100</span><span class="score-wt">{w}%</span></div>'

    # ── Finding HTML ──
    sev_colors={'critical':('#dc2626','#fee2e2','#991b1b','CRITICAL'),'high':('#f97316','#fef3c7','#92400e','HIGH'),'medium':('#d97706','#fffbeb','#92400e','MEDIUM'),'low':('#3b82f6','#eff6ff','#1e40af','LOW'),'info':('#6b7280','#f9fafb','#374151','INFO'),'pass':('#16a34a','#f0fdf4','#166534','PASS'),'fixed':('#8b5cf6','#f5f3ff','#5b21b6','FIXED')}
    def finding_html(f):
        c=sev_colors.get(f['severity'],sev_colors['info'])
        mod=html.escape(f.get('module','').upper())
        fix=''
        if f.get('fix'):
            fix=f'<div class="fix-box"><strong>→ Fix:</strong><br><pre>{html.escape(f["fix"])}</pre></div>'
        impact=''
        if f.get('impact'):
            impact=f'<div class="impact-box"><strong>📉 Impact:</strong> {html.escape(f["impact"])}</div>'
        detail=html.escape(f.get('detail','')).replace('\n','<br>')
        return f'''<div class="finding finding-{f['severity']}">
<span class="tag tag-{f['severity']}">{c[3]}</span> <span class="mod-tag">{mod}</span>
<h4>{html.escape(f["title"])}</h4>
<p>{detail}</p>
{impact}{fix}
</div>'''

    # ── Quick stats ──
    ss=[('Page Weight',f'{html_size_kb} KB','HTML size'),('Text/HTML Ratio',f'{text_html_ratio}%','Target: 10-25%'),('Word Count',f'{word_count:,}','words'),('Images',f'{image_count}','total'),('Schema Blocks',f'{schema_blocks}','JSON-LD'),('Sitemap URLs',f'{sitemap_total:,}','in sitemap'),('Internal Links',f'{internal_links}','internal'),('Total Findings',f'{len(critical)}C / {len(high)}H / {len(medium)}M','by severity')]
    qs=''
    for l,v,sub in ss:
        qs+=f'<div class="stat-card"><div class="stat-val">{v}</div><div class="stat-label">{l}</div><div class="stat-sub">{sub}</div></div>'

    # ── Key indicators ──
    sitemap_status=f'<span class="pass">✓ Found: {sitemap_total:,} URLs</span>' if sitemap_url else '<span class="fail">✗ Not found</span>'
    llms_disp=f'<span class="pass">✓ Present</span>' if llms_status=='present' else f'<span class="fail">✗ {llms_status or "Missing"}</span>'
    schema_disp=f'<span class="pass">✓ {schema_blocks} blocks — {", ".join(schema_types[:5])}</span>' if schema_blocks>0 else '<span class="fail">✗ None</span>'
    faq_disp=f'<span class="pass">✓ {faq_count} questions</span>' if faq_count>=2 else f'<span class="fail">✗ {faq_count} questions</span>'
    lists_disp=f'<span class="pass">✓ {li_count} items</span>' if li_count>=10 else f'<span class="fail">✗ {li_count} items</span>'
    eeat=geo_r.get('eeat_score',0)
    eeat_disp=f'<span class="{"pass" if eeat>=8 else "warn" if eeat>=4 else "fail"}">{"✓" if eeat>=8 else "⚠" if eeat>=4 else "✗"} {eeat}/11</span>'

    # Domain mismatch warning
    dm=sitemap_r.get('domain_mismatches',0)
    dm_row=f'<tr class="warn-row"><td>⚠ Sitemap Domain Mismatch</td><td><span class="fail">{dm} URLs use wrong domain</span></td></tr>' if dm>0 else ''

    # AI crawlers
    ai_blocked=robots_r.get('ai_crawlers_blocked',[])
    ai_allowed=robots_r.get('ai_crawlers_allowed',[])
    ai_row=''
    if ai_blocked: ai_row=f'<tr class="warn-row"><td>AI Crawlers Blocked</td><td><span class="fail">{len(ai_blocked)}: {", ".join(ai_blocked[:5])}</span></td></tr>'
    elif ai_allowed: ai_row=f'<tr><td>AI Crawlers Accessible</td><td><span class="pass">{len(ai_allowed)} allowed (GPTBot, CCBot, Claude, Perplexity…)</span></td></tr>'

    # CWV estimates
    cwv=perf_r.get('cwv',{})
    cwv_rows=''
    if cwv:
        lcp=cwv.get('estimated_lcp',0); lcp_g=cwv.get('lcp_grade','')
        cls=cwv.get('estimated_cls',0); cls_g=cwv.get('cls_grade','')
        tbt=cwv.get('estimated_tbt',0); tbt_g=cwv.get('tbt_grade','')
        cwv_rows=f'<tr><td>Est. LCP</td><td><span class="{"pass" if lcp_g=="good" else "warn" if lcp_g=="needs_improvement" else "fail"}">{lcp}s ({lcp_g})</span></td></tr><tr><td>Est. CLS</td><td><span class="{"pass" if cls_g=="good" else "warn" if cls_g=="needs_improvement" else "fail"}">{cls} ({cls_g})</span></td></tr><tr><td>Est. TBT</td><td><span class="{"pass" if tbt_g=="good" else "warn" if tbt_g=="needs_improvement" else "fail"}">{tbt}ms ({tbt_g})</span></td></tr>'

    # llms download section
    llms_gen=result.get('llms_generated',{})
    has_llms=bool(llms_gen and llms_gen.get('llms_txt'))
    llms_section=''
    if has_llms:
        biz=llms_gen.get('business_name','the site')
        locs=llms_gen.get('locations_found',0); certs=llms_gen.get('certs_found',0)
        svcs=llms_gen.get('services_found',0); faqs=llms_gen.get('faqs_found',0)
        diffs=llms_gen.get('differentiators_found',0)
        llms_section=f'''<section id="llms-gen">
<div class="section-header"><h2>🤖 AI-Ready Files Generated</h2></div>
<div class="llms-banner">
<p>We auto-generated optimized <strong>llms.txt</strong> and <strong>llms-full.txt</strong> for {biz} from the actual site content. Deploy these to make the site instantly discoverable by ChatGPT, Claude, Perplexity, and Gemini.</p>
<div class="llms-btns">
<a href="/auditor/report/{audit_id}/llms.txt" class="btn-dl-primary">⬇ Download llms.txt</a>
<a href="/auditor/report/{audit_id}/llms-full.txt" class="btn-dl-secondary">⬇ Download llms-full.txt</a>
</div>
<div class="llms-stats"><span>{locs} locations</span><span>{certs} certifications</span><span>{svcs} services</span><span>{faqs} FAQs</span><span>{diffs} differentiators</span></div>
<p class="llms-deploy">Deploy: drop in <code>/public/</code> (Next.js) or webroot. Add <code>&lt;link rel="llms.txt" href="/llms.txt"&gt;</code> to &lt;head&gt;.</p>
</div></section>'''

    # ── Keywords & Search Intent section ──
    kw = results.get('keywords', {}) or results.get('content', {})
    kw_section = ''
    if kw:
        primary_kw = kw.get('primary_keyword', '')
        intent = kw.get('dominant_intent', '')
        intent_label = {'transactional': '🛒 Transactional', 'commercial': '🔍 Commercial',
                       'informational': '📚 Informational', 'navigational': '🏢 Navigational'}.get(intent, intent)
        kw_section = f'''<section id="keywords"><div class="section-header"><h2>🎯 Keyword & Search Intent</h2></div>
    <table class="metric-table"><tbody>
    <tr><td>Primary Keyword</td><td><strong>{html.escape(primary_kw or 'Not detected')}</strong></td></tr>
    <tr><td>Search Intent</td><td>{intent_label}</td></tr>
    <tr><td>Keyword in H1</td><td><span class="{'pass' if kw.get('kw_in_h1') else 'fail'}">{'Yes' if kw.get('kw_in_h1') else 'No'}</span></td></tr>
    <tr><td>Keyword in URL</td><td><span class="{'pass' if kw.get('kw_in_url') else 'fail'}">{'Yes' if kw.get('kw_in_url') else 'No'}</span></td></tr>
    <tr><td>Keyword Density</td><td>{kw.get('kw_density', 0)}%</td></tr>
    <tr><td>Top Content Terms</td><td>{', '.join(kw.get('word_frequency_top', [])[:8])}</td></tr>
    </tbody></table></section>'''

    # ── Content Quality section ──
    cq = results.get('content', {}) or {}
    cq_section = ''
    if cq and cq.get('word_count', 0) > 0:
        orig = cq.get('originality_signals', {})
        cq_section = f'''<section id="content-quality"><div class="section-header"><h2>📝 Content Quality</h2></div>
    <table class="metric-table"><tbody>
    <tr><td>Word Count</td><td><strong>{cq.get('word_count', 0):,}</strong></td></tr>
    <tr><td>Readability</td><td>Grade {cq.get('readability_score', 'N/A')} {"(broadly accessible)" if cq.get('readability_score', 99) <= 10 else "(complex)"} — {cq.get('readability_method', 'N/A')}</td></tr>
    <tr><td>Avg Words per Sentence</td><td>{cq.get('avg_words_per_sentence', 'N/A')}</td></tr>
    <tr><td>Avg Paragraph Length</td><td>{cq.get('avg_para_length', 'N/A')} words</td></tr>
    <tr><td>Originality Score</td><td><span class="{'pass' if cq.get('originality_score', 0) >= 15 else 'warn' if cq.get('originality_score', 0) >= 5 else 'fail'}">{cq.get('originality_score', 0)} data points</span></td></tr>
    <tr><td>Statistics & Numbers</td><td>{orig.get('statistics', 0)} stats, {orig.get('specific_numbers', 0)} specific references</td></tr>
    <tr><td>Rich Elements</td><td>{cq.get('rich_elements', 0)} (images, videos, tables, quotes)</td></tr>
    </tbody></table></section>'''

    # ── Local Business section ──
    lb = results.get('local_seo', {}) or {}
    lb_section = ''
    if lb and lb.get('is_local'):
        lb_section = f'''<section id="local-seo"><div class="section-header"><h2>📍 Local SEO & Google Business Profile</h2></div>
    <table class="metric-table"><tbody>
    <tr><td>Business Name</td><td><strong>{html.escape(lb.get('business_name', 'N/A'))}</strong></td></tr>
    <tr><td>NAP Score</td><td><span class="{'pass' if lb.get('nap_score', 0) == 3 else 'fail'}">{lb.get('nap_score', 0)}/3</span></td></tr>
    <tr><td>Phone Numbers Found</td><td>{', '.join(lb.get('phones_found', [])[:3]) or 'None'}</td></tr>
    <tr><td>Addresses Found</td><td>{', '.join(lb.get('addresses_found', [])[:2]) or 'None'}</td></tr>
    <tr><td>Review Signals</td><td><span class="{'pass' if lb.get('has_review_signals') else 'fail'}">{'Present' if lb.get('has_review_signals') else 'Missing'}</span></td></tr>
    <tr><td>LocalBusiness Schema</td><td><span class="{'pass' if lb.get('has_local_schema') else 'fail'}">{'Present' if lb.get('has_local_schema') else 'Missing'}</span></td></tr>
    <tr><td>Service Cities Detected</td><td>{', '.join(lb.get('service_cities', [])[:8]) or 'None detected'}</td></tr>
    </tbody></table></section>'''

    # ── AI Citations section ──
    ai_cite = results.get('ai_citations', {}) or {}
    ai_section = ''
    if ai_cite and ai_cite.get('queries_checked', 0) > 0:
        cite_results = ai_cite.get('results', [])
        cite_rows = ''
        for r in cite_results:
            icon = '✅' if r.get('cited') else '❌' if r.get('cited') is False else '⚠️'
            cite_rows += f'<tr><td>{icon} {r.get("type","?")}</td><td>{html.escape(r.get("query","?"))}</td></tr>'
        ai_section = f'''<section id="ai-citations"><div class="section-header"><h2>🤖 AI Search Visibility (Perplexity)</h2></div>
    <table class="metric-table"><tbody>
    <tr><td>Citations Found</td><td><span class="{"pass" if ai_cite.get("citations_found",0) >= 2 else "warn" if ai_cite.get("citations_found",0) >= 1 else "fail"}">{ai_cite.get("citations_found", 0)}/{ai_cite.get("queries_checked", 0)} queries cited</span></td></tr>
    {cite_rows}
    </tbody></table></section>'''

    # ── Keyword Research section ──
    kw_research = results.get('keyword_research', {}) or {}
    kwr_section = ''
    suggestions = kw_research.get('suggestions', {})
    if suggestions and isinstance(suggestions, dict) and not suggestions.get('error'):
        hp = suggestions.get('high_priority', [])
        gaps = suggestions.get('content_gaps', [])
        local = suggestions.get('local_keywords', [])
        hp_rows = ''
        for k in hp[:5]:
            hp_rows += f'<tr><td>🎯</td><td><strong>{html.escape(k.get("keyword","?"))}</strong></td><td>{html.escape(k.get("intent","?"))}</td><td>{html.escape(k.get("why","?")[:100])}</td></tr>'
        gap_rows = ''
        for g in gaps[:3]:
            gap_rows += f'<tr><td>📝</td><td><strong>{html.escape(g.get("keyword","?"))}</strong></td><td colspan="2">{html.escape(g.get("why","?")[:100])}</td></tr>'

        kwr_section = f'''<section id="keyword-research"><div class="section-header"><h2>🔑 AI Keyword Suggestions (Claude)</h2></div>
    {"<h3>High-Priority Keywords</h3><table class=\"metric-table\"><thead><tr><th></th><th>Keyword</th><th>Intent</th><th>Why</th></tr></thead><tbody>" + hp_rows + "</tbody></table>" if hp_rows else ""}
    {"<h3>Content Gaps</h3><table class=\"metric-table\"><thead><tr><th></th><th>Keyword</th><th colspan=\"2\">Why</th></tr></thead><tbody>" + gap_rows + "</tbody></table>" if gap_rows else ""}
    </section>'''

    # GEO benchmarks
    benchmarks=geo_r.get('benchmarks',{})
    bench_section=''
    if benchmarks:
        bench_rows=''
        for k,b in benchmarks.items():
            y=b['yours']; a=b['avg']; best=b['best']
            status='✓' if y>=a else '⚠' if y>=a*0.5 else '✗'
            bc='#16a34a' if y>=a else '#d97706' if y>=a*0.5 else '#dc2626'
            bench_rows+=f'<tr><td>{b["label"]}</td><td style="color:{bc};font-weight:600;">{status} {y}</td><td>{a}</td><td>{best}</td></tr>'
        bench_section=f'''<section id="benchmarks"><div class="section-header"><h2>📊 GEO Industry Benchmarks</h2></div>
<table class="metric-table"><thead><tr><th>Metric</th><th>Your Score</th><th>Industry Avg</th><th>Best in Class</th></tr></thead><tbody>{bench_rows}</tbody></table></section>'''

    # Image stats
    img_section=''
    if images_r:
        total=images_r.get('total',0); webp=images_r.get('webp_avif',0); lazy=images_r.get('lazy',0)
        alt_q=images_r.get('with_quality_alt',0); dims=images_r.get('with_dims',0); png=images_r.get('png',0)
        lcp=images_r.get('lcp_candidate',''); lcp_ok=images_r.get('lcp_optimized',False)
        img_section=f'''<section id="images"><div class="section-header"><h2>🖼️ Image Optimization</h2></div>
<table class="metric-table"><thead><tr><th>Metric</th><th>Value</th><th>Status</th></tr></thead><tbody>
<tr><td>Total Images</td><td>{total}</td><td>—</td></tr>
<tr><td>Alt Text Present</td><td>{images_r.get("with_alt",0)}/{total} ({round(images_r.get("with_alt",0)/max(total,1)*100)}%)</td><td class="{"pass" if images_r.get("with_alt",0)==total else "warn"}">{'✓' if images_r.get("with_alt",0)==total else '⚠'}</td></tr>
<tr><td>Quality Alt Text</td><td>{alt_q}/{total} ({round(alt_q/max(total,1)*100)}%)</td><td class="{"pass" if alt_q>=total*0.8 else "warn"}">{'✓' if alt_q>=total*0.8 else '⚠'}</td></tr>
<tr><td>WebP/AVIF Format</td><td>{webp}/{total} ({round(webp/max(total,1)*100)}%)</td><td class="{"pass" if webp>=total*0.7 else "fail" if webp<total*0.3 else "warn"}">{'✓' if webp>=total*0.7 else '✗' if webp<total*0.3 else '⚠'}</td></tr>
<tr><td>Dimensions Set</td><td>{dims}/{total} ({round(dims/max(total,1)*100)}%)</td><td class="{"pass" if dims>=total*0.9 else "fail"}">{'✓' if dims>=total*0.9 else '✗'}</td></tr>
<tr><td>Lazy Loading</td><td>{lazy}/{total} ({round(lazy/max(total,1)*100)}%)</td><td class="{"pass" if lazy>=total*0.7 else "warn"}">{'✓' if lazy>=total*0.7 else '⚠'}</td></tr>
<tr><td>PNG (unoptimized)</td><td>{png}</td><td class="{"pass" if png==0 else "fail" if png>total*0.3 else "warn"}">{'✓' if png==0 else '✗' if png>total*0.3 else '⚠'}</td></tr>
<tr><td>LCP Image</td><td>{lcp[:80] if lcp else 'N/A'}</td><td class="{"pass" if lcp_ok else "warn"}">{'✓ optimized' if lcp_ok else '⚠ needs priority' if lcp else '—'}</td></tr>
</tbody></table></section>'''

    # ── Real Performance section (PSI API) ──
    perf = results.get('performance', {}) or {}
    psi_section = ''
    real_perf = perf.get('real_perf') or {}
    if real_perf and real_perf.get('score', 0) > 0:
        psi_section = f'''<section id="psi"><div class="section-header"><h2>⚡ Real Performance Data (PageSpeed Insights)</h2><span style="font-size:0.7rem;color:#64748b;">Google PSI API — real measurements, not estimates</span></div>
    <div class="llms-banner" style="background:linear-gradient(135deg,#14532d,#052e16);">
    <p style="color:#86efac;"><strong>PageSpeed Score: {real_perf.get('score', 'N/A')}/100</strong> (measured by Google Lighthouse)</p>
    </div></section>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>SEO Audit Report — {html.escape(domain)}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;background:#f4f6f9;color:#1a1a2e;line-height:1.6;padding:2rem 1.5rem;-webkit-font-smoothing:antialiased;}}
.container{{max-width:1080px;margin:0 auto}}
h2{{font-size:1.2rem;font-weight:600;margin:1.5rem 0 0.75rem;padding-bottom:0.35rem;border-bottom:2px solid #e2e8f0;letter-spacing:-0.01em;}}
h3{{font-size:1rem;font-weight:600;margin:1.25rem 0 0.5rem}}
h4{{font-size:0.95rem;font-weight:600;margin:0.25rem 0}}
pre{{font-family:'SF Mono','Fira Code','JetBrains Mono',monospace;font-size:0.8rem;background:#f1f5f9;padding:0.85rem;border-radius:8px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;border:1px solid #e2e8f0;margin:0.5rem 0;line-height:1.45;}}

.report-header{{background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);color:#fff;padding:1.75rem 2rem;border-radius:12px;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:1rem;margin-bottom:1.5rem;}}
.report-header h1{{font-size:1.4rem;margin:0;font-weight:700;}}
.report-header .meta{{font-size:0.8rem;color:#94a3b8;margin-top:0.25rem;}}
.score-badge{{display:flex;flex-direction:column;align-items:center;justify-content:center;background:rgba(255,255,255,0.1);border:3px solid {sc};border-radius:50%;width:96px;height:96px;flex-shrink:0;}}
.score-badge .number{{font-size:2.2rem;font-weight:800;line-height:1;color:{sc}}}
.score-badge .label{{font-size:0.6rem;text-transform:uppercase;letter-spacing:0.08em;color:#cbd5e1;}}

.issue-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0.75rem;margin-bottom:1.5rem;}}
.issue-card{{background:#fff;border-radius:10px;padding:1.1rem;box-shadow:0 1px 3px rgba(0,0,0,0.06);text-align:center;}}
.issue-card .count{{font-size:2rem;font-weight:800;line-height:1.2;}}
.issue-card .title{{font-size:0.8rem;font-weight:600;margin-top:0.1rem;}}
.issue-card.critical{{border-left:4px solid #dc2626;}}.issue-card.critical .count{{color:#dc2626;}}
.issue-card.high{{border-left:4px solid #f97316;}}.issue-card.high .count{{color:#f97316;}}
.issue-card.medium{{border-left:4px solid #d97706;}}.issue-card.medium .count{{color:#d97706;}}
.issue-card.low{{border-left:4px solid #3b82f6;}}.issue-card.low .count{{color:#3b82f6;}}
.issue-card.pass{{border-left:4px solid #16a34a;}}.issue-card.pass .count{{color:#16a34a;}}

.section-header{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0.75rem;margin-top:1.75rem;}}
.section-header h2{{margin:0;border:none;padding:0;}}

.score-row{{display:flex;align-items:center;gap:0.6rem;margin-bottom:0.4rem;font-size:0.85rem;}}
.score-label{{width:160px;text-align:right;color:#64748b;flex-shrink:0;}}
.score-bar{{flex:1;background:#e2e8f0;border-radius:6px;height:10px;overflow:hidden;}}
.score-fill{{height:100%;border-radius:6px;transition:width 0.5s ease;}}
.score-val{{font-weight:700;width:48px;font-size:0.8rem;}}
.score-wt{{font-size:0.7rem;color:#94a3b8;width:32px;}}

.stat-card{{background:#fff;padding:0.85rem;border-radius:8px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.04);}}
.stat-val{{font-size:1.35rem;font-weight:800;color:#1a1a2e;}}
.stat-label{{font-size:0.7rem;color:#64748b;text-transform:uppercase;margin-top:0.15rem;}}
.stat-sub{{font-size:0.65rem;color:#94a3b8;}}

.finding{{background:#fff;border-radius:8px;padding:1rem 1.25rem;margin-bottom:0.75rem;box-shadow:0 1px 3px rgba(0,0,0,0.04);border:1px solid #eef2f6;}}
.finding-critical{{border-left:4px solid #dc2626;}}.finding-high{{border-left:4px solid #f97316;}}.finding-medium{{border-left:4px solid #d97706;}}.finding-low,.finding-info{{border-left:4px solid #3b82f6;}}.finding-pass{{border-left:4px solid #16a34a;border-color:#bbf7d0;background:#f9fefb;}}.finding-fixed{{border-left:4px solid #8b5cf6;border-color:#ddd6fe;}}
.finding h4{{margin:0.35rem 0 0.2rem;font-size:0.925rem;}}
.finding p{{font-size:0.85rem;color:#475569;line-height:1.5;margin:0.3rem 0;}}

.tag{{display:inline-block;font-size:0.6rem;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;padding:0.15em 0.55em;border-radius:4px;margin-right:0.35rem;}}
.tag-critical{{background:#dc2626;color:#fff;}}.tag-high{{background:#f97316;color:#fff;}}
.tag-medium{{background:#d97706;color:#fff;}}.tag-low,.tag-info{{background:#3b82f6;color:#fff;}}
.tag-pass{{background:#16a34a;color:#fff;}}.tag-fixed{{background:#8b5cf6;color:#fff;}}
.mod-tag{{font-size:0.65rem;color:#94a3b8;}}

.fix-box{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:0.75rem 0.9rem;margin-top:0.6rem;font-size:0.825rem;}}
.fix-box strong{{color:#166534;}}
.impact-box{{background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:0.75rem 0.9rem;margin-top:0.5rem;font-size:0.8rem;}}
.impact-box strong{{color:#991b1b;}}

.metric-table{{width:100%;border-collapse:collapse;font-size:0.85rem;margin:0.75rem 0;}}
.metric-table th,.metric-table td{{text-align:left;padding:0.55rem 0.75rem;border-bottom:1px solid #e2e8f0;}}
.metric-table th{{background:#f8fafc;font-weight:600;color:#334155;font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em;}}
.pass{{color:#16a34a;font-weight:600;}}.fail{{color:#dc2626;font-weight:600;}}.warn{{color:#d97706;font-weight:600;}}
.warn-row{{background:#fefce8;}}

.llms-banner{{background:linear-gradient(135deg,#1e293b,#0f172a);color:#fff;border-radius:12px;padding:1.5rem;margin:1rem 0;}}
.llms-banner p{{font-size:0.875rem;color:#94a3b8;margin-bottom:1rem;}}
.llms-btns{{display:flex;gap:0.75rem;flex-wrap:wrap;margin-bottom:1rem;}}
.btn-dl-primary{{display:inline-block;padding:0.6rem 1.25rem;background:#6366f1;color:#fff;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.85rem;}}
.btn-dl-secondary{{display:inline-block;padding:0.6rem 1.25rem;background:rgba(255,255,255,0.1);color:#fff;border:1px solid rgba(255,255,255,0.2);border-radius:8px;text-decoration:none;font-weight:600;font-size:0.85rem;}}
.llms-stats{{display:flex;gap:1rem;flex-wrap:wrap;font-size:0.75rem;margin-bottom:0.75rem;}}
.llms-stats span{{background:rgba(255,255,255,0.08);padding:0.3rem 0.75rem;border-radius:6px;}}
.llms-deploy{{font-size:0.7rem;color:#64748b;}}
.llms-deploy code{{background:rgba(255,255,255,0.1);padding:0.15em 0.4em;border-radius:3px;}}

.report-footer{{margin-top:2.5rem;padding-top:1.25rem;border-top:2px solid #e2e8f0;font-size:0.8rem;color:#94a3b8;text-align:center;}}
.report-footer a{{color:#6366f1;text-decoration:none;}}

@media(max-width:640px){{body{{padding:1rem 0.5rem}}.report-header{{flex-direction:column;text-align:center;padding:1.25rem;}}.score-row{{font-size:0.75rem;}}.score-label{{width:100px;}}}}
</style>
</head>
<body>
<div class="container">

<header class="report-header">
<div>
<h1>🔍 SEO Audit Report</h1>
<p style="font-size:1.1rem;font-weight:600;margin:2px 0 0;">{html.escape(page_title or domain)}</p>
<p class="meta">{html.escape(url)} · {now} · {html.escape(fetch_method)}</p>
</div>
<div class="score-badge"><span class="number">{overall}</span><span class="label">/ 100</span></div>
</header>

<div class="issue-grid">
<div class="issue-card critical"><div class="count">{len(critical)}</div><div class="title">Critical</div></div>
<div class="issue-card high"><div class="count">{len(high)}</div><div class="title">High</div></div>
<div class="issue-card medium"><div class="count">{len(medium)}</div><div class="title">Medium</div></div>
<div class="issue-card low"><div class="count">{len(low)}</div><div class="title">Low/Info</div></div>
<div class="issue-card pass"><div class="count">{len(passing)}</div><div class="title">Passing</div></div>
</div>

{llms_section}

<section id="scores"><div class="section-header"><h2>📊 Score Breakdown</h2></div>
{bars}
</section>

<section id="indicators"><div class="section-header"><h2>🔑 Key Indicators</h2></div>
<table class="metric-table">
<tr><td>Sitemap</td><td>{sitemap_status}</td></tr>
{dm_row}
<tr><td>llms.txt / AI Crawlers</td><td>{llms_disp}</td></tr>
{ai_row}
<tr><td>Structured Data (Schema)</td><td>{schema_disp}</td></tr>
<tr><td>FAQ Content (Visible)</td><td>{faq_disp}</td></tr>
<tr><td>Structured Lists</td><td>{lists_disp}</td></tr>
<tr><td>E-E-A-T Score</td><td>{eeat_disp}</td></tr>
{cwv_rows}
</table>
</section>

<section id="stats"><div class="section-header"><h2>📈 Page Stats</h2></div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:0.5rem;">{qs}</div>
</section>

{psi_section}
{kw_section}
{cq_section}
{lb_section}
{ai_section}
{kwr_section}
{bench_section}
{img_section}

{f'<section id="critical"><div class="section-header"><h2>🔴 Critical Issues ({len(critical)})</h2></div>{"".join(finding_html(f) for f in critical)}</section>' if critical else ''}
{f'<section id="high"><div class="section-header"><h2>🟠 High Priority ({len(high)})</h2></div>{"".join(finding_html(f) for f in high)}</section>' if high else ''}
{f'<section id="medium"><div class="section-header"><h2>🟡 Medium Priority ({len(medium)})</h2></div>{"".join(finding_html(f) for f in medium)}</section>' if medium else ''}
{f'<section id="low"><div class="section-header"><h2>🔵 Low Priority / Info</h2></div>{"".join(finding_html(f) for f in low)}</section>' if low else ''}
{f'<section id="pass"><div class="section-header"><h2>✅ Passing Checks ({len(passing)})</h2></div>{"".join(finding_html(f) for f in passing)}</section>' if passing else ''}

<footer class="report-footer">
<p><strong>Generated by DENZO SEO Site Auditor</strong> — {now}</p>
<p><a href="/auditor/">New Audit</a> · <a href="/auditor/report/{audit_id}/download">Download HTML</a> · <a href="/auditor/history">History</a></p>
<p style="margin-top:0.25rem;">{html.escape(url)} · Audit ID: {audit_id}</p>
</footer>

</div></body></html>'''
