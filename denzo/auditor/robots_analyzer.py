"""Evaluate crawler rules for the audited URL, with group and Allow precedence."""
import re
from urllib.parse import urlsplit, quote
from denzo.auditor.safe_fetch import fetch_html

AI_CRAWLERS = ('GPTBot', 'ClaudeBot', 'PerplexityBot', 'Google-Extended', 'CCBot')


def parse_robots(text):
    groups, agents, rules, sitemaps = [], [], [], []
    directives = False
    for raw in text.splitlines():
        line = raw.lstrip('\ufeff').split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, value = [x.strip() for x in line.split(':', 1)]
        key = key.lower()
        if key == 'sitemap':
            if value: sitemaps.append(value)
        elif key == 'user-agent':
            if directives:
                groups.append((agents, rules))
                agents, rules, directives = [], [], False
            if value: agents.append(value.lower())
        elif key in ('allow', 'disallow') and agents:
            directives = True
            if value: rules.append((key == 'allow', value))
        elif key == 'crawl-delay' and agents:
            directives = True
    if agents: groups.append((agents, rules))
    return groups, sitemaps


def normalized_path(value):
    # Normalize escaped unreserved octets; preserve reserved escapes and case.
    value = quote(value, safe="/%*?$&=:+,;@!~()[]'-._")
    def replace(match):
        char = chr(int(match.group(1), 16))
        return char if re.fullmatch(r'[A-Za-z0-9._~-]', char) else match.group().upper()
    return re.sub(r'%([a-fA-F0-9]{2})', replace, value)


def can_fetch(groups, agent, path):
    matches = []
    for agents, rules in groups:
        specificity = max([len(a) for a in agents if a != '*' and agent.lower().startswith(a)] or ([0] if '*' in agents else [-1]))
        if specificity >= 0: matches.append((specificity, rules))
    if not matches: return True
    best = max(n for n, _ in matches)
    applicable = [rule for n, rules in matches if n == best for rule in rules]
    rules_matching = []
    for allow, pattern in applicable:
        pattern = normalized_path(pattern)
        end = pattern.endswith('$')
        body = pattern[:-1] if end else pattern
        expression = '^' + '.*'.join(re.escape(part) for part in body.split('*')) + ('$' if end else '')
        if re.search(expression, normalized_path(path)):
            rules_matching.append((len(body.replace('*', '').encode('utf-8')), allow))
    # Most specific path wins; Allow wins an equally specific tie.
    return max(rules_matching, default=(0, True))[1]


def analyze_robots(url, html, domain):
    parsed = urlsplit(url)
    robots_url = f'{parsed.scheme}://{parsed.netloc}/robots.txt'
    try:
        res = fetch_html(robots_url, allow_jina=False)
        status = res.get('status')
        if status in (404, 410):
            text = ''
        elif res.get('ok'):
            text = res.get('html', '')
            if re.search(r'<(?:!doctype|html|body)\b', text, re.I):
                raise ValueError('Expected robots text, received an HTML page')
        else:
            raise ValueError('Unverified robots response')
        groups, sitemaps = parse_robots(text)
        path = parsed.path or '/'
        if parsed.query: path += '?' + parsed.query
        allowed = can_fetch(groups, 'Googlebot', path)
        blocked_ai = [a for a in AI_CRAWLERS if not can_fetch(groups, a, path)]
        evidence = dict(source='robots_txt', url=robots_url, page_url=url, googlebot_allowed=allowed)
        findings = [dict(rule_id='robots_page_access', module='robots',
            severity='info' if allowed else 'high',
            title='Googlebot may crawl this URL under the detected rules' if allowed else 'Robots rules restrict Googlebot from crawling this URL',
            detail='This evaluates the requested path and user-agent precedence. It does not prove whether the URL is indexed or whether every crawler obeys the rules.',
            fix=None if allowed else 'Review whether the restriction is intentional before changing robots.txt.',
            evidence=evidence, deduction=0 if allowed else 50)]
        if blocked_ai:
            findings.append(dict(rule_id='ai_crawler_policy', module='robots', severity='info',
                title='Some AI crawlers are restricted for this URL',
                detail='This can be an intentional access policy and does not change the Googlebot result or score.',
                fix=None, deduction=0, evidence=dict(source='robots_txt', url=robots_url, agents=blocked_ai)))
        return dict(score=100 if allowed else 50, status='completed', findings=findings, robots_url=robots_url,
                    sitemap_refs=sitemaps, ai_crawlers_blocked=blocked_ai,
                    ai_crawlers_allowed=[a for a in AI_CRAWLERS if a not in blocked_ai],
                    total_rules=sum(len(rules) for _, rules in groups), googlebot_allowed=allowed)
    except Exception:
        return dict(score=None, status='unavailable', findings=[], error='Could not verify robots.txt')
