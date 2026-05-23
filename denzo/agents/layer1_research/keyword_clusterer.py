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

        # Load all keywords for this tenant
        rows = db_execute(
            "SELECT keyword, intent, category, priority FROM keywords WHERE tenant_id=?",
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

        raw = self.call_claude(prompt, max_tokens=6000, model="claude-sonnet-4-6")

        if not raw:
            self.log("AI returned empty response", "error")
            self.set_status("error", "Empty API response")
            return

        cleaned = strip_json_fences(raw, start_char="{")
        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as e:
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

        if n_risks > 0:
            for risk in cannibalization_risks[:5]:
                self.log(f"Cannibalization risk: {risk}", "warning")

        self.set_status(
            "done",
            f"{n_clusters} keyword clusters — {n_risks} cannibalization risks resolved"
        )
