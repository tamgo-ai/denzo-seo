"""
Keyword Clusterer — Layer 1
Groups keywords into semantic clusters to eliminate cannibalization.
Critical for preventing multiple pages from competing for the same search intent.
"""
import json
from denzo.agents.base_agent import TenantAwareBaseAgent, ClientContext, db_execute, db_write, strip_json_fences


class KeywordClusterer(TenantAwareBaseAgent):

    MIN_KEYWORDS = 10

    def __init__(self, ctx: ClientContext):
        super().__init__("Keyword Clusterer", ctx, layer=1, color="cyan")

    def run(self):
        self.log("Starting keyword clustering analysis...")
        self.set_status("working", "Loading keywords from database")
        ctx = self.ctx

        # Load keywords — limit to 300 to guarantee JSON fits in output tokens
        rows = db_execute(
            "SELECT keyword, intent, category, priority FROM keywords WHERE tenant_id=? ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END LIMIT 300",
            (self.ctx.tenant_id,)
        )

        if not rows or len(rows) < 10:
            count = len(rows) if rows else 0
            self.log(
                f"Not enough keywords to cluster — found {count}, need at least 10. "
                "Run Keyword Strategist first.",
                "warning"
            )
            self.set_status("idle", f"Need at least 10 keywords — found {count}")
            return

        self.log(f"Loaded {len(rows)} keywords — building semantic clusters...")
        self.set_status("working", f"Clustering {len(rows)} keywords with AI")

        # Format keyword list for the prompt
        kw_list = [
            {
                "keyword": row["keyword"],
                "intent": row["intent"] or "",
                "category": row["category"] or "",
                "priority": row["priority"] or "medium",
            }
            for row in rows
        ]

        prompt = f"""{ctx.to_prompt_block()}

You are a Senior SEO Strategist specializing in keyword cannibalization prevention and semantic clustering.

Below is a list of {len(kw_list)} keywords for this business. Your job is to group them into semantic clusters where each cluster represents:
- The SAME primary search intent
- The SAME core topic / service / page

RULES FOR CLUSTERING:
1. One page should target ONE cluster — never let two clusters map to the same URL
2. The "winner_keyword" is the highest-value keyword in the cluster (most commercial, most specific, best estimated volume)
3. "member_keywords" are supporting keywords that should appear naturally on the same page as the winner
4. Identify cannibalization risks — keywords that are so similar that Google might rank the wrong page
5. Be conservative: if in doubt, merge into fewer clusters rather than splitting too granularly

KEYWORDS TO CLUSTER:
{json.dumps(kw_list, ensure_ascii=False, indent=2)}

Return a JSON object with this exact structure:
{{
  "clusters": [
    {{
      "cluster_name": "Descriptive name for the cluster topic",
      "winner_keyword": "the most valuable keyword for this cluster",
      "winner_slug": "url-slug-for-this-page",
      "member_keywords": ["supporting keyword 1", "supporting keyword 2"],
      "intent": "transactional|commercial|informational|navigational",
      "page_type": "service|location|faq|blog|about"
    }}
  ],
  "total_clusters": 0,
  "cannibalization_risks": [
    "keyword A vs keyword B — both target the same informational intent about X"
  ]
}}

Return ONLY the JSON object, no explanation or markdown.
"""

        raw = self.call_claude(prompt, max_tokens=12000, model="claude-sonnet-4-6")

        if not raw:
            self.log("AI returned empty response", "error")
            self.set_status("error", "Empty API response")
            return

        cleaned = strip_json_fences(raw, start_char="{")
        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as e:
            # ── Repair: try to salvage partial JSON ──────────────────
            self.log(f"JSON parse error, attempting repair...", "warning")
            repaired = self._repair_json(cleaned)
            if repaired:
                try:
                    result = json.loads(repaired)
                    self.log("JSON repaired successfully", "info")
                except json.JSONDecodeError as e2:
                    self.log(f"JSON repair also failed: {e2}", "error")
                    self.set_status("error", f"Parse error (unrepairable): {e2}")
                    return
            else:
                self.log(f"JSON parse failed — preview: {cleaned[:200]}", "error")
                self.set_status("error", f"Parse error: {e}")
                return

        clusters = result.get("clusters", [])
        cannibalization_risks = result.get("cannibalization_risks", [])
        n_clusters = len(clusters)
        n_risks = len(cannibalization_risks)

        if not clusters:
            self.log("AI returned 0 clusters — unexpected response", "error")
            self.set_status("error", "No clusters returned by AI")
            return

        # Save full clustering result to settings
        db_write(
            "INSERT OR REPLACE INTO settings (tenant_id, key, value, updated_at) "
            "VALUES (?,?,?,CURRENT_TIMESTAMP)",
            (self.ctx.tenant_id, "keyword_clusters", json.dumps(result))
        )
        self.log(f"Saved {n_clusters} clusters to settings.", "success")

        # For each cluster, tag member keywords in the DB with their cluster name
        tagged = 0
        for cluster in clusters:
            if self.should_stop():
                break
            cluster_name = cluster.get("cluster_name", "")
            member_keywords = cluster.get("member_keywords", [])
            if not cluster_name or not member_keywords:
                continue
            note_value = f"cluster:{cluster_name}"
            for member_kw in member_keywords:
                if not member_kw:
                    continue
                db_write(
                    "UPDATE keywords SET notes=? WHERE tenant_id=? AND keyword=?",
                    (note_value, self.ctx.tenant_id, member_kw)
                )
                tagged += 1

        self.log(
            f"Clustering complete: {n_clusters} clusters found, "
            f"{tagged} member keywords tagged, "
            f"{n_risks} cannibalization risks resolved.",
            "success"
        )

        # ── Populate topic_map — one row per canonical intent ───────────────
        self._populate_topic_map(clusters)

        if n_risks > 0:
            for risk in cannibalization_risks[:5]:
                self.log(f"Cannibalization risk: {risk}", "warning")

        self.set_status(
            "done",
            f"{n_clusters} keyword clusters — {n_risks} cannibalization risks resolved, {len(clusters)} intents in topic_map"
        )

    def _populate_topic_map(self, clusters: list):
        """Insert each cluster into topic_map as a canonical intent.

        Checks existing_keyword_map first: if a keyword is already owned
        by client content, marks it as 'owned_existing' (no new page needed).
        """
        # Load existing keyword map (from KeywordFootprintAgent)
        existing_map = {}
        rows = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='existing_keyword_map'",
            (self.ctx.tenant_id,)
        )
        if rows and rows[0]["value"]:
            try:
                existing_map = json.loads(rows[0]["value"]).get("keywords", {})
            except Exception:
                pass

        inserted = 0
        skipped = 0
        for cluster in clusters:
            winner = (cluster.get("winner_keyword") or "").strip()
            if not winner:
                continue

            intent = cluster.get("intent", "commercial")
            label = cluster.get("cluster_name", "")

            # Check if this keyword is already owned by existing content
            if winner.lower() in {k.lower() for k in existing_map}:
                owner_url = existing_map.get(winner) or existing_map.get(
                    next((k for k in existing_map if k.lower() == winner.lower()), ""), ""
                )
                try:
                    db_write(
                        """INSERT OR REPLACE INTO topic_map
                           (tenant_id, primary_keyword, cluster_label, intent, owner_url, status)
                           VALUES (?, ?, ?, ?, ?, 'owned_existing')""",
                        (self.ctx.tenant_id, winner, label, intent, owner_url)
                    )
                except Exception:
                    pass  # UNIQUE constraint — already exists
                skipped += 1
            else:
                try:
                    db_write(
                        """INSERT OR REPLACE INTO topic_map
                           (tenant_id, primary_keyword, cluster_label, intent, status)
                           VALUES (?, ?, ?, ?, 'planned')""",
                        (self.ctx.tenant_id, winner, label, intent)
                    )
                    inserted += 1
                except Exception:
                    pass  # UNIQUE constraint

        self.log(f"topic_map: {inserted} planned, {skipped} owned by existing content")

    @staticmethod
    def _repair_json(text: str) -> str | None:
        """Attempt to repair truncated/ malformed JSON from LLM output.
        Common failures: unterminated strings, missing closing brackets.
        Returns repaired JSON string or None if unrepairable."""
        if not text or not text.strip().startswith('{'):
            return None
        # Strategy: find the last valid complete object/array by counting brackets
        # Remove everything after the last valid structural close
        depth = 0
        in_string = False
        escape = False
        last_valid_pos = len(text)
        for i, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in '{[':
                depth += 1
            elif ch in '}]':
                depth -= 1
                if depth <= 0:
                    last_valid_pos = i + 1
                    if depth < 0:
                        break
        if last_valid_pos < len(text):
            repaired = text[:last_valid_pos]
            # Try to close unclosed strings
            if repaired.rstrip().endswith('"') or repaired.rstrip().endswith(']'):
                return repaired
            # Try adding closing brackets
            open_braces = repaired.count('{') - repaired.count('}')
            open_brackets = repaired.count('[') - repaired.count(']')
            suffix = '}' * open_braces + ']' * open_brackets
            return repaired + suffix
        return text[:last_valid_pos]
