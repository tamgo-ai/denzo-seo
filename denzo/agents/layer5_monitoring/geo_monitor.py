"""
GEO Monitor — Layer 6
Tracks whether AI systems cite the business when users ask relevant questions.

Supported engines:
  perplexity  — Perplexity Sonar (best AI search engine)       key: perplexity_api_key
  chatgpt     — OpenAI GPT-4o-mini                             key: openai_api_key
  gemini      — Google Gemini 1.5 Flash                        key: gemini_api_key
  claude      — Claude (cold, no business context — honest)    key: anthropic_api_key (auto)
  bing        — Bing Search → Copilot-style answer             key: bing_api_key
"""
import json
import os
import re
import requests
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


def _get_setting(tenant_id, key):
    for tid in (tenant_id, "__global__"):
        rows = db_execute("SELECT value FROM settings WHERE tenant_id=? AND key=?", (tid, key))
        if rows:
            return rows[0]["value"]
    return ""


def _query_perplexity(api_key, query):
    try:
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "sonar", "messages": [{"role": "user", "content": query}], "max_tokens": 500},
            timeout=25,
        )
        r.raise_for_status()
        return {"success": True, "response": r.json()["choices"][0]["message"]["content"]}
    except Exception as e:
        return {"success": False, "error": str(e), "response": ""}


def _query_openai(api_key, query):
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini",
                  "messages": [{"role": "system", "content": "You are a helpful local search assistant. Answer concisely based on your knowledge."},
                                {"role": "user", "content": query}],
                  "max_tokens": 400, "temperature": 0.3},
            timeout=20,
        )
        r.raise_for_status()
        return {"success": True, "response": r.json()["choices"][0]["message"]["content"]}
    except Exception as e:
        return {"success": False, "error": str(e), "response": ""}


def _query_gemini(api_key, query):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        r = requests.post(url,
            json={"contents": [{"parts": [{"text": query}]}],
                  "generationConfig": {"maxOutputTokens": 400, "temperature": 0.3}},
            timeout=20)
        r.raise_for_status()
        return {"success": True, "response": r.json()["candidates"][0]["content"]["parts"][0]["text"]}
    except Exception as e:
        return {"success": False, "error": str(e), "response": ""}


def _query_claude_cold(query):
    """
    Query Claude with NO business context — honest cold test of whether
    Claude's training data includes this business. Uses env API key directly.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"success": False, "error": "No ANTHROPIC_API_KEY in env", "response": ""}
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "system": "You are a helpful local search assistant. Answer based only on your training knowledge. Be concise.",
                "messages": [{"role": "user", "content": query}],
            },
            timeout=20,
        )
        r.raise_for_status()
        text = r.json()["content"][0]["text"]
        return {"success": True, "response": text}
    except Exception as e:
        return {"success": False, "error": str(e), "response": ""}


def _query_bing(api_key, query):
    """
    Bing Search API — returns top web snippets (what Copilot uses as context).
    We treat it as: does Bing surface this business in its top results?
    """
    try:
        r = requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": api_key},
            params={"q": query, "count": 5, "mkt": "en-US", "responseFilter": "Webpages"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        pages = data.get("webPages", {}).get("value", [])
        # Combine snippets into a single response text
        snippets = " | ".join(
            f"{p.get('name','')} — {p.get('snippet','')}" for p in pages
        )
        return {"success": True, "response": snippets or "No results"}
    except Exception as e:
        return {"success": False, "error": str(e), "response": ""}


def _analyze_citation(response_text, business_name, website, competitors):
    text_lower = response_text.lower()
    name_lower = business_name.lower()
    words      = name_lower.split()
    domain     = (website or "").replace("https://", "").replace("www.", "").split("/")[0]

    if name_lower in text_lower or (domain and domain in text_lower):
        cited = "CITED"
    elif len(words) >= 2 and words[0] in text_lower and words[-1] in text_lower:
        cited = "PARTIAL"
    else:
        cited = "NOT_CITED"

    position = None
    if cited in ("CITED", "PARTIAL"):
        for i, sentence in enumerate(re.split(r"[.!?\n]", response_text), 1):
            if name_lower in sentence.lower():
                position = i
                break

    comp_found = [c["name"] for c in competitors if c.get("name", "").lower() in text_lower]
    return {"cited": cited, "position": position, "competitors_found": comp_found}


class GEOMonitor(TenantAwareBaseAgent):

    def __init__(self, ctx: ClientContext):
        super().__init__("GEO Monitor", ctx, layer=6, color="cyan")

    def _load_query_bank(self):
        rows = db_execute(
            "SELECT id, query, category FROM geo_query_bank WHERE tenant_id=? AND active=1 ORDER BY id",
            (self.ctx.tenant_id,)
        )
        return [dict(r) for r in rows] if rows else []

    def _seed_query_bank(self):
        ctx    = self.ctx
        cities = ctx.service_cities[:5] if ctx.service_cities else [ctx.primary_city or ""]
        prompt = f"""{ctx.to_prompt_block()}

Generate 25 search queries that real customers would type into ChatGPT, Perplexity, or Google AI to find this business.

Cover:
- branded (2-3): include "{ctx.client_name}" directly
- service (6-8): specific services + cities
- location (4-5): "best [service] in [city]" for: {', '.join(cities[:4])}
- problem (4-5): customer pain points after an accident or needing this service
- comparison (3-4): vs competitors or vs chain shops
- certification (2-3): brand-specific certifications

