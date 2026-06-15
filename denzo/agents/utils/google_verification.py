"""
Google Domain Verification — automated via DNS TXT + Site Verification API.

Architecture:
  ONE Google Cloud project (tamgo-crm-497016)
  ONE service account (denzo-seo-indexer@tamgo-crm-497016.iam.gserviceaccount.com)
  ALL clients verified via DNS TXT record

Flow for each new client:
  1. Onboarding wizard calls get_verification_token(domain) → returns DNS TXT value
  2. Client/admin adds TXT record to their DNS (or Raúl does it for them)
  3. System calls verify_domain(domain) → service account becomes owner
  4. Google Indexing API now works for that domain

This file also provides the status check used by the Indexation Accelerator
to decide whether to submit URLs to Google Indexing API (vs just IndexNow).
"""
import json
import os
import requests
from typing import Optional

from denzo.agents.base_agent import db_execute, db_write


SERVICE_ACCOUNT_PATH = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "/root/denzo-seo/data/google-service-account.json"
)

VERIFIED_DOMAINS_CACHE_KEY = "google_verified_domains"


def _get_credentials(scopes: list[str] = None):
    """Load service account credentials. Returns None if not configured."""
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        return None
    try:
        from google.oauth2.service_account import Credentials
        import google.auth.transport.requests

        with open(SERVICE_ACCOUNT_PATH) as f:
            sa_info = json.load(f)

        if scopes is None:
            scopes = ["https://www.googleapis.com/auth/siteverification"]

        creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
        creds.refresh(google.auth.transport.requests.Request())
        return creds
    except ImportError:
        return None
    except Exception:
        return None


def is_configured() -> bool:
    """True if the Google service account JSON file exists and is valid."""
    return _get_credentials() is not None


def get_service_account_email() -> Optional[str]:
    """Return the service account email for this organization."""
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        return None
    try:
        with open(SERVICE_ACCOUNT_PATH) as f:
            return json.load(f).get("client_email")
    except Exception:
        return None


def get_verification_token(domain: str) -> Optional[str]:
    """
    Get a DNS TXT verification token for a domain.
    Call this during onboarding. Returns the TXT record value to add to DNS.
    Returns None if service account not configured or API fails.
    """
    creds = _get_credentials()
    if not creds:
        return None

    # Normalize domain: strip protocol and path
    domain = domain.replace("https://", "").replace("http://", "").split("/")[0].strip()

    try:
        resp = requests.post(
            "https://www.googleapis.com/siteVerification/v1/token",
            json={
                "verificationMethod": "DNS_TXT",
                "site": {"identifier": domain, "type": "INET_DOMAIN"}
            },
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("token")
        return None
    except Exception:
        return None


def verify_domain(domain: str) -> bool:
    """
    Attempt to verify domain ownership via DNS TXT.
    Returns True if verification succeeded.
    Call this after the DNS record has been added.
    """
    creds = _get_credentials()
    if not creds:
        return False

    domain = domain.replace("https://", "").replace("http://", "").split("/")[0].strip()

    try:
        resp = requests.post(
            "https://www.googleapis.com/siteVerification/v1/webResource",
            params={"verificationMethod": "DNS_TXT"},
            json={"site": {"identifier": domain, "type": "INET_DOMAIN"}},
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            # Cache the verified domain
            _mark_verified(domain)
            return True
        return False
    except Exception:
        return False


def is_domain_verified(domain: str) -> bool:
    """
    Check if a domain is verified for the service account.
    Used by Indexation Accelerator to decide Google Indexing API vs IndexNow only.
    """
    domain = domain.replace("https://", "").replace("http://", "").split("/")[0].strip()

    # Check cache first
    cached = _get_verified_domains()
    if domain in cached:
        return True

    # Check via API
    creds = _get_credentials(
        scopes=["https://www.googleapis.com/auth/siteverification"]
    )
    if not creds:
        return False

    try:
        import urllib.parse
        encoded = urllib.parse.quote(domain, safe="")
        resp = requests.get(
            f"https://www.googleapis.com/siteVerification/v1/webResource/{encoded}",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            _mark_verified(domain)
            return True
        return False
    except Exception:
        return cached is not None and domain in cached


def list_verified_domains() -> list[str]:
    """List all domains verified for this service account."""
    creds = _get_credentials()
    if not creds:
        return []

    try:
        resp = requests.get(
            "https://www.googleapis.com/siteVerification/v1/webResource",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            domains = []
            for item in items:
                site = item.get("site", {})
                identifier = site.get("identifier", "")
                if identifier:
                    domains.append(identifier.replace("sc_domain:", ""))
            return domains
        return []
    except Exception:
        return []


def _mark_verified(domain: str) -> None:
    """Cache a verified domain in the settings table."""
    domains = _get_verified_domains()
    if domain not in domains:
        domains.append(domain)
    db_write(
        "INSERT OR REPLACE INTO settings (tenant_id, key, value) VALUES (?,?,?)",
        ("__global__", VERIFIED_DOMAINS_CACHE_KEY, json.dumps(domains))
    )


def _get_verified_domains() -> list[str]:
    """Get cached list of verified domains."""
    rows = db_execute(
        "SELECT value FROM settings WHERE tenant_id='__global__' AND key=?",
        (VERIFIED_DOMAINS_CACHE_KEY,)
    )
    if rows:
        try:
            return json.loads(rows[0]["value"])
        except Exception:
            pass
    return []
