"""
Technical Auditor — Layer 1
Scrapes the client's website and audits for SEO technical issues.
"""
import json
import requests
from bs4 import BeautifulSoup
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_write, strip_json_fences


class TechnicalAuditor(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("Technical Auditor", ctx, layer=1, color="gray")

    def _run_ai_audit(self, url: str):
        """Fallback: generate a technical SEO audit via AI when the site blocks crawlers."""
        self.log("Generating AI-based technical SEO audit (site is bot-protected)...", "info")
        self.set_status("working", "Running AI audit (Cloudflare-protected site)")

        prompt = f"""{self.ctx.to_prompt_block()}

The website {url} is protected and cannot be crawled directly.
Based on your knowledge of this business, brand, and typical websites in the {self.ctx.industry_vertical} industry,
generate a realistic technical SEO audit.

Consider what you know about:
- Typical automotive dealership website issues
- Common Cloudflare-protected enterprise dealer sites (often run by CDK Global, DealerSocket, Dealer.com)
- Schema markup requirements for automotive dealerships
- Mobile optimization for dealer sites
- Page speed issues common in dealer CMS platforms

Return ONLY this JSON, no markdown:
{{
  "score": 0-100,
  "critical": ["issue 1", "issue 2"],
  "high_priority": ["fix 1", "fix 2"],
  "quick_wins": ["win 1", "win 2"],
  "strengths": ["strength 1", "strength 2"],
  "summary": "One paragraph executive summary of likely technical SEO state",
  "note": "AI-generated audit — site blocked crawlers, analysis based on industry knowledge"
}}"""
        raw = self.call_claude(prompt, max_tokens=1000)
        if not raw:
            self.log("AI audit generation failed.", "error")
            self.set_status("error", "Could not fetch site or generate AI audit")
            return

        try:
            result = json.loads(strip_json_fences(raw))
        except Exception:
            result = {"summary": raw[:500], "score": 40}

        score = result.get("score", "?")
        self.log(f"AI Audit score (estimated): {score}/100", "info")
        for issue in result.get("critical", []):
            self.log(f"CRITICAL: {issue}", "error")
        for fix in result.get("high_priority", []):
            self.log(f"HIGH: {fix}", "warning")
        for win in result.get("quick_wins", []):
            self.log(f"QUICK WIN: {win}", "success")
        if result.get("summary"):
            self.log(result["summary"], "info")

        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.ctx.tenant_id, "technical_audit", json.dumps(result))
        )
        self.log("AI audit saved — available to Strategy layer agents", "success")
        self.log(f"Technical audit complete. Score: {score}/100", "success")
        self.set_status("done", f"Technical audit complete — score {score}/100")

    def run(self):
        self.log("Starting technical SEO audit...")
        self.set_status("working", "Fetching website")

        url = self.ctx.website_url or self.ctx.domain
        if not url:
            self.log("No website URL configured. Add it in Settings.", "warning")
            self.set_status("idle", "No website URL")
            return

        if not url.startswith("http"):
            url = "https://" + url

        # 3-pass Cloudflare bypass: requests → cloudscraper → Playwright stealth
        from denzo.agents.utils.stealth_fetch import fetch_html
        result = fetch_html(url, timeout=25, log_fn=lambda m: self.log(m, "info"))

        if not result["ok"]:
            self.log(f"All fetch methods failed — running AI-based audit", "warning")
            self._run_ai_audit(url)
            return

        content = result["html"]
        status_code = result["status"]
        final_url = url
        self.log(f"Fetched via {result['method']} — status {status_code}", "info")

        soup = BeautifulSoup(content, "html.parser")

        # Collect technical signals
        signals = {}
        signals["status_code"]   = status_code
        signals["final_url"]     = final_url
        signals["https"]         = final_url.startswith("https://")
        signals["title"]         = soup.title.string.strip() if soup.title and soup.title.string else ""
        signals["title_length"]  = len(signals["title"])
        meta_desc = soup.find("meta", {"name": "description"})
        signals["meta_description"] = meta_desc.get("content", "").strip() if meta_desc else ""
        signals["meta_desc_length"] = len(signals["meta_description"])
        signals["h1_count"]      = len(soup.find_all("h1"))
        signals["h2_count"]      = len(soup.find_all("h2"))
        signals["h1_texts"]      = [h.get_text(" ", strip=True) for h in soup.find_all("h1")][:3]
        canonical = soup.find("link", {"rel": "canonical"})
        signals["canonical"]     = canonical.get("href", "") if canonical else ""
        robots = soup.find("meta", {"name": "robots"})
        signals["robots"]        = robots.get("content", "") if robots else ""
        signals["img_count"]     = len(soup.find_all("img"))
        signals["imgs_no_alt"]   = len([i for i in soup.find_all("img") if not i.get("alt")])
        signals["schema_types"]  = list({s.get("type","") for s in soup.find_all("script", {"type":"application/ld+json"})})
        has_schema = bool(soup.find("script", {"type": "application/ld+json"}))
        signals["has_schema"]    = has_schema
        viewport = soup.find("meta", {"name": "viewport"})
        signals["mobile_ready"]  = bool(viewport)
        word_count = len(soup.get_text().split())
        signals["word_count"]    = word_count
        signals["internal_links"]= len([a for a in soup.find_all("a", href=True) if a["href"].startswith("/") or url in a["href"]])

        self.set_status("working", "Analyzing findings with AI")
        self.log(f"Fetched {url} — {status_code} — {word_count} words")

        # ── Optional: enrich with Apify real technical audit ─────────────────
        from denzo.agents.utils.apify_service import ApifyService
        apify = ApifyService(log_fn=lambda m, l="info": self.log(m, l))
        apify_audit = None
        if apify.available():
            self.log("[APIFY REAL] Running technical SEO audit via Apify...")
            self.set_status("working", "Running real technical audit (Apify)")
            apify_audit = apify.audit_url(url)
            if apify_audit:
                self.log(
                    f"[APIFY REAL] Technical audit score: {apify_audit.get('score', '?')} "
                    f"(grade: {apify_audit.get('grade', '?')})",
                    "success"
                )
                signals["apify_score"] = apify_audit.get("score")
                signals["apify_grade"] = apify_audit.get("grade")
                signals["apify_issues"] = apify_audit.get("issues", [])

        prompt = f"""{self.ctx.to_prompt_block()}

Technical SEO audit findings for {url}:
{json.dumps(signals, indent=2)}

As an expert Technical SEO auditor, analyze these signals and provide:
1. Critical issues (blocking indexation or rankings)
2. High-priority fixes (significant ranking impact)
3. Quick wins (easy to fix, good ROI)
4. What's working well

Return a JSON object:
{{
  "score": 0-100,
  "critical": ["issue 1", "issue 2"],
  "high_priority": ["fix 1", "fix 2"],
  "quick_wins": ["win 1", "win 2"],
  "strengths": ["strength 1"],
  "summary": "One paragraph executive summary"
}}

Return ONLY valid JSON.
"""
        raw = self.call_claude(prompt, max_tokens=1500)
        if not raw:
            self.log("AI analysis failed.", "error")
            return

        try:
            raw = strip_json_fences(raw)
            result = json.loads(raw)
        except Exception:
            result = {"summary": raw[:500]}

        # Merge Apify real scores into result if available
        if apify_audit:
            result["apify_score"] = apify_audit.get("score")
            result["apify_grade"] = apify_audit.get("grade")
            result["apify_issues"] = apify_audit.get("issues", [])
            # Surface any critical Apify-detected issues
            for issue in result["apify_issues"][:3]:
                if isinstance(issue, dict):
                    self.log(f"[APIFY] {issue.get('title','issue')}: {issue.get('description','')}", "warning")

        score = result.get("score", "?")
        self.log(f"Audit score: {score}/100", "info")

        for issue in result.get("critical", []):
            self.log(f"CRITICAL: {issue}", "error")
        for fix in result.get("high_priority", []):
            self.log(f"HIGH: {fix}", "warning")
        for win in result.get("quick_wins", []):
            self.log(f"QUICK WIN: {win}", "success")

        summary = result.get("summary", "")
        if summary:
            self.log(summary, "info")

        # Persist audit result to activity log
        db_write(
            "INSERT INTO activity (tenant_id, type, message, agent, level) VALUES (?,?,?,?,?)",
            (self.ctx.tenant_id, "audit", json.dumps(result), self.name, "info")
        )

        # Save to settings table so downstream agents (E-E-A-T, Schema) can read it
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.ctx.tenant_id, "technical_audit", json.dumps(result))
        )
        self.log("Audit results saved → available to Strategy layer agents", "info")

        self.log(f"Technical audit complete. Score: {score}/100", "success")
        self.set_status("done", f"Audit complete — score {score}/100")
