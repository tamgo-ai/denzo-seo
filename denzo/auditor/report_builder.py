"""
Report Builder v4 — dual-mode standalone reports.

Two modes, chosen by request host:
  * 'droppin' — acquisition: verdict, measured findings and full fix detail,
    with an optional contact CTA. Incomplete measurements do not show a score.
  * 'full'    — internal technical tool: every finding + fix, filterable, plus a
    "copy the full technical brief" button for handing to an AI.

Dark premium theme matching the landing. CSS is a single module constant (not an
f-string) and is inlined so downloads are self-contained.
"""
import html
import json
import base64
import os
from datetime import datetime, timezone
from denzo.auditor.analyzer import MODULE_WEIGHTS

_SEV_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4, 'pass': 5}

_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'static', 'img', 'brand', 'logo-light.png'
)


def _grade(score):
    if score >= 90:
        return 'A', 'EXCELLENT', '#22c55e'
    if score >= 80:
        return 'B', 'GOOD', '#22c55e'
    if score >= 70:
        return 'C', 'FAIR', '#f59e0b'
    if score >= 60:
        return 'D', 'NEEDS WORK', '#f97316'
    return 'F', 'CRITICAL', '#ef4444'


def _logo(inline_assets: bool) -> str:
    if inline_assets:
        try:
            with open(_LOGO_PATH, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
            return f'<img src="data:image/png;base64,{b64}" alt="Droppin" style="height:24px;width:auto;display:block">'
        except Exception:
            pass
    return '<img src="/static/img/brand/logo-light.png" alt="Droppin" style="height:24px;width:auto;display:block">'


REPORT_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#08090b;--surface:#101218;--surface-2:#161a22;--surface-3:#1d222c;--border:rgba(255,255,255,0.07);--border-2:rgba(255,255,255,0.12);--text:#e8ecf2;--muted:#8a93a5;--faint:#5b6472;--accent:#5b2ef5;--accent-2:#7c5cff;--good:#22c55e;--good-bg:rgba(34,197,94,0.10);--warn:#f59e0b;--bad:#ef4444;--bad-bg:rgba(239,68,68,0.10);--crit:#ef4444;--high:#f97316;--med:#f59e0b;--low:#3b82f6;--pass:#22c55e;--mono:ui-monospace,"SF Mono","JetBrains Mono","Fira Code",Menlo,Consolas,monospace;--sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--text);font-family:var(--sans);line-height:1.6;-webkit-font-smoothing:antialiased;font-size:15px;background-image:radial-gradient(1200px 600px at 50% -200px,rgba(91,46,245,0.12),transparent 60%)}
a{color:inherit;text-decoration:none}
.wrap{max-width:1000px;margin:0 auto;padding:0 24px}
.toolbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;padding:12px 24px;border-bottom:1px solid var(--border);background:rgba(8,9,11,0.85);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}
.brand{display:flex;align-items:center;gap:10px}
.brand .divider{width:1px;height:20px;background:var(--border-2)}
.brand .tag{font-size:13px;font-weight:600;color:var(--muted)}
.toolbar-actions{display:flex;gap:8px;align-items:center}
.tbtn{font-size:12.5px;font-weight:550;color:var(--muted);padding:7px 14px;border-radius:8px;border:1px solid var(--border);transition:.18s;cursor:pointer}
.tbtn:hover{color:var(--text);border-color:var(--border-2)}
.tbtn.primary{background:var(--accent);color:#fff;border-color:transparent}
.tbtn.primary:hover{background:var(--accent-2)}
.verdict{display:grid;grid-template-columns:auto 1fr;gap:40px;align-items:center;padding:48px 0 8px}
.gauge{position:relative;width:180px;height:180px;flex-shrink:0}
.gauge svg{transform:rotate(-90deg)}
.gauge .track{fill:none;stroke:var(--surface-3);stroke-width:10}
.gauge .fill{fill:none;stroke-width:10;stroke-linecap:round;transition:stroke-dashoffset 1.2s cubic-bezier(.2,.7,.2,1)}
.gauge-center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.gauge-center .num{font-family:var(--mono);font-size:52px;font-weight:700;line-height:1;letter-spacing:-0.03em;font-variant-numeric:tabular-nums}
.gauge-center .grade{font-family:var(--mono);font-size:12px;font-weight:600;letter-spacing:0.2em;color:var(--muted);margin-top:5px}
.verdict-body .eyebrow{font-size:11px;font-weight:650;letter-spacing:0.16em;text-transform:uppercase;color:var(--muted);margin-bottom:12px}
.verdict-body h1{font-size:30px;font-weight:700;letter-spacing:-0.02em;line-height:1.15;text-wrap:balance}
.verdict-body .url{font-family:var(--mono);font-size:12.5px;color:var(--accent-2);margin-top:8px}
.verdict-body .meta{font-size:12.5px;color:var(--faint);margin-top:2px}
.verdict-line{margin-top:16px;font-size:16.5px;color:var(--muted);max-width:560px;line-height:1.55}
.verdict-line b{color:var(--text);font-weight:600}
.verdict-line .hot{color:var(--crit);font-weight:700}
.sev-strip{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;padding:24px 0 4px}
.sev{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.sev .n{font-family:var(--mono);font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}
.sev .l{font-size:11px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:var(--muted);margin-top:2px}
.sev.crit{border-top:2px solid var(--crit)}.sev.crit .n{color:var(--crit)}
.sev.high{border-top:2px solid var(--high)}.sev.high .n{color:var(--high)}
.sev.med{border-top:2px solid var(--med)}.sev.med .n{color:var(--med)}
.sev.low{border-top:2px solid var(--low)}.sev.low .n{color:var(--low)}
.sev.pass{border-top:2px solid var(--pass)}.sev.pass .n{color:var(--pass)}
section{margin:44px 0}
.sec-head{display:flex;align-items:baseline;gap:12px;margin-bottom:18px}
.sec-head .kicker{font-size:11px;font-weight:650;letter-spacing:0.16em;text-transform:uppercase;color:var(--accent-2)}
.sec-head h2{font-size:20px;font-weight:680;letter-spacing:-0.015em}
.priorities{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.prio{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px;display:flex;flex-direction:column;gap:12px;position:relative;overflow:hidden}
.prio::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px}
.prio.crit::before{background:var(--crit)}.prio.high::before{background:var(--high)}.prio.med::before{background:var(--med)}
.prio .rank{font-family:var(--mono);font-size:11px;font-weight:700;letter-spacing:0.12em;color:var(--faint);text-transform:uppercase}
.prio h3{font-size:15.5px;font-weight:650;letter-spacing:-0.01em;line-height:1.3}
.prio .impact{font-size:13px;color:var(--muted);line-height:1.5}
.prio .impact b{color:var(--text);font-weight:600}
.prio .lock{font-size:12px;color:var(--faint);display:flex;align-items:center;gap:6px;margin-top:auto;padding-top:4px}
.prio .lock svg{width:12px;height:12px;stroke:var(--faint);flex-shrink:0}
.prio .fix{font-family:var(--mono);font-size:12px;color:#c6cdd8;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:9px 11px;line-height:1.5;white-space:pre-wrap;word-break:break-word;margin-top:auto}
.bars{display:flex;flex-direction:column;gap:14px}
.bar-row{display:grid;grid-template-columns:200px 1fr 52px;align-items:center;gap:16px}
.bar-label{font-size:13.5px;font-weight:550;color:var(--text)}
.bar-label .wt{display:block;font-family:var(--mono);font-size:11px;color:var(--faint)}
.bar-track{background:var(--surface-2);border-radius:6px;height:9px;overflow:hidden}
.bar-fill{height:100%;border-radius:6px}
.bar-val{font-family:var(--mono);font-size:14px;font-weight:650;text-align:right;font-variant-numeric:tabular-nums}
.locked{position:relative;border:1px solid var(--border);border-radius:16px;overflow:hidden;background:var(--surface)}
.locked-list{filter:blur(6px);opacity:0.5;user-select:none;pointer-events:none;padding:20px;display:flex;flex-direction:column;gap:10px;transform:scale(1.02)}
.lcard{background:var(--surface-2);border:1px solid var(--border);border-radius:10px;padding:14px 16px;position:relative;overflow:hidden}
.lcard::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px}
.lcard.crit::before{background:var(--crit)}.lcard.high::before{background:var(--high)}.lcard.med::before{background:var(--med)}.lcard.low::before{background:var(--low)}
.lcard .lt{height:11px;width:58%;background:var(--surface-3);border-radius:4px;margin-bottom:8px}
.lcard .lb{height:8px;width:86%;background:var(--surface-3);border-radius:4px}
.lcard .lb.s{width:64%}
.lcard .chip{display:inline-block;height:12px;width:52px;border-radius:4px;margin-bottom:8px}
.lcard.crit .chip{background:var(--crit)}.lcard.high .chip{background:var(--high)}.lcard.med .chip{background:var(--med)}.lcard.low .chip{background:var(--low)}
.lock-overlay{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:32px 24px;background:radial-gradient(700px 400px at 50% 100%,rgba(91,46,245,0.18),transparent 65%),linear-gradient(180deg,rgba(8,9,11,0.55) 0%,rgba(8,9,11,0.92) 70%)}
.lock-icon{width:56px;height:56px;border-radius:16px;background:var(--accent);display:flex;align-items:center;justify-content:center;box-shadow:0 8px 30px rgba(91,46,245,0.3);margin-bottom:18px}
.lock-icon svg{width:26px;height:26px;stroke:#fff}
.lock-overlay h3{font-size:22px;font-weight:700;letter-spacing:-0.02em}
.lock-overlay p{color:var(--muted);font-size:14.5px;max-width:460px;margin-top:8px;line-height:1.55}
.lock-overlay .count{display:inline-block;font-family:var(--mono);font-size:13px;color:var(--crit);background:var(--bad-bg);border:1px solid rgba(239,68,68,0.3);border-radius:100px;padding:4px 12px;margin-top:14px;font-weight:650}
.lock-overlay .btn{margin-top:20px}
.lock-overlay .proof{font-size:12px;color:var(--faint);margin-top:12px}
.btn{font-size:14px;font-weight:620;padding:12px 22px;border-radius:10px;display:inline-flex;align-items:center;gap:8px;cursor:pointer;transition:.18s;border:1px solid transparent}
.btn-p{background:var(--accent);color:#fff}.btn-p:hover{background:var(--accent-2);transform:translateY(-1px)}
.btn-s{background:transparent;color:var(--text);border-color:var(--border-2)}.btn-s:hover{border-color:var(--accent-2)}
.cta{background:linear-gradient(135deg,#231857 0%,#171238 55%,#0e0c1c 100%);border:1px solid rgba(91,46,245,0.32);border-radius:16px;padding:36px 38px;position:relative;overflow:hidden}
.cta::after{content:"";position:absolute;right:-80px;top:-80px;width:280px;height:280px;background:radial-gradient(circle,rgba(91,46,245,0.25),transparent 70%)}
.cta h2{font-size:24px;font-weight:700;letter-spacing:-0.02em;line-height:1.2}
.cta p{color:var(--muted);font-size:15px;max-width:620px;margin-top:10px;line-height:1.6}
.cta .btns{display:flex;gap:12px;margin-top:22px;flex-wrap:wrap;position:relative;z-index:1}
.cta .proof{font-size:12.5px;color:var(--faint);margin-top:16px}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
.fchip{font-size:12.5px;font-weight:550;color:var(--muted);background:var(--surface);border:1px solid var(--border);border-radius:100px;padding:7px 14px;cursor:pointer;transition:.15s}
.fchip:hover{color:var(--text);border-color:var(--border-2)}
.fchip.on{background:var(--accent);color:#fff;border-color:transparent}
.finding{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin-bottom:10px;position:relative}
.finding::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;border-radius:12px 0 0 12px}
.finding.crit::before{background:var(--crit)}.finding.high::before{background:var(--high)}.finding.med::before{background:var(--med)}.finding.low::before{background:var(--low)}.finding.info::before{background:var(--low)}
.finding .top{display:flex;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap}
.tag{font-size:10.5px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;padding:3px 9px;border-radius:5px}
.tag.crit{background:var(--crit);color:#fff}.tag.high{background:var(--high);color:#fff}.tag.med{background:var(--med);color:#1a1304}.tag.low{background:var(--low);color:#fff}.tag.info{background:#475569;color:#fff}.tag.pass{background:var(--pass);color:#fff}
.mod{font-family:var(--mono);font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:0.06em}
.finding h4{font-size:14.5px;font-weight:620;letter-spacing:-0.01em}
.finding .impact{font-size:13px;color:var(--muted);margin-top:6px;line-height:1.5}
.finding .detail{font-size:13.5px;color:var(--muted);margin-top:5px;line-height:1.55}
.finding .impact b{color:var(--text)}
.finding .fix{font-family:var(--mono);font-size:12px;color:#c6cdd8;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:11px 13px;margin-top:10px;line-height:1.6;white-space:pre-wrap;word-break:break-word}
.fix-label{font-size:11px;font-weight:650;letter-spacing:0.08em;text-transform:uppercase;color:var(--accent-2);margin-top:12px;display:block}
.copy-panel{background:var(--surface);border:1px solid var(--accent);border-radius:14px;overflow:hidden}
.copy-head{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 22px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.copy-head .t{font-size:15px;font-weight:650}
.copy-head .t span{color:var(--muted);font-weight:450;font-size:13px;display:block;margin-top:2px}
.copy-btn{font-size:13px;font-weight:620;color:#fff;background:var(--accent);border:none;border-radius:8px;padding:9px 16px;cursor:pointer;transition:.18s;font-family:var(--sans)}
.copy-btn:hover{background:var(--accent-2)}
.copy-btn.done{background:var(--good)}
.brief{background:var(--bg);padding:20px 22px;font-family:var(--mono);font-size:12px;line-height:1.7;color:#c6cdd8;overflow-x:auto;white-space:pre-wrap;word-break:break-word;max-height:340px;overflow-y:auto}
.passing{display:flex;flex-wrap:wrap;gap:8px}
.pchip{font-size:12.5px;color:var(--good);background:var(--good-bg);border:1px solid rgba(34,197,94,0.22);border-radius:100px;padding:7px 13px}
.footer{margin-top:56px;padding:28px 0 40px;border-top:1px solid var(--border);display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:center}
.footer .l{font-size:12.5px;color:var(--faint)}
.footer .r{display:flex;gap:16px;font-size:12.5px;color:var(--muted);align-items:center}
.footer a{color:var(--muted)}.footer a:hover{color:var(--text)}
@media(max-width:760px){.verdict{grid-template-columns:1fr;gap:24px;padding-top:32px}.gauge{margin:0 auto}.sev-strip{grid-template-columns:repeat(3,1fr)}.priorities{grid-template-columns:1fr}.bar-row{grid-template-columns:130px 1fr 44px;gap:10px}.cta{padding:28px 24px}}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""


# JS is a plain template — replace __SCORE__ placeholder to avoid f-string brace escaping.
_REPORT_JS = """
(function(){
  var n=document.getElementById('score-num');
  if(!n) return;
  var target=__SCORE__;
  if(window.matchMedia('(prefers-reduced-motion:reduce)').matches){n.textContent=target;return;}
  var cur=0,t0=null;
  function tick(ts){if(!t0)t0=ts;var p=Math.min(1,(ts-t0)/1000);var e=1-Math.pow(1-p,3);n.textContent=Math.round(cur+(target-cur)*e);if(p<1)requestAnimationFrame(tick);}
  requestAnimationFrame(tick);
})();
(function(){
  var chips=document.querySelectorAll('.fchip');
  var findings=document.querySelectorAll('.finding');
  if(!chips.length) return;
  chips.forEach(function(c){c.addEventListener('click',function(){
    chips.forEach(function(x){x.classList.remove('on')});c.classList.add('on');
    var s=c.getAttribute('data-sev');
    findings.forEach(function(f){if(s==='all'){f.style.display='';}else{f.style.display=(f.getAttribute('data-sev')===s)?'':'none';}});
  });});
})();
(function(){
  var btn=document.getElementById('copyBtn');
  var brief=document.getElementById('brief');
  if(!btn||!brief) return;
  var txt=brief.innerText;
  btn.addEventListener('click',function(){
    function done(){btn.textContent='Copied ✓';btn.classList.add('done');setTimeout(function(){btn.textContent='Copy technical brief';btn.classList.remove('done');},1800);}
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(txt).then(done).catch(done);}
    else{var ta=document.createElement('textarea');ta.value=txt;document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);done();}
  });
})();
"""

_SEV_COLOR = {
    'critical': '#ef4444', 'high': '#f97316', 'medium': '#f59e0b',
    'low': '#3b82f6', 'info': '#475569', 'pass': '#22c55e', 'fixed': '#8b5cf6',
}
_SEV_LABEL = {'critical': 'Critical', 'high': 'High', 'medium': 'Medium', 'low': 'Low', 'info': 'Info', 'pass': 'Pass', 'fixed': 'Fixed'}
_SEV_CLS = {'critical': 'crit', 'high': 'high', 'medium': 'med', 'low': 'low', 'info': 'info', 'pass': 'pass', 'fixed': 'fixed'}


def _top_findings(findings, n):
    """Top-N findings by severity (critical first, then high, etc.)."""
    ordered = sorted(findings, key=lambda f: _SEV_ORDER.get(f.get('severity', 'info'), 5))
    return ordered[:n]


def _plain_title(title):
    """Split 'Technical part — plain consequence' and prefer the consequence."""
    for sep in ('—', ' - ', ':'):
        if sep in title:
            parts = title.split(sep, 1)
            if len(parts[1].strip()) > 6:
                return parts[1].strip()
    return title.strip()


def _bar_color(score):
    return '#22c55e' if score >= 70 else '#f59e0b' if score >= 40 else '#ef4444'


def _verdict_line(score, n_crit, n_high):
    return f"This automated homepage screening found {n_crit + n_high} priority observations. Review the evidence and recommendations below; rankings and business impact require further analysis."


def _build_body(result, audit_id, mode, inline_assets):
    url = result.get('url', '')
    domain = result.get('domain', '')
    overall = int(result.get('overall_score', 0))
    findings = result.get('findings', [])
    page_title = result.get('page_title', '') or domain
    module_scores = result.get('module_scores', {})
    weights = result.get('scoring_weights') or MODULE_WEIGHTS
    results = result.get('results', {})

    now = html.escape(str(result.get('checked_at') or 'Date unavailable')[:10])

    critical = [f for f in findings if f.get('severity') == 'critical']
    high = [f for f in findings if f.get('severity') == 'high']
    medium = [f for f in findings if f.get('severity') == 'medium']
    low = [f for f in findings if f.get('severity') in ('low', 'info')]
    passing = [f for f in findings if f.get('severity') == 'pass']

    grade_letter, grade_label, grade_color = _grade(overall)
    circ = 339.29  # 2*pi*54
    dashoffset = round(circ * (1 - overall / 100), 1)

    # ── toolbar ──
    if mode == 'droppin':
        toolbar_actions = '<a class="tbtn primary" href="#cta">Get it fixed</a>'
    else:
        toolbar_actions = (f'<a class="tbtn" href="/auditor/">New audit</a>'
                           f'<a class="tbtn" href="/auditor/report/{audit_id}/download">Download HTML</a>'
                           f'<a class="tbtn" href="/auditor/history">History</a>')

    # ── verdict ──
    verdict = f'''
<header class="verdict">
  <div class="gauge">
    <svg width="180" height="180" viewBox="0 0 120 120">
      <circle class="track" cx="60" cy="60" r="54"/>
      <circle class="fill" cx="60" cy="60" r="54" stroke="{grade_color}" stroke-dasharray="{circ}" stroke-dashoffset="{dashoffset}"/>
    </svg>
    <div class="gauge-center"><div class="num" id="score-num" style="color:{grade_color}">{overall}</div><div class="grade">{grade_letter} · {grade_label}</div></div>
  </div>
  <div class="verdict-body">
    <div class="eyebrow">SEO Audit Report</div>
    <h1>{html.escape(page_title)}</h1>
    <div class="url">{html.escape(domain)}</div>
    <div class="meta">Audited {now} · {len(findings)} issues</div>
    <p class="verdict-line">{_verdict_line(overall, len(critical), len(high))}</p>
  </div>
</header>'''

    # ── severity strip ──
    sev_strip = f'''
<div class="sev-strip">
  <div class="sev crit"><div class="n">{len(critical)}</div><div class="l">Critical</div></div>
  <div class="sev high"><div class="n">{len(high)}</div><div class="l">High</div></div>
  <div class="sev med"><div class="n">{len(medium)}</div><div class="l">Medium</div></div>
  <div class="sev low"><div class="n">{len(low)}</div><div class="l">Low</div></div>
  <div class="sev pass"><div class="n">{len(passing)}</div><div class="l">Passing</div></div>
</div>'''

    # ── priorities (top 3) ──
    top = _top_findings([f for f in findings if f.get('severity') in ('critical', 'high', 'medium')], 3)
    prio_cards = ''
    for i, f in enumerate(top, 1):
        sev = f.get('severity', 'medium')
        sev_cls = 'crit' if sev == 'critical' else ('high' if sev == 'high' else 'med')
        title = _plain_title(f.get('title', ''))
        impact = html.escape(f.get('impact', '') or '')
        impact_html = f'<div class="impact">{impact}</div>' if impact else ''
        if mode == 'droppin':
            fix_html = '<div class="lock"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg> How we fix it → in your full report</div>'
        else:
            fix = f.get('fix', '') or ''
            fix_html = f'<div class="fix">{html.escape(fix)}</div>' if fix else ''
        prio_cards += f'''
<div class="prio {sev_cls}">
  <div class="rank">Problem {i} · {sev.title()}</div>
  <h3>{html.escape(title)}</h3>
  {impact_html}{fix_html}
</div>'''

    priorities = f'<section><div class="sec-head"><span class="kicker">Measured observations</span><h2>{"Priority observations" if mode == "droppin" else "Top 3 priorities"}</h2></div><div class="priorities">{prio_cards}</div></section>'

    # ── scorecard ──
    mods = [
        ('technical', 'Technical foundations', weights.get('technical', 40), module_scores.get('technical', 0)),
        ('geo', 'Structured data', weights.get('geo', 5), module_scores.get('geo', 0)),
        ('geo_visibility', 'AI visibility (GEO)', weights.get('geo_visibility', 15), module_scores.get('geo_visibility', 0)),
        ('performance', 'Page speed', weights.get('performance', 35), module_scores.get('performance', 0)),
        ('content', 'Content quality', weights.get('content', 5), module_scores.get('content', 0)),
        ('images', 'Images', weights.get('images', 5), module_scores.get('images', 0)),
        ('sitemap', 'Sitemap', weights.get('sitemap', 5), module_scores.get('sitemap', 0)),
        ('robots', 'Crawler access', weights.get('robots', 10), module_scores.get('robots', 0)),
        ('authority', 'Domain authority', weights.get('authority', 10), module_scores.get('authority', 0)),
    ]
    if weights.get('local_seo', 0) > 0:
        mods.append(('local_seo', 'Local SEO', 10, module_scores.get('local_seo', 0)))
    bar_rows = ''
    for _, label, wt, s in mods:
        # Skip modules with zero weight — they don't contribute to the score, so
        # rendering a bar (e.g. "0% weight · 100 score") would be misleading.
        if wt == 0:
            continue
        if s is None:
            bar_rows += f'<div class="bar-row"><span>{html.escape(label)}</span><span>Not measured</span></div>'
            continue
        c = _bar_color(s)
        bar_rows += f'<div class="bar-row"><span class="bar-label">{label}<span class="wt">{wt}%</span></span><div class="bar-track"><div class="bar-fill" style="width:{min(100, s)}%;background:{c}"></div></div><span class="bar-val" style="color:{c}">{s}</span></div>'
    scorecard = f'<section><div class="sec-head"><span class="kicker">Scorecard</span><h2>Automated website health · not a Google ranking score</h2></div><div class="bars">{bar_rows}</div></section>'

    # ── findings: locked (droppin) vs full ──
    if mode == 'droppin':
        hidden_count = max(0, len(findings) - len(top))
        lock_cards = ''
        for f in _top_findings(findings, 8):
            sev = f.get('severity', 'medium')
            cls = 'crit' if sev == 'critical' else ('high' if sev == 'high' else ('med' if sev == 'medium' else 'low'))
            lock_cards += f'<div class="lcard {cls}"><span class="chip"></span><div class="lt"></div><div class="lb"></div><div class="lb s"></div></div>'
        findings_section = f'''
<section>
  <div class="sec-head"><span class="kicker">Full report</span><h2>The other {hidden_count} issues</h2></div>
  <div class="locked">
    <div class="locked-list" aria-hidden="true">{lock_cards}</div>
    <div class="lock-overlay">
      <div class="lock-icon"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg></div>
      <h3>Your full report is locked</h3>
      <p>We found {len(findings)} issues. Here are {len(top)}. The other {hidden_count} — ranked by urgency, each with the exact fix — we'll walk you through on a free call.</p>
      <span class="count">{hidden_count} issues hidden · 1 call to unlock</span>
      <a class="btn btn-p" href="#cta">Unlock my full report →</a>
      <div class="proof">Free · No obligation · Plain English · 15 minutes</div>
    </div>
  </div>
</section>'''
    else:
        sev_counts = {}
        for f in findings:
            sev_counts[f.get('severity', 'info')] = sev_counts.get(f.get('severity', 'info'), 0) + 1
        filter_chips = f'<span class="fchip on" data-sev="all">All · {len(findings)}</span>'
        for sev_full in ('critical', 'high', 'medium', 'low', 'info'):
            if sev_counts.get(sev_full, 0):
                filter_chips += f'<span class="fchip" data-sev="{_SEV_CLS[sev_full]}">{_SEV_LABEL.get(sev_full, sev_full)} · {sev_counts[sev_full]}</span>'
        finding_rows = ''
        for f in findings:
            sev = f.get('severity', 'info')
            if sev == 'pass':
                continue
            cls = _SEV_CLS.get(sev, 'info')
            mod = html.escape(f.get('module', '').upper())
            title = html.escape(f.get('title', ''))
            detail = html.escape(f.get('detail', '') or '').replace('\n', '<br>')
            detail_html = f'<div class="detail">{detail}</div>' if detail else ''
            fix = f.get('fix', '') or ''
            fix_html = f'<span class="fix-label">Fix</span><div class="fix">{html.escape(fix)}</div>' if fix else ''
            finding_rows += f'''
<div class="finding {cls}" data-sev="{cls}">
  <div class="top"><span class="tag {cls}">{_SEV_LABEL.get(sev, sev)}</span><span class="mod">{mod}</span></div>
  <h4>{title}</h4>
  {detail_html}{fix_html}
</div>'''
        findings_section = f'''
<section id="findings">
  <div class="sec-head"><span class="kicker">Full detail</span><h2>All findings</h2></div>
  <div class="filters">{filter_chips}</div>
  {finding_rows}
</section>'''

    # ── CTA (droppin) / copy-for-AI (full) ──
    if mode == 'droppin':
        cta_section = '''
<section id="cta">
  <div class="cta">
    <h2>Droppin fixes this for you.</h2>
    <p>We audit, fix, and monitor your website so you show up when customers search. We explain the measured findings and help you decide what to improve.</p>
    <div class="btns">
      <a class="btn btn-p" href="https://getdroppin.ai/contact">Get your free fix plan →</a>
      <a class="btn btn-s" href="https://getdroppin.ai/contact">Book a free call</a>
    </div>
    <div class="proof">No contracts. A plain-English plan in 24 hours.</div>
  </div>
</section>'''
    else:
        brief_lines = [f'SEO AUDIT — {domain}', f'Score {overall}/100 ({grade_letter}) · {len(findings)} issues · 8 modules checked', '']
        current_sev = None
        for f in findings:
            sev = f.get('severity', 'info')
            if sev in ('pass',):
                continue
            if sev != current_sev:
                brief_lines.append(f'{_SEV_LABEL.get(sev, sev).upper()} ({sev_counts.get(sev, 0)})')
                current_sev = sev
            brief_lines.append(f'- {f.get("title", "")}')
            if f.get('fix'):
                brief_lines.append(f'  Fix: {f["fix"].replace(chr(10), " ")}')
        brief_text = '\n'.join(brief_lines)
        cta_section = f'''
<section id="copy">
  <div class="sec-head"><span class="kicker">Hand it to AI</span><h2>Fix everything with AI</h2></div>
  <div class="copy-panel">
    <div class="copy-head">
      <div class="t">Copy the full technical brief<span>Paste it into Claude or ChatGPT and ask it to apply every fix.</span></div>
      <button class="copy-btn" id="copyBtn">Copy technical brief</button>
    </div>
    <pre class="brief" id="brief">{html.escape(brief_text)}</pre>
  </div>
</section>'''

    # ── what's working ──
    pass_chips = ''.join(f'<span class="pchip">✓ {html.escape(f.get("title", ""))}</span>' for f in passing[:8])
    working_section = f'<section><div class="sec-head"><span class="kicker">Credit where due</span><h2>What\'s already working</h2></div><div class="passing">{pass_chips}</div></section>' if pass_chips else ''

    footer = f'''
<footer class="footer">
  <div class="l">Generated by Droppin Site Audit · Audit ID {audit_id}</div>
  <div class="r"><a href="/auditor/">New audit</a><a href="/auditor/history">History</a></div>
</footer>'''

    return f'''
<div class="toolbar">
  <div class="brand">{_logo(inline_assets)}<span class="divider"></span><span class="tag">Site Audit</span></div>
  <div class="toolbar-actions">{toolbar_actions}</div>
</div>
<div class="wrap">
  {verdict}
  {sev_strip}
  {priorities}
  {scorecard}
  {findings_section}
  {cta_section}
  {working_section}
  {footer}
</div>'''


def build_report_html(result: dict, audit_id: str, mode: str = 'full', inline_assets: bool = False) -> str:
    """Render a standalone report. mode: 'droppin' | 'full'."""
    # No grade at all → show the incomplete page. For the lead-gen (droppin) mode
    # we also hide a partial grade: a report that isn't "commercial ready" must
    # not advertise a score. In full mode a partial audit still shows its grade.
    if result.get('overall_score') is None or (mode == 'droppin' and not result.get('commercial_ready')):
        reason = html.escape(str(result.get('error') or 'Some checks could not be completed. No reliable overall grade is available.'))
        return '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><title>Audit incomplete</title><body><h1>Audit incomplete</h1><p>' + reason + '</p><p>This does not imply that the website is poor. Please retry the analysis.</p></body></html>'
    overall = int(result['overall_score'])
    body = _build_body(result, audit_id, 'full', inline_assets)
    js = _REPORT_JS.replace('__SCORE__', str(overall))
    title = html.escape(result.get('domain', 'site'))
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>SEO Audit — {title}</title>
<style>{REPORT_CSS}</style>
</head>
<body>
{body}
<script>{js}</script>
</body>
</html>'''
