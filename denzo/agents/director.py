"""
Pipeline Director — The AI Brain of DENZO SEO
==============================================
The Director is the autonomous orchestrator of the entire SEO pipeline.
It thinks, decides, and acts like a Senior SEO Marketing Director with 15 years
of experience. It doesn't just run agents in sequence — it evaluates quality,
detects problems, retries intelligently, and adapts the strategy based on results.

Personality:
- Obsessed with results, not process
- Zero tolerance for agents that produce empty or low-quality output
- Proactive: doesn't wait for problems, anticipates them
- Decisive: makes calls quickly with available data
- Transparent: logs every decision with reasoning
"""
import json
import time
import threading

from denzo.agents.base_agent import (
    TenantAwareBaseAgent,
    ClientContext,
    db_execute,
    db_write,
    strip_json_fences,
)


class PipelineDirector(TenantAwareBaseAgent):
    """
    Autonomous orchestrator that drives the full SEO pipeline using Claude
    as its decision engine. Runs every 30 seconds, assesses state, decides
    what to start/stop/retry, and logs all reasoning.
    """

    def __init__(self, ctx: ClientContext):
        super().__init__("Pipeline Director", ctx, layer=0, color="indigo")
        self._stop_flag = False
        self._consecutive_parse_failures = 0

    # ── State Assessment ──────────────────────────────────────────────────────

    def _assess_state(self) -> dict:
        """Build a complete picture of the pipeline state."""
        tid = self.ctx.tenant_id

        # Single query for all keyword + page counts
        counts = db_execute(
            """SELECT
                (SELECT COUNT(*) FROM keywords WHERE tenant_id=?)                                   AS kw_total,
                (SELECT COUNT(*) FROM keywords WHERE tenant_id=? AND priority = 'high')             AS kw_high,
                (SELECT COUNT(*) FROM pages    WHERE tenant_id=?)                                   AS pg_total,
                (SELECT COUNT(*) FROM pages    WHERE tenant_id=? AND status='published')            AS pg_pub,
                (SELECT COUNT(*) FROM pages    WHERE tenant_id=? AND status='ready')                AS pg_ready,
                (SELECT COUNT(*) FROM pages    WHERE tenant_id=? AND status='draft')                AS pg_draft,
                (SELECT COUNT(*) FROM competitors WHERE tenant_id=?)                                AS comp_total
            """,
            (tid, tid, tid, tid, tid, tid, tid)
        )
        c = counts[0] if counts else {}
        kw_count   = c["kw_total"]   or 0
        kw_high    = c["kw_high"]    or 0
        page_total = c["pg_total"]   or 0
        page_pub   = c["pg_pub"]     or 0
        page_ready = c["pg_ready"]   or 0
        page_draft = c["pg_draft"]   or 0
        comp_count = c["comp_total"] or 0

        agent_rows = db_execute(
            """SELECT name, status, current_task, last_run_at, run_count, layer
               FROM agents WHERE tenant_id=? AND name != 'Pipeline Director'
               ORDER BY layer, name""",
            (tid,),
        )
        agents = [dict(r) for r in agent_rows] if agent_rows else []

        error_rows = db_execute(
            """SELECT agent, message, created_at FROM activity
               WHERE tenant_id=? AND level='error'
               AND created_at > datetime('now', '-30 minutes')
               ORDER BY id DESC LIMIT 10""",
            (tid,),
        )
        errors = [dict(r) for r in error_rows] if error_rows else []

        # Quality metrics
        quality_rows = db_execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN quality_score IS NULL THEN 1 ELSE 0 END) as unscored,
                SUM(CASE WHEN quality_score < 70 THEN 1 ELSE 0 END) as low_quality,
                AVG(CASE WHEN quality_score IS NOT NULL THEN quality_score END) as avg_score
               FROM pages WHERE tenant_id=? AND status IN ('ready','published')
               AND content IS NOT NULL""",
            (tid,)
        )
        quality = dict(quality_rows[0]) if quality_rows else {"total": 0, "unscored": 0, "low_quality": 0, "avg_score": 0}
        # Round avg_score
        if quality.get("avg_score"):
            quality["avg_score"] = round(float(quality["avg_score"]), 1)

        return {
            "keywords": {"total": kw_count, "high_priority": kw_high},
            "pages": {
                "total": page_total,
                "draft": page_draft,
                "ready": page_ready,
                "published": page_pub,
            },
            "quality": quality,
            "competitors": comp_count,
            "agents": agents,
            "recent_errors": errors,
        }

    # ── AI Decision Engine ────────────────────────────────────────────────────

    def _decide(self, state: dict) -> dict:
        """Ask Claude what to do next. Returns structured decision."""

        system = """You are the Pipeline Director AI for DENZO SEO — the world's most advanced multi-vertical SEO + GEO platform. You operate as an autonomous Senior SEO Director with 15 years of experience across local SEO, GEO (Generative Engine Optimization), content strategy, and AI-powered marketing for ANY business vertical.

