"""
GEOBaselineAgent — Discovery layer. Queries AI engines BEFORE DENZO generates
content, establishing a baseline of current LLM citations.

Part of Capa 0.5 (Discovery & Reconciliation).
Reuses the same engine query functions as GEOMonitor.
"""

import json
import time
from datetime import datetime, timezone

from denzo.agents.base_agent import TenantAwareBaseAgent


class GEOBaselineAgent(TenantAwareBaseAgent):
    """Establish pre-DENZO GEO citation baseline across AI engines."""

    PREREQUISITES = []
    MIN_KEYWORDS = 0

    BASELINE_QUERIES = 15  # Fewer than full monitor (25) — just the baseline

    def __init__(self, ctx):
        super().__init__(name="GEO Baseline", ctx=ctx, layer=1, color="sky")

    def run(self):
        self.log("GEOBaselineAgent: establishing pre-DENZO citation baseline...")
        self.set_status("working", "Querying AI engines for current citations")

        # ── Generate seed queries ──────────────────────────────────────────
        queries = self._generate_seed_queries()
        if not queries:
            self.set_status("done", "No queries to check — skipping baseline")
            return

        self.log(f"Checking {len(queries)} queries across AI engines")

        results = []
        engine_available = {
            "perplexity": bool(self._get_api_key("PERPLEXITY_API_KEY")),
            "gemini": bool(self._get_api_key("GEMINI_API_KEY")),
            "chatgpt": bool(self._get_api_key("OPENAI_API_KEY")),
        }

        for query in queries:
            if self.should_stop():
                break

            engine_results = {}

            # Perplexity (primary — has real citation API)
            if engine_available["perplexity"]:
                try:
                    engine_results["perplexity"] = self._query_perplexity(query)
                except Exception as e:
                    self.log(f"Perplexity baseline error: {e}", "warning")

            # Gemini
            if engine_available["gemini"]:
                try:
                    engine_results["gemini"] = self._query_gemini(query)
                    time.sleep(1)
                except Exception as e:
                    self.log(f"Gemini baseline error: {e}", "warning")

            # ChatGPT
            if engine_available["chatgpt"]:
                try:
                    engine_results["chatgpt"] = self._query_chatgpt(query)
                    time.sleep(2)
                except Exception as e:
                    self.log(f"ChatGPT baseline error: {e}", "warning")

            if engine_results:
                results.append({
                    "query": query,
                    "engines": engine_results,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                })

            time.sleep(1)

        # Persist results to geo_queries with baseline flag
        self._save_baseline_results(results)

        # Save summary to settings for Reconciliation
        cited_count = sum(
            1 for r in results
            for e in r["engines"].values()
            if e.get("cited")
        )
        total_checks = sum(len(r["engines"]) for r in results)

        self.save_output("geo_baseline", {
            "queries_checked": len(results),
            "engines_available": engine_available,
            "citations_found": cited_count,
            "total_checks": total_checks,
            "citation_rate": round(cited_count / max(total_checks, 1) * 100, 1),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        self.set_status("done",
            f"GEO baseline: {cited_count}/{total_checks} citations ({len(results)} queries)")

    # ── Query helpers (reuse GEOMonitor functions where possible) ──────────

    def _generate_seed_queries(self) -> list[str]:
        """Generate seed queries from tenant context for baseline check."""
        queries = []
        name = self.ctx.client_name or ""
        city = self.ctx.primary_city or ""
        services = self.ctx.services[:3] if self.ctx.services else []

        if name:
            queries.append(f"{name} {city}")
            queries.append(f"best {name}")
            queries.append(f"{name} reviews")

        for svc in services:
            if name:
                queries.append(f"{svc} at {name} {city}")
            queries.append(f"best {svc} in {city}")

        # Generic local service queries
        industry = self.ctx.industry_vertical or "general"
        if city and services:
            queries.append(f"best {services[0]} near me in {city}")
            queries.append(f"affordable {services[0]} {city}")

        return queries[:self.BASELINE_QUERIES]

    def _get_api_key(self, env_var: str) -> str:
        import os
        return os.getenv(env_var, "").strip()

    def _query_perplexity(self, query: str) -> dict:
        import requests
        key = self._get_api_key("PERPLEXITY_API_KEY")
        if not key:
            return {"cited": False, "error": "no_api_key"}

        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "sonar", "messages": [{"role": "user", "content": query}], "max_tokens": 300},
            timeout=25,
        )
        if r.status_code == 200:
            data = r.json()
            text = data["choices"][0]["message"]["content"] if data.get("choices") else ""
            cited = self._check_citation(text)
            return {"cited": cited, "text_snippet": text[:200]}
        return {"cited": False, "status": r.status_code}

    def _query_gemini(self, query: str) -> dict:
        import requests
        key = self._get_api_key("GEMINI_API_KEY")
        if not key:
            return {"cited": False, "error": "no_api_key"}

        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
            json={"contents": [{"parts": [{"text": query}]}],
                  "generationConfig": {"maxOutputTokens": 300, "temperature": 0.3}},
            timeout=20,
        )
        if r.status_code == 200:
            data = r.json()
            text = ""
            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                pass
            cited = self._check_citation(text)
            return {"cited": cited, "text_snippet": text[:200]}
        return {"cited": False, "status": r.status_code}

    def _query_chatgpt(self, query: str) -> dict:
        import requests
        key = self._get_api_key("OPENAI_API_KEY")
        if not key:
            return {"cited": False, "error": "no_api_key"}

        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini", "messages": [
                {"role": "system", "content": "You are a helpful local search assistant. Answer concisely."},
                {"role": "user", "content": query}
            ], "max_tokens": 300, "temperature": 0.3},
            timeout=20,
        )
        if r.status_code == 200:
            data = r.json()
            text = data["choices"][0]["message"]["content"] if data.get("choices") else ""
            cited = self._check_citation(text)
            return {"cited": cited, "text_snippet": text[:200]}
        return {"cited": False, "status": r.status_code}

    def _check_citation(self, text: str) -> bool:
        """Check if the business name or domain appears in the response."""
        if not text:
            return False
        text_lower = text.lower()
        name = (self.ctx.client_name or "").lower()
        domain = (self.ctx.domain or "").lower().replace("https://", "").replace("http://", "").rstrip("/")
        if name and name in text_lower:
            return True
        if domain and domain in text_lower:
            return True
        return False

    def _save_baseline_results(self, results: list):
        """Save baseline results to geo_queries table."""
        from denzo.agents.base_agent import db_write

        for r in results:
            for engine, data in r.get("engines", {}).items():
                try:
                    db_write(
                        """INSERT INTO geo_queries
                           (tenant_id, query, engine, cited, baseline, checked_at)
                           VALUES (?, ?, ?, ?, 1, ?)""",
                        (self.tenant_id, r["query"], engine,
                         1 if data.get("cited") else 0,
                         r.get("checked_at", datetime.now(timezone.utc).isoformat()))
                    )
                except Exception:
                    pass  # duplicate or schema miss — non-critical
