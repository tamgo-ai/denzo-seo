"""
Local Business Checker — verifies Google Business Profile readiness and NAP consistency.
For businesses detected as 'local' by the industry detector.
"""
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse


def check_local_business(url: str, html: str, domain: str, industry_profile: dict = None) -> dict:
    """
    Check local business signals: NAP consistency, GBP readiness, review signals.
    Only generates findings if the business is detected as local/physical.
    """
    findings = []
    score = 100

    profile = industry_profile or {}
    is_local = profile.get('is_local_business', False)

    if not is_local:
        return {
            "score": 100,
            "findings": [],
            "is_local": False,
            "note": "Not detected as local business — skipping local SEO checks"
        }

    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator=' ')

    # ── 1. NAP Detection (Name, Address, Phone) ──
    # Phone regex: supports US (+1), LATAM (+503, +52, +54, +57, +56, +51, +506, +507),
    # Spain (+34), and generic international formats
    phone_pattern = re.findall(
        r'(?:\+?\d{1,3}[-.\s]?)?\d{3,4}[-.\s]?\d{3,4}[-.\s]?\d{3,4}',
        text
    )
    phones_found = list(set(p.strip() for p in phone_pattern if len(re.sub(r'\D', '', p)) >= 7))

    # Address: supports Spanish and English formats
    # US format: supports abbreviations with periods (Rd., St., Blvd., Ave., Dr., Ct., Ln., Pkwy.)
    # and multi-word city names (Santa Fe Springs, Rancho Cucamonga, San Fernando Valley)
    address_pattern_en = re.findall(
        r'\d{1,5}\s+[\w.]+(?:\s+[\w.]+){1,6}(?:,\s*[\w.\s]+?,\s*[A-Z]{2}\s*\d{5})',
        text
    )
    # Suite/Ste/Unit/Apt pattern: "123 Main St., Suite 100, City, ST 12345"
    address_pattern_suite = re.findall(
        r'\d{1,5}\s+[\w.]+(?:\s+[\w.]+){1,8}(?:,\s*(?:Suite|Ste|Unit|Apt)\.?\s*\d+)?'
        r'(?:,\s*[\w.\s]+?,\s*[A-Z]{2}\s*\d{5})',
        text, re.IGNORECASE
    )
    address_pattern_es = re.findall(
        r'(?:calle|av\.?|avenida|col\.?|colonia|blvd\.?|paseo|calzada|urb\.?|urbanización|'
        r'residencial|edificio|local|n[°º]|#)\s+[\w.]+(?:\s+[\w.]+){1,8}',
        text, re.IGNORECASE
    )
    # Broad fallback: any line with a street number near a ZIP code
    address_pattern_loose = re.findall(
        r'\d{1,6}\s+[\w.\-\']+(?:\s+[\w.\-\']+){2,8}\s*,?\s*\w+(?:\s+\w+){0,2}\s*,?\s*(?:CA|California|TX|Texas|NY|FL|AZ|NV|CO|OR|WA|IL|PA|OH|GA|NC|MI|NJ|VA|MD|MA|TN|IN|MO|WI|MN|SC|AL|LA|KY|OK|CT|IA|MS|AR|KS|UT|NM|WV|NE|ID|ME|NH|HI|RI|MT|DE|SD|AK|ND|VT|WY)\s*\d{4,5}',
        text
    )
    addresses_found = list(set(address_pattern_en + address_pattern_suite + address_pattern_es + address_pattern_loose))

    # Extract business name from title or profile
    business_name = profile.get('business_name', '')
    if not business_name:
        title_tag = soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else ''
        business_name = title.split('|')[0].split('—')[0].strip()

    nap_score = 0
    nap_issues = []

    if not phones_found:
        nap_issues.append("No phone number found in visible text — critical for local SEO")
    else:
        nap_score += 1

    if not addresses_found:
        nap_issues.append("No physical address found in visible text")
    else:
        nap_score += 1

    if business_name:
        nap_score += 1

    if nap_score < 3:
        findings.append({"severity":"critical","module":"local","title":f"NAP signals incomplete: {nap_score}/3 — invisible to local search","detail":"Google needs Name, Address, and Phone in VISIBLE text (not just schema) to confidently rank a business for local queries. Missing: " + '; '.join(nap_issues),"fix":"Add in visible text (footer or contact section):\n1. Business name exactly as registered in Google Business Profile\n2. Full street address with ZIP\n3. Local phone number (not toll-free if possible)\nAll three must match across GBP, website, and all directories exactly."})
        score -= 25
    elif nap_score == 3:
        findings.append({"severity":"pass","module":"local","title":"NAP signals present — good for local SEO","detail":f"Phone: {phones_found[0] if phones_found else 'N/A'}\nAddress: {addresses_found[0] if addresses_found else 'N/A'}\nBusiness: {business_name}","fix":None})

    # ── 2. Review Signals ──
    review_signals = re.findall(r'(?:review|rating|star|testimonial|\d+\.\d/\d)', text, re.IGNORECASE)
    has_review_widget = bool(soup.find_all(['div', 'section'], class_=lambda c: c and any(w in str(c).lower() for w in ['review', 'testimonial', 'rating'])))

    if not review_signals and not has_review_widget:
        findings.append({"severity":"high","module":"local","title":"No review or testimonial signals on page","detail":"Reviews are the #1 local ranking factor after GBP completeness. Pages without review signals are at a severe disadvantage vs competitors who showcase their reviews.","fix":"Add to your page:\n• Google review widget/embed\n• 3-5 customer testimonials with full names and locations\n• Aggregate rating (e.g., '4.9/5 from 247 reviews')\n• Link to your Google Business Profile for new reviews\n• Structured data: Review and AggregateRating schema"})
        score -= 15

    # ── 3. GBP Optimization Checklist (things the business SHOULD have) ──
    gbp_reminders = []

    # Check for location-specific service areas
    locations = profile.get('locations', [])
    service_cities = []
    city_pattern = re.findall(r'\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?),\s*(?:CA|California|TX|Texas|NY|FL|AZ|NV|CO|OR|WA)\b', text)
    service_cities = list(set(city_pattern))[:10]

    if not service_cities and not locations:
        gbp_reminders.append("No service cities detected — list your service area for local SEO")

    if gbp_reminders:
        findings.append({"severity":"medium","module":"local","title":f"GBP readiness: improvements recommended","detail":"\n".join(f"• {r}" for r in gbp_reminders),"fix":"Optimize your Google Business Profile:\n• Select the correct primary category (most important GBP decision)\n• Add all relevant secondary categories\n• Upload 20+ high-quality photos\n• Post weekly updates/offers\n• Answer all Q&A\n• Respond to every review within 24 hours"})
        score -= 8

    # ── 4. Local Schema Needs ──
    # schema.org LocalBusiness subtypes — sites rarely use the base "LocalBusiness"
    # type directly; they use a subtype (AutoRepair, Restaurant, MedicalClinic, …).
    # Match any known subtype so valid local schema isn't reported as missing.
    _local_types = {
        'localbusiness', 'autorepair', 'autobody', 'autobodyrepair', 'automotivebusiness',
        'autodealer', 'autopartsstore', 'autorental', 'autowash', 'gasstation',
        'motorcycledealer', 'motorcyclerepair', 'restaurant', 'foodestablishment',
        'fastfoodrestaurant', 'bakery', 'cafeorcoffeehouse', 'barorpub',
        'dentist', 'medicalclinic', 'medicalbusiness', 'physician', 'veterinarycare',
        'healthandbeautybusiness', 'beautysalon', 'hairsalon', 'dayspa', 'nailsalon',
        'store', 'clothingstore', 'electronicsstore', 'furniturestore', 'grocerystore',
        'hardwarestore', 'jewelrystore', 'petstore', 'sportinggoodsstore', 'tirestore',
        'homeandconstructionbusiness', 'electrician', 'generalcontractor', 'hvacbusiness',
        'housepainter', 'locksmith', 'movingcompany', 'plumber', 'roofingcontractor',
        'professionalservice', 'accountingservice', 'insuranceagency', 'legalservice',
        'notary', 'realestateagent', 'travelagency', 'lodgingbusiness', 'hotel', 'motel',
        'bedandbreakfast', 'gym', 'amusementpark', 'bowlingalley', 'emergencyservice',
        'fireservice', 'hospital', 'policestation', 'childcare', 'preschool', 'library',
        'postoffice', 'selfstorage', 'shoppingcenter', 'sportsactivitylocation',
    }
    schema_scripts = soup.find_all('script', type='application/ld+json')
    has_local_schema = False
    for s in schema_scripts:
        if not s.string:
            continue
        low = s.string.lower()
        types = re.findall(r'"@type"\s*:\s*"([^"]+)"', s.string, re.IGNORECASE)
        if 'localbusiness' in low or any(t.strip().lower() in _local_types for t in types):
            has_local_schema = True
            break

    if not has_local_schema:
        findings.append({"severity":"high","module":"local","title":"No LocalBusiness schema — invisible in Google Maps search","detail":"Without LocalBusiness structured data, Google cannot confirm your business details for Local Pack and Google Maps results.","fix":"Add LocalBusiness JSON-LD schema with:\n{\n  \"@type\": \"LocalBusiness\",\n  \"name\": \"[Business Name]\",\n  \"address\": { \"@type\": \"PostalAddress\", ... },\n  \"telephone\": \"[phone]\",\n  \"geo\": { \"@type\": \"GeoCoordinates\", \"latitude\": ..., \"longitude\": ... },\n  \"openingHoursSpecification\": [...],\n  \"aggregateRating\": { ... }\n}"})
        score -= 18

    return {
        "score": max(0, score),
        "findings": findings,
        "is_local": True,
        "phones_found": phones_found,
        "addresses_found": addresses_found,
        "business_name": business_name,
        "nap_score": nap_score,
        "has_review_signals": bool(review_signals or has_review_widget),
        "service_cities": service_cities,
        "has_local_schema": has_local_schema,
    }
