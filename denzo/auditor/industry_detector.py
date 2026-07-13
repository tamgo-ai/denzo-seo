"""
Industry Detector — Identifies business type, services, and attributes from URL content.
Used by the Site Auditor to adapt ALL recommendations to the actual business,
NOT hardcoded assumptions.

Returns an IndustryProfile that every audit module uses to generate
relevant, contextual recommendations.
"""

import re
import json
from typing import Optional
from bs4 import BeautifulSoup

# ── Quick HTML-based detection (fast path, no AI) ──────────────────────────

def _extract_text_content(html: str) -> str:
    """Strip HTML tags and get visible text."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:8000]  # First 8K chars is enough for detection


def _extract_meta(html: str) -> dict:
    """Extract title, meta description, and H1 using BeautifulSoup."""
    soup = BeautifulSoup(html, 'html.parser')
    title_tag = soup.find('title')
    title = title_tag.get_text(strip=True) if title_tag else ''

    desc_tag = soup.find('meta', attrs={'name': 'description'})
    description = desc_tag.get('content', '').strip() if desc_tag else ''

    h1_tag = soup.find('h1')
    h1_text = h1_tag.get_text(strip=True) if h1_tag else ''

    return {
        'title': re.sub(r'\s+', ' ', title).strip(),
        'description': re.sub(r'\s+', ' ', description).strip(),
        'h1': re.sub(r'\s+', ' ', h1_text).strip(),
    }


def _detect_from_schema(html: str) -> dict:
    """Extract industry from JSON-LD schema @type — much more reliable than keywords."""
    import json as _json
    schema_types = []
    try:
        matches = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL)
        for match in matches:
            try:
                data = _json.loads(match.strip())
                items = []
                if isinstance(data, dict):
                    if '@graph' in data:
                        items = data['@graph']
                    elif '@type' in data:
                        items = [data]
                elif isinstance(data, list):
                    items = data
                for item in items:
                    if isinstance(item, dict):
                        stype = item.get('@type', '')
                        if isinstance(stype, list):
                            schema_types.extend(stype)
                        elif stype:
                            schema_types.append(stype)
            except: pass
    except: pass

    # Map schema types to industries
    schema_to_industry = {
        'MedicalBusiness': 'healthcare',
        'DiagnosticLab': 'healthcare',
        'MedicalClinic': 'healthcare',
        'Hospital': 'healthcare',
        'Physician': 'healthcare',
        'Dentist': 'healthcare',
        'AutoBodyShop': 'auto_body_shop',
        'AutoRepair': 'auto_body_shop',
        'AutomotiveBusiness': 'auto_body_shop',
        'LocalBusiness': 'local_service',
        'Restaurant': 'restaurant',
        'RealEstateAgent': 'real_estate',
        'LegalService': 'legal',
        'Attorney': 'legal',
        'School': 'education',
        'CollegeOrUniversity': 'education',
        'SoftwareApplication': 'tech_saas',
        'WebApplication': 'tech_saas',
        'Store': 'ecommerce',
        'OnlineStore': 'ecommerce',
    }

    detected = []
    for st in schema_types:
        for schema_type, industry in schema_to_industry.items():
            if schema_type in st:
                detected.append(industry)

    return {
        'schema_types': list(set(schema_types)),
        'schema_industry': list(set(detected)),
        'has_medical_schema': any(t in str(schema_types) for t in ['Medical', 'Diagnostic', 'Hospital', 'Physician', 'Clinic', 'Health']),
        'has_organization_schema': any('Organization' in t for t in schema_types),
    }


def quick_detect(html: str) -> dict:
    """
    Fast keyword-based industry detection. Returns a preliminary profile
    that Claude can refine.
    """
    text = _extract_text_content(html).lower()
    meta = _extract_meta(html)
    combined = f"{meta['title']} {meta['description']} {meta['h1']} {text[:3000]}".lower()

    # ── Industry signals ─────────────────────────────────────────────────
    signals = {
        'auto_body_shop': [
            'auto body', 'collision repair', 'body shop', 'carrocería', 'refinishing',
            'frame straightening', 'bumper repair', 'dent repair', 'hail damage',
            'aluminum repair', 'oem parts', 'certified collision', 'auto repair',
            'paint matching', 'towing service', 'fender', 'panel beating',
            # Spanish
            'taller', 'reparación', 'choque', 'colisión', 'pintura', 'enderezado',
            'carrocero', 'hojalatería', 'pintor', 'aseguradora',
        ],
        'marketing_agency': [
            'marketing agency', 'digital marketing', 'seo services', 'ppc management',
            'social media marketing', 'content marketing', 'brand strategy',
            'growth marketing', 'paid ads', 'advertising agency', 'google ads',
            'meta ads', 'marketing digital', 'agencia de marketing',
        ],
        'tech_saas': [
            'software', 'saas', 'platform', 'api', 'cloud', 'dashboard',
            'automation', 'workflow', 'integrations', 'sdk', 'developer',
            'enterprise', 'b2b', 'analytics', 'ai platform', 'machine learning',
        ],
        'healthcare': [
            'medical', 'clinic', 'hospital', 'doctor', 'patient', 'healthcare',
            'dental', 'dentist', 'radiology', 'imaging', 'diagnostic',
            'physician', 'surgery', 'treatment', 'therapy',
            # Spanish
            'médico', 'médica', 'clínica', 'hospital', 'doctor', 'paciente', 'salud',
            'imagenología', 'imagen', 'diagnóstico', 'radiología', 'ecografía',
            'ultrasonido', 'mamografía', 'resonancia', 'rayos x', 'rayos-x',
            'laboratorio', 'diagnóstico por imagen', 'medicina', 'cirugía',
            'doppler', 'tomografía', 'densitometría',
        ],
        'real_estate': [
            'real estate', 'realtor', 'property', 'homes for sale', 'listing',
            'broker', 'mortgage', 'buying', 'selling', 'rental', 'leasing',
            'commercial real estate', 'residential', 'condo', 'apartment',
            # Spanish
            'inmobiliaria', 'inmuebles', 'propiedad', 'bienes raíces', 'bienes raices',
            'alquiler', 'venta', 'compra', 'casa', 'apartamento', 'corredor',
            'agente inmobiliario', 'hipoteca',
        ],
        'legal': [
            'law firm', 'attorney', 'lawyer', 'legal services', 'litigation',
            'personal injury', 'divorce', 'bankruptcy', 'immigration',
            'criminal defense', 'corporate law',
            # Spanish
            'abogado', 'abogada', 'bufete', 'despacho jurídico', 'legal',
            'derecho', 'litigio', 'divorcio', 'inmigración', 'penal',
            'corporativo', 'firma legal', 'asesoría legal', 'consultoría jurídica',
        ],
        'ecommerce': [
            'shop', 'store', 'buy online', 'free shipping', 'add to cart',
            'checkout', 'products', 'collection', 'sale', 'discount',
        ],
        'education': [
            'university', 'college', 'school', 'academy', 'training', 'course',
            'diploma', 'certification', 'online learning', 'tutoring',
            'educational', 'curriculum',
        ],
        'personal_brand': [
            'speaker', 'consultant', 'advisor', 'coach', 'founder', 'entrepreneur',
            'personal website', 'portfolio', 'about me', 'my work',
            'keynote', 'thought leader',
        ],
        'restaurant': [
            'restaurant', 'cafe', 'bar', 'dining', 'menu', 'cuisine',
            'catering', 'food', 'chef', 'dinner', 'lunch', 'breakfast',
            'takeout', 'delivery', 'reservations',
            # Spanish
            'restaurante', 'cafetería', 'bar', 'menú', 'cocina', 'comida',
            'chef', 'cena', 'almuerzo', 'desayuno', 'domicilio', 'reservas',
            'carta', 'plato', 'gastronomía',
        ],
        'construction': [
            'construction', 'contractor', 'builder', 'renovation', 'remodeling',
            'general contractor', 'roofing', 'plumbing', 'electrical', 'hvac',
            'home improvement', 'concrete', 'landscaping',
        ],
    }

    scores = {}
    for industry, keywords in signals.items():
        score = 0
        for kw in keywords:
            if ' ' in kw:
                if kw in combined:
                    score += 1
            else:
                if re.search(r'\b' + re.escape(kw) + r'\b', combined):
                    score += 1
        if score > 0:
            scores[industry] = score

    # Sort by signal strength
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    # Detect location signals (is this a local business?)
    location_signals = ['city', 'county', 'serving', 'located in', 'address',
                        'call us', 'directions', 'nearby']
    is_local = any(sig in combined for sig in location_signals)

    # Detect multi-location
    zip_pattern = re.findall(r'\b\d{5}\b', combined)
    has_multiple_locations = len(set(zip_pattern)) > 1

    # Detect certification/manufacturer mentions
    cert_pattern = re.findall(
        r'\b(Tesla|BMW|Mercedes|Audi|Porsche|Lexus|Toyota|Honda|Ford|GM|'
        r'ISO|certified|accredited|licensed|OEM|manufacturer)\b',
        combined, re.IGNORECASE
    )
    certifications = list(set(c.lower() for c in cert_pattern))

    # Country/region detection (for adapting thresholds and patterns)
    country_signals = {
        'SV': ['el salvador', 'san salvador', 'santa ana', 'san miguel', '.sv'],
        'MX': ['méxico', 'mexico', 'cdmx', 'guadalajara', 'monterrey', '.mx'],
        'ES': ['españa', 'madrid', 'barcelona', 'valencia', 'sevilla', '.es'],
        'AR': ['argentina', 'buenos aires', 'cordoba', 'rosario', '.ar'],
        'CO': ['colombia', 'bogotá', 'medellín', 'cali', '.co'],
        'CL': ['chile', 'santiago', 'valparaíso', '.cl'],
        'PE': ['perú', 'peru', 'lima', '.pe'],
        'US': ['united states', 'california', 'texas', 'florida', 'new york', '.us'],
    }
    detected_country = None
    for country, patterns in country_signals.items():
        if any(p in combined for p in patterns):
            detected_country = country
            break

    # Phone-based country detection
    if not detected_country:
        if re.search(r'\+503', combined): detected_country = 'SV'
        elif re.search(r'\+52', combined): detected_country = 'MX'
        elif re.search(r'\+34', combined): detected_country = 'ES'
        elif re.search(r'\+54', combined): detected_country = 'AR'
        elif re.search(r'\+57', combined): detected_country = 'CO'
        elif re.search(r'\+56', combined): detected_country = 'CL'
        elif re.search(r'\+51', combined): detected_country = 'PE'
        elif re.search(r'\+1', combined): detected_country = 'US'

    # Try schema-based detection first (much more reliable)
    schema_info = _detect_from_schema(html)

    return {
        'primary_industry': ranked[0][0] if ranked else 'general_business',
        'industry_scores': dict(ranked[:5]),
        'confidence': min(1.0, ranked[0][1] / max(3, sum(s[1] for s in ranked[:3]))) if ranked else 0,
        'is_local_business': is_local,
        'has_multiple_locations': has_multiple_locations,
        'detected_certifications': certifications[:10],
        'title': meta['title'],
        'description': meta['description'],
        'h1': meta['h1'],
        'detected_country': detected_country,
        'schema_info': schema_info,
    }


# ── Claude-based deep detection ─────────────────────────────────────────────

async def deep_detect(html: str, url: str) -> dict:
    """
    Uses Claude to deeply analyze the business and return a comprehensive
    industry profile with services, audience, and competitive context.
    """
    text = _extract_text_content(html)
    meta = _extract_meta(html)
    quick = quick_detect(html)

    prompt = f"""Analyze this website and return a JSON business profile.

