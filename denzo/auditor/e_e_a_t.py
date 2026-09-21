"""E-E-A-T signals (Experience, Expertise, Authoritativeness, Trustworthiness).

Google's quality-rater guidelines reward sites that demonstrate who is behind
the content and why they can be trusted. These checks are observable from the
homepage (links, bylines, credentials, social proof) and complement the
word-count/readability/originality signals already in content_quality.py.
"""
import re
from bs4 import BeautifulSoup


def analyze_e_e_a_t(url, html, domain, industry_profile=None):
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('script,style,noscript,template'):
        node.decompose()
    text = (soup.body or soup).get_text(' ', strip=True)
    text_lower = text.lower()

    anchors = [(a.get('href', ''), a.get_text(' ', strip=True).lower()) for a in soup.find_all('a', href=True)]
    href_lower = [h.lower() for h, _ in anchors]
    anchor_text = ' '.join(t for _, t in anchors)

    meta_author = soup.find('meta', attrs={'name': re.compile('author', re.I)})

    # ── Signal detectors (each returns True when the signal is PRESENT) ─────
    has_privacy = any('privacy' in t or 'privacidad' in t for _, t in anchors) or any('privacy' in h for h in href_lower)
    has_terms = any(('terms' in t or 'términos' in t or 'legal' in t or 'condiciones' in t) for _, t in anchors) or any('terms' in h or 'legal' in h for h in href_lower)
    has_contact = any(('contact' in t or 'contacto' in t) for _, t in anchors) or any('contact' in h for h in href_lower)
    has_about = any(('about' in t or 'nosotros' in t or 'quienes somos' in t or 'conócenos' in t) for _, t in anchors) or any('about' in h for h in href_lower)

    has_author = bool(meta_author) or bool(re.search(r'\b(written|posted|authored|reviewed)\s+by\b|by\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+', text)) or bool(re.search(r'\b(autor|escrito por|redactado por)\b', text_lower))
    has_credentials = bool(re.search(r'\b(certif|licens|accredit|phd|degree|credential|board.?certified|especialista|certificado|colegiado|titulado)\w*', text_lower))

    has_testimonials = bool(re.search(r'\b(testimonial|review|rating|case stud|before.?after|what (our )?clients say|resenas|opiniones|casos de exito)\w*', text_lower)) or bool(soup.find_all(['div', 'section'], class_=lambda c: c and any(w in str(c).lower() for w in ['review', 'testimonial', 'rating'])))
    has_years = bool(re.search(r'\b\d+\s*(years|años)\b', text_lower))

    has_awards = bool(re.search(r'\b(award|accreditation|affiliat|member of|association|certif|premio|galardón|miembro de|asociación|reconocido por)\w*', text_lower))

    # ── Ordered checks: (rule_id, present, title, detail, fix, deduction) ──
    checks = [
        ('eeat_privacy', has_privacy, 'No privacy policy link found',
         'A privacy policy is a baseline trust signal for Google and users, especially for a business collecting any data.',
         'Add a dedicated privacy policy page and link it in the footer.', 15),
        ('eeat_contact', has_contact, 'No contact page or contact info found',
         'A clear contact path (phone, address or form) is a core trust signal and a ranking factor for local queries.',
         'Add a contact page with phone, address and hours, linked sitewide.', 15),
        ('eeat_about', has_about, 'No About/Company page found',
         'An About page lets you state who you are, your history and your expertise — a key E-E-A-T signal.',
         'Add an About page describing the business, team and credentials, and link it from the homepage.', 10),
        ('eeat_terms', has_terms, 'No terms/legal page found',
         'Terms of service or a legal notice reinforces legitimacy, especially for financial, medical or transactional businesses.',
         'Add a terms/legal page and link it in the footer.', 10),
        ('eeat_author', has_author, 'No author or byline detected',
         'Naming an author (person or organization) demonstrates accountability and expertise to Google.',
         'Add an author byline and author metadata (schema Person or Organization) to the page.', 10),
        ('eeat_credentials', has_credentials, 'No credentials or qualifications mentioned',
         'Certifications, licenses, degrees or years of experience are direct expertise signals.',
         'Surface credentials (certifications, licenses, awards, experience) in visible text.', 10),
        ('eeat_awards', has_awards, 'No awards, certifications or affiliations mentioned',
         'Third-party recognition (awards, memberships, accreditations) builds authoritativeness.',
         'Mention industry memberships, certifications or awards, with links where possible.', 15),
        ('eeat_testimonials', has_testimonials, 'No testimonials, reviews or case studies found',
         'Social proof (reviews, testimonials, case studies) demonstrates real-world experience and results.',
         'Add customer testimonials, reviews or case studies to the page.', 10),
        ('eeat_years', has_years, 'No years-in-business stated',
         'Stating how long the business has operated is a simple, strong experience signal.',
         'Mention "X years in business" or the founding year in visible text.', 5),
    ]

    deductions = 0
    findings = []
    for rule_id, present, title, detail, fix, deduction in checks:
        if present:
            findings.append(dict(rule_id=rule_id, module='e_e_a_t', severity='pass',
                                 title=title.replace('No ', 'Has ').replace(' found', '').replace(' detected', '').replace(' mentioned', ''),
                                 detail='Signal present.', fix=None, deduction=0,
                                 evidence=dict(source='fetched_homepage_html', url=url)))
        else:
            deductions += deduction
            findings.append(dict(rule_id=rule_id, module='e_e_a_t',
                                 severity='high' if deduction >= 15 else ('medium' if deduction >= 10 else 'low'),
                                 title=title, detail=detail, fix=fix, deduction=deduction,
                                 evidence=dict(source='fetched_homepage_html', url=url)))

    return dict(score=max(0, 100 - deductions), status='completed',
                findings=findings, deductions=deductions)
