"""Exercise real rule execution; only outbound HTTP/PageSpeed are fixtures."""
import json
import pytest
from denzo.auditor import analyzer, robots_analyzer, sitemap_analyzer, performance_estimator
from denzo.auditor.onpage_evidence import analyze_onpage
from denzo.auditor.scoring import METHODOLOGY_VERSION

PAGE = '''<!doctype html><html lang="en"><head><title>Example Studio</title>
<meta name="description" content="Visit our local studio."><meta name="viewport" content="width=device-width,initial-scale=1">
</head><body><h1>Welcome to our studio</h1><p>Talk to our team about your next visit.</p>
<img src="decoration.png" alt=""><img src="brand.png" role="presentation">
<a href="http://external.example">More information</a><a href="http://two.example">Second link</a>
<a href="http://three.example">Third link</a><script>''' + 'fake words '*1500 + '''</script></body></html>'''


def test_short_content_decorative_images_and_http_links_are_not_fabricated_defects():
    result = analyze_onpage('https://example.com/', PAGE)
    assert result['technical']['word_count'] < 80
    assert all(result[n]['score']==100 for n in ('technical','images','content'))
    # No JSON-LD in PAGE → structured data is correctly flagged as absent (not 100).
    assert result['geo']['score'] == 0
    assert not any('ranking' in f['title'].lower() for r in result.values() for f in r['findings'])


def test_actual_noindex_missing_title_and_asset_protocol_have_traceable_deductions():
    html = PAGE.replace('<title>Example Studio</title>','').replace('</head>','<meta name="robots" content="noindex"></head>')
    html = html.replace('decoration.png','http://example.com/photo.jpg')
    result = analyze_onpage('https://example.com/', html)['technical']
    rules = {f['rule_id'] for f in result['findings']}
    assert {'noindex','title_missing','mixed_assets'} <= rules
    assert result['score'] == 30
    assert all(f['evidence']['url']=='https://example.com/' for f in result['findings'])


@pytest.mark.parametrize('text,agent,path,allowed', [
    ('User-agent: *\nDisallow: /\nUser-agent: Googlebot\nAllow: /','Googlebot','/',True),
    ('User-agent: *\nDisallow: /\nAllow: /public/','Googlebot','/public/page',True),
    ('User-agent: *\nDisallow: /\nAllow: /','Googlebot','/',True),
    ('User-agent: *\nDisallow: /$','Googlebot','/page',True),
    ('User-agent: Googlebot\nUser-agent: Bingbot\nDisallow: /','Googlebot','/',False),
    ('User-agent: Googlebot\nDisallow: /\nUser-agent: Googlebot\nAllow: /news','Googlebot','/news',True),
    ('User-agent: *\nDisallow: /file*.pdf$','Googlebot','/file123.pdf',False),
    ('User-agent: *\nDisallow: /caf%C3%A9','Googlebot','/café',False),
    ('User-agent: *\nDisallow: /%61','Googlebot','/a',False),
])
def test_robots_group_and_longest_rule_precedence(text, agent, path, allowed):
    groups, _ = robots_analyzer.parse_robots(text)
    assert robots_analyzer.can_fetch(groups,agent,path) is allowed


def test_missing_sitemap_is_a_real_gap_and_counts_are_never_extrapolated(monkeypatch):
    monkeypatch.setattr(sitemap_analyzer,'_fetch',lambda _:None)
    missing = sitemap_analyzer.analyze_sitemap('https://example.com/', PAGE,'example.com')
    assert missing['score']==0
    assert missing['findings'][0]['severity']=='medium'
    docs = {
      'https://example.com/robots.txt':None,
      'https://example.com/sitemap.xml':'<sitemapindex><sitemap><loc>https://example.com/a.xml</loc></sitemap><sitemap><loc>https://example.com/b.xml</loc></sitemap></sitemapindex>',
      'https://example.com/a.xml':'<urlset><url><loc>https://example.com/a</loc></url></urlset>',
      'https://example.com/b.xml':'<urlset><url><loc>https://example.com/b</loc></url><url><loc>https://example.com/c</loc></url></urlset>',
    }
    monkeypatch.setattr(sitemap_analyzer,'_fetch',lambda url:docs[url])
    measured=sitemap_analyzer.analyze_sitemap('https://example.com/',PAGE,'example.com')
    assert measured['total_urls']==3
    assert measured['is_sample'] is True
    assert measured['sampled_children']==2


def actual_report(monkeypatch, *, performance=95):
    def fetch(url, **kwargs):
        if url.endswith('/robots.txt'): return dict(ok=True,status=200,html='User-agent: *\nAllow: /')
        if 'sitemap' in url: return dict(ok=False,status=404,html='')
        return dict(ok=True,status=200,html=PAGE,final_url='https://www.example.com/',
                    headers={'Content-Type':'text/html'},redirect_chain=[url,'https://www.example.com/'])
    monkeypatch.setattr(analyzer,'fetch_html',fetch)
    monkeypatch.setattr(robots_analyzer,'fetch_html',fetch)
    monkeypatch.setattr(sitemap_analyzer,'fetch_html',fetch)
    monkeypatch.setattr(performance_estimator,'get_lighthouse_performance',lambda _:dict(score=performance,lab_data={},field_data={},final_url='https://www.example.com/'))
    monkeypatch.setattr(analyzer,'analyze_authority',lambda *a,**k:dict(score=50,status='completed',findings=[]))
    return analyzer.SiteAnalyzer('http://example.com/','example.com').run_full_analysis()


def test_actual_analyzer_uses_redirect_destination_and_reports_only_measured_evidence(monkeypatch):
    report=actual_report(monkeypatch)
    assert report['methodology_version']==METHODOLOGY_VERSION
    assert report['commercial_ready'] and report['coverage']==100
    assert report['overall_score']==37
    assert report['results']['technical']['score']==0
    # The analyzer must use the redirect destination (https), not the requested http URL.
    assert report['final_url'].startswith('https://')
    assert all(f.get('module') and f.get('evidence') for f in report['findings'])
    assert not any(phrase in json.dumps(report) for phrase in ['traffic loss','ranking ceiling','Estimated ranking','1-3 seconds'])


def test_actual_analyzer_does_not_publish_a_score_when_pagespeed_is_missing(monkeypatch):
    report=actual_report(monkeypatch,performance=None)
    assert report['status']=='partial'
    assert not report['commercial_ready']
    assert report['module_scores']['performance'] is None


def test_access_challenge_is_not_scored(monkeypatch):
    monkeypatch.setattr(analyzer,'fetch_html',lambda *a,**k:dict(ok=True,status=200,html='<html><form id="challenge-form"></form></html>'))
    result=analyzer.SiteAnalyzer('https://example.com/','example.com').run_full_analysis()
    assert result['status']=='failed' and result['overall_score'] is None


def test_other_crawler_noindex_header_does_not_become_google_indexing_issue():
    result=analyze_onpage('https://example.com/',PAGE,{'X-Robots-Tag':'otherbot: noindex, nofollow'})
    assert result['technical']['score']==100

if __name__ == '__main__':
    cases=[]
    for label, performance in [('complete',95),('partial',None)]:
        with pytest.MonkeyPatch.context() as mp:
            report=actual_report(mp,performance=performance)
        cases.append(dict(label=label,expected_status='completed' if label=='complete' else 'partial',
            response=dict(audit_id='contract-fixture',url=report['url'],status=report['status'],
                overall_score=report['overall_score'] if report['commercial_ready'] else None,report=report)))
    print(json.dumps(cases,ensure_ascii=False))