Return JSON array: [{{"query": "best BMW certified body shop Los Angeles", "category": "service"}}]
Return ONLY valid JSON. Make queries sound natural.
"""
        raw = self.call_claude(prompt, max_tokens=1500, model="claude-sonnet-4-6")
        if not raw:
            return 0
        try:
            queries = json.loads(strip_json_fences(raw, "["))
        except Exception:
            return 0
        saved = 0
        for q in queries:
            text = q.get("query", "").strip()
            if not text:
                continue
            try:
                db_write(
                    "INSERT OR IGNORE INTO geo_query_bank (tenant_id, query, category) VALUES (?,?,?)",
                    (ctx.tenant_id, text, q.get("category", "general"))
                )
                saved += 1
            except Exception:
                pass
        self.log(f"Query bank seeded: {saved} queries.", "success")
        return saved

    def run(self):
        self.log("GEO Monitor starting...")
        self.set_status("working", "Loading API keys")
        ctx = self.ctx

        # Prereq check: need at least one published page to monitor
        pub_check = db_execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant_id=? AND status='published'",
            (ctx.tenant_id,)
        )
        pub_count = pub_check[0]["n"] if pub_check else 0
        if pub_count == 0:
            self.log("No published pages found. Run a Publisher agent first.", "warning")
            self.set_status("idle", "No published pages — run a Publisher first")
            return

        perplexity_key = _get_setting(ctx.tenant_id, "perplexity_api_key")
        openai_key     = _get_setting(ctx.tenant_id, "openai_api_key")
        gemini_key     = _get_setting(ctx.tenant_id, "gemini_api_key")
        bing_key       = _get_setting(ctx.tenant_id, "bing_api_key")
        claude_cold    = bool(os.getenv("ANTHROPIC_API_KEY"))  # always available if env key set

        engines = []
        if perplexity_key:
            engines.append(("perplexity", perplexity_key))
            self.log("Perplexity Sonar — enabled", "info")
        if openai_key:
            engines.append(("chatgpt", openai_key))
            self.log("ChatGPT (OpenAI) — enabled", "info")
        if gemini_key:
            engines.append(("gemini", gemini_key))
            self.log("Google Gemini — enabled", "info")
        if bing_key:
            engines.append(("bing", bing_key))
            self.log("Bing Search (Copilot) — enabled", "info")
        if claude_cold:
            engines.append(("claude", None))
            self.log("Claude (cold, no context) — enabled", "info")
        # Load or seed query bank regardless of keys
        query_bank = self._load_query_bank()
        if not query_bank:
            self.set_status("working", "Generating query bank with AI...")
            self._seed_query_bank()
            query_bank = self._load_query_bank()

        if not query_bank:
            self.log("No queries available.", "error")
            self.set_status("error", "Empty query bank")
            return

        if not engines:
            self.log(
                f"Query bank ready with {len(query_bank)} queries. "
                "Add Perplexity/OpenAI/Gemini API keys in Settings to start real monitoring.",
                "warning"
            )
            self.set_status("idle", f"{len(query_bank)} queries ready — add API keys to run")
            return

        self.log(f"Monitoring {len(query_bank)} queries × {len(engines)} engine(s)...")

        comp_rows   = db_execute("SELECT name FROM competitors WHERE tenant_id=? LIMIT 20", (ctx.tenant_id,))
        competitors = [{"name": r["name"]} for r in comp_rows] if comp_rows else []

        total_checks = 0
        total_cited  = 0

        for q in query_bank:
            if self.should_stop():
                break
            query = q["query"]
            self.set_status("working", f"Checking: {query[:55]}")

            # Real AI engines
            for engine_name, api_key in engines:
                if self.should_stop():
                    break
                self.set_status("working", f"[{engine_name}] {query[:45]}")
                if engine_name == "perplexity":
                    result = _query_perplexity(api_key, query)
                elif engine_name == "chatgpt":
                    result = _query_openai(api_key, query)
                elif engine_name == "gemini":
                    result = _query_gemini(api_key, query)
                elif engine_name == "claude":
                    result = _query_claude_cold(query)
                elif engine_name == "bing":
                    result = _query_bing(api_key, query)
                else:
                    continue

                if not result["success"]:
                    self.log(f"[{engine_name}] Error: {result.get('error','?')[:60]}", "warning")
                    continue

                analysis   = _analyze_citation(result["response"], ctx.client_name, ctx.website_url, competitors)
                cited_flag = 1 if analysis["cited"] == "CITED" else 0
                if cited_flag:
                    total_cited += 1
                total_checks += 1
                db_write(
                    "INSERT INTO geo_queries "
                    "(tenant_id, query, ai_model, response, client_mentioned, client_position, competitors_mentioned) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (ctx.tenant_id, query, engine_name, result["response"][:1000], cited_flag,
                     analysis["position"],
                     json.dumps(analysis["competitors_found"]) if analysis["competitors_found"] else None)
                )
                icon  = "✓" if analysis["cited"] == "CITED" else ("~" if analysis["cited"] == "PARTIAL" else "✗")
                level = "success" if analysis["cited"] == "CITED" else ("warning" if analysis["cited"] == "PARTIAL" else "error")
                note  = f" | competitors: {', '.join(analysis['competitors_found'])}" if analysis["competitors_found"] else ""
                self.log(f"[{engine_name}] {icon} {analysis['cited']}: {query[:50]}{note}", level)

        rate        = round((total_cited / total_checks) * 100) if total_checks else 0
        engine_list = ", ".join(e[0] for e in engines)
        self.log(f"Done. Citation rate: {rate}% ({total_cited}/{total_checks}) — {engine_list}", "success")
        self.set_status("done", f"Citation rate: {rate}% · {total_checks} checks · {engine_list}")