URL: {url}
Title: {meta['title']}
Meta Description: {meta['description']}
H1: {meta['h1']}

First 5000 chars of visible text:
{text[:5000]}

Return EXACTLY this JSON structure (no other text):
{{
  "business_name": "Name of the business",
  "industry": "primary industry category",
  "sub_industry": "more specific sub-category",
  "is_local_business": true/false,
  "has_physical_location": true/false,
  "services": ["service 1", "service 2", ...],
  "target_audience": "who they serve",
  "unique_selling_points": ["USP 1", "USP 2", ...],
  "locations": ["city, state" if local],
  "certifications_or_specialties": ["cert 1", ...],
  "competitor_examples": ["similar business type"],
  "relevant_faq_topics": ["topic 1", "topic 2", ...],
  "industry_keywords": ["keyword 1", "keyword 2", ...],
  "geo_relevant": true/false,
  "typical_word_count_benchmark": 1500,
  "ai_citation_opportunities": ["what AI models would cite this business for"]
}}

Industry options: auto_body_shop, marketing_agency, tech_saas, healthcare, real_estate, legal, ecommerce, education, personal_brand, construction, restaurant, financial_services, manufacturing, nonprofit, or a custom one.

Be SPECIFIC. Do NOT default to auto_body_shop unless the site clearly is one. Look at title, description, H1, and services mentioned."""

    try:
        from anthropic import Anthropic
        client = Anthropic()
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=600,
            messages=[{'role': 'user', 'content': prompt}],
            timeout=30.0,
        )
        text_block = resp.content.find(lambda b: b.type == 'text') if hasattr(resp.content, 'find') else resp.content[0]
        raw = text_block.text if hasattr(text_block, 'text') else str(text_block)

        # Extract JSON
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            profile = json.loads(match.group(0))
            # Merge quick detection signals
            profile['quick_signals'] = quick
            return profile
    except Exception as e:
        print(f"[industry_detector] Claude detection failed: {e}")

    # Fallback: use quick detection
    return {
        'business_name': meta['title'].split(' — ')[0].split(' | ')[0].split(' - ')[0].strip() or url,
        'industry': quick['primary_industry'],
        'sub_industry': '',
        'is_local_business': quick['is_local_business'],
        'has_physical_location': quick['is_local_business'],
        'services': [],
        'target_audience': '',
        'unique_selling_points': [],
        'locations': [],
        'certifications_or_specialties': quick.get('detected_certifications', []),
        'competitor_examples': [],
        'relevant_faq_topics': [],
        'industry_keywords': [],
        'geo_relevant': quick['is_local_business'],
        'typical_word_count_benchmark': 1500,
        'ai_citation_opportunities': [],
        'quick_signals': quick,
        'fallback': True,
    }