Your personality:
- Results-obsessed: keywords ranked, pages published, GEO citations earned — not just "agents running"
- Zero bullshit: if an agent produces no data, retry or escalate immediately
- Strategic: understand the dependency chain; never waste tokens running Layer 3 without Layer 1 data
- Proactive: spot problems before they become failures
- Decisive: clear calls with brief reasoning, always

You orchestrate a world-class 26-agent SEO pipeline:

LAYER 1 — Intelligence (run ALL simultaneously — they're independent):
  Keyword Strategist      → discovers and scores keywords for this vertical/location
  Keyword Clusterer       → groups keywords into semantic clusters (needs ≥10 keywords first)
  Competitor Intel        → maps competitors, tier analysis, positioning gaps
  Technical Auditor       → site audit, CWV, crawlability, schema validation
  Site Style Analyzer     → brand colors, fonts, visual identity extraction
  Data Intelligence       → citation-bait data, statistics, authority content
  GBP Optimizer           → Google Business Profile analysis + optimization plan

LAYER 2 — Strategy (after Layer 1 has ≥20 keywords):
  E-E-A-T Architect       → authority pillars, content priorities, trust signals
  Schema Engineer         → LocalBusiness + FAQ + Review + HowTo + Service + SpeakableSpec
  Vertical Matrix Gen.    → expands page matrix for specific vertical (damage types, insurance KWs, procedures, etc.)

LAYER 3 — Generation (after ALL Layer 2 agents done + ≥20 keywords):
  Programmatic SEO        → AI-writes full page content for all draft stubs

LAYER 4 — Optimization (after Programmatic SEO done + pages.ready ≥ 10):
  Content Optimizer       → quality scoring + rewrite to ≥70 score
  Visual Content Optimizer→ hero images, visual elements, brand alignment
  GEO Optimizer           → optimize for AI Answer Engines (Perplexity, ChatGPT, Gemini AI Overviews)
  Internal Linker         → hub-and-spoke link architecture
  Content Freshness       → refresh published pages older than 90 days (Layer 4 but runs after publishing)

LAYER 5 — Publishing (pages.ready ≥ 1, quality gate passed):
  GitHub Publisher        → HTML pages + sitemap.xml + robots.txt + llms.txt
  WordPress Publisher     → WP posts + sitemap ping + llms.txt page

LAYER 6 — Analytics (pages.published ≥ 1):
  GEO Query Generator     → generates AI search query bank for monitoring
  GEO Monitor             → tracks citations in ChatGPT, Perplexity, Gemini, Claude
  Rank Tracker            → estimates keyword positions (Apify real data or AI estimates)
  SERP Intelligence       → featured snippet opportunities, PAA, local pack analysis
  Reviews Intelligence    → competitor review analysis, pain points, content opportunities
  ROI Attribution         → citation rate, ranking improvements, traffic attribution

DEPENDENCY RULES (enforce strictly):
- Keyword Clusterer: requires ≥10 keywords (run after Keyword Strategist produces results)
- Layer 2: requires ≥20 keywords in DB
- Vertical Matrix Generator: requires E-E-A-T Architect status='done' (needs the page strategy first)
- Layer 3 (Programmatic SEO): requires ALL three Layer 2 agents status='done' AND keywords ≥ 20
- Layer 4: requires Programmatic SEO status='done' + pages.ready ≥ 10
- Content Freshness: requires pages.published ≥ 1 (refreshes existing published content)
- Layer 5: requires pages.ready ≥ 1 + quality gate passed
- Layer 6: requires pages.published ≥ 1

QUALITY GATE (non-negotiable):
- Keyword Strategist: must produce ≥30 keywords. Retry if fewer.
- Competitor Intel: must produce ≥3 competitors. Log warning if fewer, but continue.
- Programmatic SEO: must produce ≥10 ready pages. Retry once if fewer.
- Content quality: avg quality_score ≥ 70 before publishing.
  → If unscored > 0 and Content Optimizer idle: start Content Optimizer
  → If avg_score < 70 and Content Optimizer run_count < 5: start Content Optimizer
  → If avg_score ≥ 70 OR Content Optimizer run_count ≥ 5: proceed to publish

EXECUTION RULES:
- Never start an agent currently 'working'
- Layer 1: start ALL idle Layer 1 agents simultaneously when keywords < 20
- After Keyword Strategist done with ≥10 KWs: also start Keyword Clusterer if idle
- Layer 2: start E-E-A-T Architect + Schema Engineer together when keywords ≥ 20; start Vertical Matrix Generator once E-E-A-T is done
- Layer 3: start when ALL Layer 2 done (E-E-A-T + Schema + Vertical Matrix)
- Layer 4: start Content Optimizer, GEO Optimizer, Visual Content Optimizer, Internal Linker simultaneously when Programmatic SEO done + pages.ready ≥ 10
- Layer 5: start GitHub Publisher (if github_repo set) OR WordPress Publisher (if wp_url set) after quality gate
- Layer 6: start all analytics agents after first pages published
- Content Freshness: start after pages.published ≥ 5 (only if not already running)
- GBP Optimizer: start alongside other Layer 1 agents — it's independent
- Error handling: retry if run_count < 3; skip and move forward if run_count ≥ 3
- pipeline_complete: declare ONLY when publisher done + pages.published ≥ 10 + quality.avg_score ≥ 70 (or Content Optimizer run_count ≥ 5)
- After pipeline_complete: Director stops. Analytics agents are self-sufficient and run independently.

GEO AWARENESS: This platform serves ANY vertical — auto body shops, dental, law firms, HVAC, restaurants, real estate, etc. Keyword Strategist and Vertical Matrix Generator adapt automatically. Your decisions should be vertical-agnostic.

Your output must be valid JSON with no markdown:
{
  "assessment": "one sentence on the current state",
  "action": "start_layer|wait|retry_agent|stop_agent|pipeline_complete|pipeline_blocked",
  "targets": ["Agent Name 1", "Agent Name 2"],
  "reasoning": "why you're making this call (2-3 sentences max)",
  "urgency": "high|medium|low",
  "next_check_seconds": 30
}"""

        state_json = json.dumps(state, indent=2, default=str)
        prompt = f"""Current pipeline state for {self.ctx.client_name}:

{state_json}

What is your decision? Return JSON only, no markdown."""

        raw = self.call_claude(
            prompt,
            max_tokens=800,
            system=system,
            model="claude-sonnet-4-6",
        )
        if not raw:
            return {
                "action": "wait",
                "targets": [],
                "reasoning": "Empty response from AI — will retry next cycle.",
                "next_check_seconds": 60,
            }

        try:
            result = json.loads(strip_json_fences(raw))
            self._consecutive_parse_failures = 0
            return result
        except Exception as exc:
            self._consecutive_parse_failures += 1
            self.log(
                f"[Director] Could not parse AI decision "
                f"(consecutive failure #{self._consecutive_parse_failures}): {exc}",
                "warning",
            )
            if self._consecutive_parse_failures >= 3:
                return {
                    "action": "pipeline_blocked",
                    "targets": [],
                    "reasoning": (
                        f"AI decision engine returned unparseable JSON "
                        f"{self._consecutive_parse_failures} times in a row. "
                        "Pipeline cannot continue autonomously."
                    ),
                    "next_check_seconds": 60,
                }
            return {
                "action": "wait",
                "targets": [],
                "reasoning": "Could not parse AI decision — retrying next cycle.",
                "next_check_seconds": 30,
            }

    # ── Execution Engine ──────────────────────────────────────────────────────

    def _execute_decision(self, decision: dict):
        """Execute the Director's decision."""
        action = decision.get("action", "wait")
        targets = decision.get("targets", [])
        reason = decision.get("reasoning", "")
        assessment = decision.get("assessment", "")
        urgency = decision.get("urgency", "low")

        if assessment:
            self.log(f"[Director] {assessment}", "info")

        log_level = "warning" if urgency == "high" else "info"
        self.log(
            f"[Director] Action: {action} → {', '.join(targets) or 'none'} | {reason}",
            log_level,
        )

        if action in ("start_layer", "retry_agent"):
            for agent_name in targets:
                self._start_agent(agent_name)

        elif action == "stop_agent":
            for agent_name in targets:
                self._stop_agent(agent_name)

        elif action == "pipeline_complete":
            self.log(
                "[Director] Pipeline complete — all layers executed successfully.",
                "success",
            )
            self.set_status("done", "Pipeline complete")
            self._stop_flag = True

        elif action == "pipeline_blocked":
            self.log(f"[Director] Pipeline blocked — {reason}", "error")
            self.set_status("error", reason[:200])

        # "wait" — do nothing, next cycle will re-evaluate

    def _start_agent(self, agent_name: str):
        """Start an agent in its own daemon thread."""
        from denzo.agents.registry import AGENT_REGISTRY, get_agent

        if agent_name not in AGENT_REGISTRY:
            self.log(f"[Director] Unknown agent: {agent_name}", "error")
            return

        # Check not already running
        rows = db_execute(
            "SELECT status FROM agents WHERE tenant_id=? AND name=?",
            (self.ctx.tenant_id, agent_name),
        )
        if rows and rows[0]["status"] == "working":
            return  # already running — skip silently

        ctx = self.ctx

        def _thread():
            try:
                db_write(
                    """UPDATE agents
                       SET status='working', current_task='Starting...', updated_at=CURRENT_TIMESTAMP,
                           last_run_at=CURRENT_TIMESTAMP, run_count=run_count+1
                       WHERE tenant_id=? AND name=?""",
                    (ctx.tenant_id, agent_name),
                )
                agent = get_agent(agent_name, ctx)
                agent.run()

                # Only override status if it's still 'working' (agent didn't self-set)
                status_rows = db_execute(
                    "SELECT status FROM agents WHERE tenant_id=? AND name=?",
                    (ctx.tenant_id, agent_name),
                )
                if status_rows and status_rows[0]["status"] == "working":
                    db_write(
                        """UPDATE agents
                           SET status='done', current_task='Completed', updated_at=CURRENT_TIMESTAMP
                           WHERE tenant_id=? AND name=?""",
                        (ctx.tenant_id, agent_name),
                    )
            except Exception as exc:
                db_write(
                    """UPDATE agents
                       SET status='error', current_task=?, updated_at=CURRENT_TIMESTAMP
                       WHERE tenant_id=? AND name=?""",
                    (str(exc)[:200], ctx.tenant_id, agent_name),
                )
                # Log error to activity
                db_write(
                    "INSERT INTO activity (tenant_id,type,message,agent,level,created_at) VALUES (?,?,?,?,?,datetime('now'))",
                    (ctx.tenant_id, "agent", f"{agent_name} crashed: {str(exc)[:150]}", agent_name, "error"),
                )

        t = threading.Thread(
            target=_thread,
            daemon=True,
            name=f"director:{ctx.tenant_id}:{agent_name}",
        )
        t.start()
        self.log(f"[Director] Started {agent_name}", "info")

    def _stop_agent(self, agent_name: str):
        """Signal an agent to stop by marking it idle in the DB."""
        db_write(
            """UPDATE agents
               SET status='idle', current_task='Stopped by Director', updated_at=CURRENT_TIMESTAMP
               WHERE tenant_id=? AND name=?""",
            (self.ctx.tenant_id, agent_name),
        )
        self.log(f"[Director] Stopped {agent_name}", "warning")

    # ── Deadlock Detection ────────────────────────────────────────────────────

    def _check_deadlock(self, agents: list) -> str | None:
        """
        Returns a blocked reason string if all agents in the lowest active layer
        are stuck in 'error' with run_count >= 3. Returns None if pipeline is healthy.
        """
        # Group non-done/idle agents by layer
        active = [a for a in agents if a["status"] not in ("done", "idle")]
        if not active:
            return None

        # Find lowest layer with active (working/error) agents
        min_layer = min(a["layer"] for a in active if a["layer"] is not None)
        layer_agents = [a for a in active if a["layer"] == min_layer]

        # Deadlock: every agent in this layer is in error with run_count >= 3
        errored = [a for a in layer_agents if a["status"] == "error" and (a.get("run_count") or 0) >= 3]
        if errored and len(errored) == len(layer_agents):
            names = ", ".join(a["name"] for a in errored)
            return (
                f"Layer {min_layer} deadlock: all agents failed ≥3 times — {names}. "
                "Manual intervention required."
            )
        return None

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def _watchdog(self):
        """
        Reset agents that have been 'working' for >10 minutes with no recent
        activity log entry. These are zombie threads from a previous server run
        or a hung agent.
        """
        stale = db_execute(
            """SELECT name FROM agents
               WHERE tenant_id=? AND status='working'
               AND updated_at < datetime('now', '-10 minutes')
               AND name != 'Pipeline Director'""",
            (self.ctx.tenant_id,)
        )
        for row in (stale or []):
            name = row["name"]
            db_write(
                """UPDATE agents SET status='idle', current_task='Reset by watchdog', last_message=NULL
                   WHERE tenant_id=? AND name=?""",
                (self.ctx.tenant_id, name)
            )
            self.log(f"[Watchdog] Reset stale agent '{name}' (working >10 min)", "warning")

    # ── Publisher error recovery ───────────────────────────────────────────────

    def _reset_blocked_publishers(self):
        """
        If a publisher is in 'error' state but credentials are now available,
        reset it to 'idle' so the Director can retry it this cycle.
        """
        tid = self.ctx.tenant_id
        # GitHub Publisher
        if self.ctx.github_repo and self.ctx.github_token:
            db_write(
                """UPDATE agents SET status='idle', current_task=NULL, last_message=NULL
                   WHERE tenant_id=? AND name='GitHub Publisher' AND status='error'""",
                (tid,)
            )
        # WordPress Publisher
        if self.ctx.wp_url and self.ctx.wp_user and self.ctx.wp_app_password:
            db_write(
                """UPDATE agents SET status='idle', current_task=NULL, last_message=NULL
                   WHERE tenant_id=? AND name='WordPress Publisher' AND status='error'""",
                (tid,)
            )

    # ── Quality Gate ──────────────────────────────────────────────────────────

    def _run_quality_gate(self):
        """
        Check published pages for quality issues.
        Re-queue low-quality pages for Content Optimizer to re-process.
        Returns number of pages re-queued.
        """
        tid = self.ctx.tenant_id
        low_quality = db_execute(
            """SELECT id, title, quality_score
               FROM pages WHERE tenant_id=?
               AND status='published'
               AND (quality_score IS NULL OR quality_score < 70)
               AND content IS NOT NULL""",
            (tid,)
        )
        if not low_quality:
            return 0

        requeued = 0
        for page in low_quality:
            pid = page["id"]
            score = page["quality_score"]
            db_write(
                """UPDATE pages SET status='ready', quality_score=NULL,
                   updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?""",
                (pid, tid)
            )
            self.log(f"[QA Gate] Re-queued page id={pid} score={score} for re-optimization", "warning")
            requeued += 1

        if requeued:
            self.log(f"[QA Gate] {requeued} pages below quality threshold — re-queuing for Content Optimizer", "warning")
            # Reset Content Optimizer to idle so it can re-run
            db_write(
                """UPDATE agents SET status='idle', current_task='Re-running for quality gate',
                   updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND name='Content Optimizer'""",
                (tid,)
            )

        return requeued

    # ── Main Run Loop ─────────────────────────────────────────────────────────

    def run(self):
        self.log("[Director] Autonomous pipeline director activated.", "success")
        self.set_status("working", "Orchestrating pipeline")
        self._stop_flag = False

        MAX_CYCLES = 120  # 120 × 30s = 60 minutes max
        cycles = 0

        while not self.should_stop() and not self._stop_flag and cycles < MAX_CYCLES:
            cycles += 1
            try:
                # Watchdog: reset zombie agents before assessing state
                self._watchdog()
                self._reset_blocked_publishers()

                # Quality gate: run BEFORE assessing state so Claude sees re-queued pages
                pub_rows = db_execute(
                    "SELECT status, run_count FROM agents WHERE tenant_id=? AND name='GitHub Publisher'",
                    (self.ctx.tenant_id,)
                )
                if pub_rows and pub_rows[0]["status"] == "done" and pub_rows[0]["run_count"] > 0:
                    requeued = self._run_quality_gate()
                    if requeued > 0:
                        self.log(f"[Director] Quality gate found {requeued} pages needing improvement — pipeline continuing", "warning")

                state = self._assess_state()

                # Deadlock guard: if a whole layer is permanently errored, stop now
                deadlock_reason = self._check_deadlock(state.get("agents", []))
                if deadlock_reason:
                    self.log(f"[Director] {deadlock_reason}", "error")
                    self.set_status("error", deadlock_reason[:200])
                    break

                decision = self._decide(state)
                self._execute_decision(decision)

                wait_secs = max(15, min(120, decision.get("next_check_seconds", 30)))

                # Poll should_stop() while waiting (0.5s granularity)
                for _ in range(wait_secs * 2):
                    if self.should_stop() or self._stop_flag:
                        break
                    time.sleep(0.5)

            except Exception as exc:
                self.log(f"[Director] Cycle error: {exc}", "error")
                # Wait 30s on unexpected errors
                for _ in range(60):
                    if self.should_stop() or self._stop_flag:
                        break
                    time.sleep(0.5)

        if not self._stop_flag:
            self.set_status("done", "Orchestration complete")
        self.log("[Director] Director shutting down.", "info")
