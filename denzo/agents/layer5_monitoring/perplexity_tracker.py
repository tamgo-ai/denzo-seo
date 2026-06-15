"""
Perplexity GEO Tracker — Layer 6 (Analytics)
=============================================
Tracks whether client pages are cited by Perplexity AI (the only major AI
with a citation API). Runs weekly to monitor Generative Engine visibility.

Perplexity API: https://docs.perplexity.ai
Pricing: $20/mo basic, scales with usage.
Setup: Get API key at https://www.perplexity.ai/settings/api
       Add to .env as PERPLEXITY_API_KEY=pplx-...

Strategy:
  1. Load top GEO query bank for this tenant
  2. Query Perplexity with each (batched, rate-limited)
  3. Check if client domain appears in citations
  4. Track position, frequency, competitor citations
  5. Log results to activity + geo_queries table
"""
import json
import os
import time
import requests
from datetime import datetime, timezone
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write


PERPLEXITY_API = "https://api.perplexity.ai/chat/completions"
MAX_QUERIES_PER_RUN = 20     # keep costs controlled
DELAY_BETWEEN_CALLS = 3      # seconds between Perplexity calls


class PerplexityTracker(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Perplexity Tracker", ctx, layer=6, color="violet")

    def _get_api_key(self) -> str | None:
        key = os.getenv("PERPLEXITY_API_KEY", "")
        if key:
            return key
        rows = db_execute(
            "SELECT value FROM settings WHERE tenant_id='__global__' AND key='perplexity_api_key'"
        )
        return rows[0]["value"] if rows else None

    def _query_perplexity(self, question: str) -> dict | None:
        """Query Perplexity and extract citations. Returns dict with answer + citations."""
        api_key = self._get_api_key()
        if not api_key:
            return None

        try:
            resp = requests.post(
                PERPLEXITY_API,
                json={
                    "model": "sonar-pro",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant. When answering, cite specific sources from the web."},
                        {"role": "user", "content": question}
                    ],
                    "max_tokens": 300,
                    "temperature": 0.0,
                    "return_citations": True,
                    "search_domain_filter": None,  # no filter = organic results
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                citations = data.get("citations", [])
                return {
                    "answer": answer[:500],
                    "citations": citations,
                    "citation_count": len(citations),
                }
            elif resp.status_code == 402:
                self.log("Perplexity API: payment required — check billing", "error")
                return None
            elif resp.status_code == 429:
                self.log("Perplexity API: rate limited — waiting 60s", "warning")
                time.sleep(60)
                return self._query_perplexity(question)
            else:
                self.log(f"Perplexity API: HTTP {resp.status_code}", "warning")
                return None
        except Exception as e:
            self.log(f"Perplexity API error: {str(e)[:80]}", "warning")
            return None

    def _check_citations(self, citations: list[str], domain: str) -> dict:
        """Check if our domain or competitors appear in Perplexity citations."""
        domain_clean = domain.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")

        our_citations = []
        competitor_citations = []

        for url in (citations or []):
            url_clean = url.lower().replace("https://", "").replace("http://", "").replace("www.", "")
            if domain_clean.lower() in url_clean:
                our_citations.append(url)
            else:
                # Check against known competitors
                for comp in (self.ctx.competitors or [])[:10]:
                    comp_domain = (comp.get("url") or comp.get("name", "")).lower()
                    if comp_domain and comp_domain in url_clean:
                        competitor_citations.append({"competitor": comp.get("name", ""), "url": url})

        return {
            "cited": len(our_citations) > 0,
            "our_citations": our_citations,
            "our_position": 1 if our_citations else None,  # simplified: just check if we appear
            "competitor_citations": competitor_citations,
        }

    def run(self):
        self.log("Perplexity GEO Tracker — checking AI visibility...")
        self.set_status("working", "Loading query bank")

        api_key = self._get_api_key()
        if not api_key:
            self.log(
                "PERPLEXITY_API_KEY not configured. Add to .env or Settings → Global. "
                "Skipping Perplexity tracking.",
                "warning"
            )
            self.set_status("idle", "No API key — add PERPLEXITY_API_KEY")
            return

        domain = self.ctx.domain or self.ctx.pages_domain or ""
        if not domain:
            self.log("No domain configured.", "warning")
            self.set_status("idle", "No domain")
            return

        # Load queries from GEO query bank
        queries = db_execute(
            "SELECT id, query, category FROM geo_query_bank "
            "WHERE tenant_id=? AND active=1 ORDER BY id LIMIT ?",
            (self.ctx.tenant_id, MAX_QUERIES_PER_RUN)
        )
        if not queries:
            self.log("No GEO queries in bank. Run GEO Query Generator first.", "warning")
            self.set_status("idle", "No queries")
            return

        self.log(f"Testing {len(queries)} queries against Perplexity for {domain}...")

        cited_count = 0
        total_checked = 0
        competitor_appearances = 0

        for q in queries:
            if self.should_stop():
                break

            question = q["query"]
            self.set_status("working", f"Asking Perplexity: {question[:60]}")

            result = self._query_perplexity(question)
            if not result:
                continue

            total_checked += 1
            check = self._check_citations(result.get("citations", []), domain)

            if check["cited"]:
                cited_count += 1
                self.log(
                    f"✓ CITED: \"{question[:80]}\" — {len(check['our_citations'])} citation(s)",
                    "success"
                )
            else:
                self.log(f"✗ Not cited: \"{question[:80]}\"", "info")

            if check.get("competitor_citations"):
                competitor_appearances += len(check["competitor_citations"])
                comp_names = set(c["competitor"] for c in check["competitor_citations"])
                self.log(f"  ⚔ Competitors cited: {', '.join(list(comp_names)[:3])}", "warning")

            # Save result to geo_queries table
            db_write(
                """INSERT INTO geo_queries
                   (tenant_id, query, ai_model, response, client_mentioned, client_position,
                    competitors_mentioned, checked_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    self.ctx.tenant_id,
                    question,
                    "perplexity",
                    result.get("answer", "")[:500],
                    1 if check["cited"] else 0,
                    check.get("our_position"),
                    json.dumps(check.get("competitor_citations", [])),
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                )
            )

            # Mark query as checked in bank
            db_write(
                "UPDATE geo_query_bank SET active=CASE WHEN ? THEN 0 ELSE 1 END WHERE id=? AND tenant_id=?",
                (not check["cited"], q["id"], self.ctx.tenant_id)
            )

            time.sleep(DELAY_BETWEEN_CALLS)

        cite_rate = round(cited_count / total_checked * 100) if total_checked > 0 else 0
        self.log(
            f"Perplexity GEO complete: {cited_count}/{total_checked} queries cited ({cite_rate}%). "
            f"Competitors appeared in {competitor_appearances} citations.",
            "success" if cite_rate > 30 else "warning"
        )
        self.set_status("done", f"{cite_rate}% citation rate ({cited_count}/{total_checked})")
