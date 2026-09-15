"""
Pipeline Director — Autonomous SEO Pipeline Orchestrator
=========================================================
Deterministic Python state machine that drives the 26-agent pipeline.
No Claude API calls in the decision loop — only ONE strategic planning
call at startup.

Dependency rules are enforced in code, not in prompts.
"""
import json
import time

from denzo.agents.base_agent import (
    TenantAwareBaseAgent,
    ClientContext,
    db_execute,
    db_write,
    strip_json_fences,
)


# ── Agent names by layer ──────────────────────────────────────────────────────

# Discovery agents (Capa 0.5) — must complete before any Layer 1+ generation
DISCOVERY_AGENTS = ["Site Inventory", "Keyword Footprint", "GEO Baseline"]

LAYER_1 = [
    "Keyword Strategist", "Keyword Clusterer", "Competitor Intel",
    "Technical Auditor", "Site Style Analyzer", "Data Intelligence",
    "GBP Optimizer",
]
LAYER_2 = ["E-E-A-T Architect", "Schema Engineer"]
LAYER_2B = ["Vertical Matrix Generator"]  # needs EEAT done
LAYER_3 = ["Programmatic SEO"]
LAYER_4 = ["Visual Content Optimizer", "GEO Optimizer", "Internal Linker", "Content Optimizer"]
LAYER_4B = ["Content Freshness"]  # needs pages published
LAYER_5 = ["GitHub Publisher", "WordPress Publisher", "Indexation Accelerator",
           "GBP Autopilot", "Video Engine"]
LAYER_6 = [
    "Rank Tracker", "GEO Query Generator", "GEO Monitor",
    "SERP Intelligence", "Reviews Intelligence", "ROI Attribution",
    "Content Duplicate Checker", "Perplexity Tracker", "GEO Gap Closer",
]

ALL_AGENTS = LAYER_1 + LAYER_2 + LAYER_2B + LAYER_3 + LAYER_4 + LAYER_4B + LAYER_5 + LAYER_6


class PipelineDirector(TenantAwareBaseAgent):
    """
    Autonomous orchestrator using a deterministic state machine.
    Evaluates pipeline state every 30 seconds and starts/retries agents
    based on dependency rules enforced in Python code.
    """

    def __init__(self, ctx: ClientContext):
        super().__init__("Pipeline Director", ctx, layer=0, color="indigo")
        self._stop_flag = False
        self._strategy = None  # populated by _generate_strategy() on first run
        self._publisher_skip_warned = False

    # ── State Assessment ──────────────────────────────────────────────────────

    def _assess_state(self) -> dict:
        """Build a complete picture of the pipeline state."""
        tid = self.ctx.tenant_id

        counts = db_execute(
            """SELECT
                (SELECT COUNT(*) FROM keywords WHERE tenant_id=?)     AS kw_total,
                (SELECT COUNT(*) FROM keywords WHERE tenant_id=? AND priority='high') AS kw_high,
                (SELECT COUNT(*) FROM pages    WHERE tenant_id=?)     AS pg_total,
                (SELECT COUNT(*) FROM pages    WHERE tenant_id=? AND status='published') AS pg_pub,
                (SELECT COUNT(*) FROM pages    WHERE tenant_id=? AND status='ready')     AS pg_ready,
                (SELECT COUNT(*) FROM pages    WHERE tenant_id=? AND status='draft')     AS pg_draft,
                (SELECT COUNT(*) FROM competitors WHERE tenant_id=?)  AS comp_total
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
            """SELECT name, status, current_task, run_count, layer, retry_after
               FROM agents WHERE tenant_id=? AND name != 'Pipeline Director'
               ORDER BY layer, name""",
            (tid,),
        )
        agents = [dict(r) for r in agent_rows] if agent_rows else []

        # Data samples for visibility
        sample_kws = db_execute(
            "SELECT keyword, priority, location FROM keywords WHERE tenant_id=? ORDER BY id DESC LIMIT 5",
            (tid,)
        )
        sample_pages = db_execute(
            "SELECT title, status, quality_score FROM pages WHERE tenant_id=? AND content IS NOT NULL ORDER BY id DESC LIMIT 3",
            (tid,)
        )

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
        if quality.get("avg_score"):
            quality["avg_score"] = round(float(quality["avg_score"]), 1)

        return {
            "keywords": {"total": kw_count, "high_priority": kw_high},
            "pages": {"total": page_total, "draft": page_draft, "ready": page_ready, "published": page_pub,
                      "approved": db_execute("SELECT COUNT(*) n FROM pages WHERE tenant_id=? AND status='ready' AND approval_hash IS NOT NULL AND quality_score>=70 AND COALESCE(managed,1)=1", (tid,))[0]['n']},
            "quality": quality,
            "competitors": comp_count,
            "agents": agents,
            "sample_keywords": [dict(r) for r in (sample_kws or [])],
            "sample_pages": [dict(r) for r in (sample_pages or [])],
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _agent_status(self, name: str, agents: list) -> str:
        """Return the status string for a named agent, or 'unknown'."""
        for a in agents:
            if a["name"] == name:
                return a["status"]
        return "unknown"

    def _agent_run_count(self, name: str, agents: list) -> int:
        for a in agents:
            if a["name"] == name:
                return a.get("run_count", 0) or 0
        return 0

    def _all_done(self, names, agents):
        statuses = {a['name']:a['status'] for a in agents}
        return all(statuses.get(n) in ('done','skipped') for n in names)

    def _any_done(self, names: list[str], agents: list) -> bool:
        """True if at least one named agent is 'done'."""
        statuses = {a["name"]: a["status"] for a in agents}
        return any(statuses.get(n) == "done" for n in names)

    def _idle_in_layer(self, names: list[str], agents: list) -> list[str]:
        """Return names of idle agents within a list."""
        statuses = {a["name"]: a["status"] for a in agents}
        return [n for n in names if statuses.get(n) == "idle"]

    def _retriable_errors(self, names: list[str], agents: list, max_retries: int = 3) -> list[str]:
        """Return names of agents in 'error' state with run_count < max_retries."""
        result = []
        for a in agents:
            if a["name"] in names and a["status"] == "error":
                if (a.get("run_count") or 0) < max_retries:
                    ready = db_execute("SELECT 1 FROM agents WHERE tenant_id=? AND name=? AND (retry_after IS NULL OR retry_after<=datetime('now'))", (self.tenant_id,a['name']))
                    last = db_execute('SELECT retryable FROM agent_jobs WHERE tenant_id=? AND agent_name=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (self.tenant_id,a['name']))
                    if ready and (not last or last[0]['retryable']):
                        result.append(a["name"])
        return result

    def _layer_summary(self, names: list[str], agents: list) -> str:
        """Compact status summary for a layer."""
        statuses = {a["name"]: a["status"] for a in agents}
        parts = []
        for n in names:
            st = statuses.get(n, "unknown")[:1].upper()  # I/W/D/E
            parts.append(f"{n}={st}")
        return ", ".join(parts)

    # ── Strategic Plan (ONE Claude call at startup) ───────────────────────────

    def _generate_strategy(self):
        """Generate the strategic plan ONCE. Stored in self._strategy and DB."""
        tid = self.ctx.tenant_id

        # Check for existing plan
        existing = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key='pipeline_plan'",
            (tid,)
        )
        if existing:
            try:
                self._strategy = json.loads(existing[0]["value"])
                self.log(f"[Director] Loaded existing pipeline plan: {self._strategy.get('strategy', '')[:100]}", "info")
                return
            except Exception:
                pass

        # Gather context for strategy
        kw_sample = db_execute(
            "SELECT keyword, priority, category FROM keywords WHERE tenant_id=? ORDER BY id LIMIT 30",
            (tid,)
        )
        kw_list = [dict(r) for r in (kw_sample or [])]

        comp_sample = db_execute(
            "SELECT name, location, tier FROM competitors WHERE tenant_id=? LIMIT 10",
            (tid,)
        )
        comp_list = [dict(r) for r in (comp_sample or [])]

        prompt = f"""{self.ctx.to_prompt_block()}

You are planning the SEO pipeline execution for this business.

Current state:
- Keywords in DB: {len(kw_list)}
- Competitors analyzed: {len(comp_list)}
- Industry: {self.ctx.industry_vertical}

Sample keywords: {json.dumps(kw_list[:10], ensure_ascii=False)}
Sample competitors: {json.dumps(comp_list[:5], ensure_ascii=False)}

Design a ONE-SENTENCE strategy, then list priority keywords and content pillars.

Return JSON:
{{
  "strategy": "one-sentence high-level plan",
  "priority_keywords": ["kw1", "kw2", "kw3"],
  "content_pillars": ["pillar 1", "pillar 2", "pillar 3"],
  "estimated_pages": 50,
  "target_verticals": ["service pages", "location pages", "brand pages"],
  "notes": "any special considerations"
}}
Return ONLY valid JSON."""

        raw = self.call_claude(prompt, max_tokens=800, model="claude-sonnet-4-6")
        if not raw:
            self._strategy = {"strategy": "Default pipeline execution", "priority_keywords": [], "content_pillars": [], "estimated_pages": 0}
            return

        try:
            self._strategy = json.loads(strip_json_fences(raw))
            self.save_output("pipeline_plan", self._strategy)
            self.log(f"[Director] Strategic plan generated: {self._strategy.get('strategy', '')[:120]}", "success")
        except Exception:
            self._strategy = {"strategy": "Default pipeline execution", "priority_keywords": [], "content_pillars": [], "estimated_pages": 0}

    # ── Reconciliation ───────────────────────────────────────────────────────

    def _reconcile_world_state(self, agents: dict):
        """Consolidate SiteInventory + KeywordFootprint + GEOBaseline into world_state.
        Called automatically when all 3 discovery agents are done.
        """
        import json as _json

        # Load individual outputs from settings
        inventory = self._load_json_setting("site_inventory")
        footprint = self._load_json_setting("existing_keyword_map")
        geo_baseline = self._load_json_setting("geo_baseline")

        # Build existing_urls from pages with origin='existing'
        inv_rows = db_execute(
            "SELECT slug, title, source_url, target_keyword, content_hash FROM pages "
            "WHERE tenant_id=? AND origin='existing'",
            (self.tenant_id,)
        )
        existing_urls = [dict(r) for r in (inv_rows or [])]

        # Build occupied_keywords map
        occupied = {}
        if footprint and isinstance(footprint, dict):
            occupied = footprint.get("keywords", {})

        # Build geo_gaps
        geo_gaps = []
        if geo_baseline and isinstance(geo_baseline, dict):
            total = geo_baseline.get("total_checks", 0)
            cited = geo_baseline.get("citations_found", 0)
            geo_gaps = [f"{total - cited} queries without citation (baseline)"]

        # Build managed_paths list
        mp_rows = db_execute(
            "SELECT publisher, path, managed FROM managed_paths WHERE tenant_id=?",
            (self.tenant_id,)
        )
        managed = [dict(r) for r in (mp_rows or [])]
        protected = [r for r in managed if r.get("managed") == 0]

        world_state = {
            "existing_urls": existing_urls,
            "occupied_keywords": occupied,
            "geo_gaps": geo_gaps,
            "managed_paths": managed,
            "protected_paths": protected,
            "reconciled_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        }

        self.save_output("world_state", world_state)
        self.log(f"[Director] World state reconciled: "
                 f"{len(existing_urls)} existing URLs, "
                 f"{len(occupied)} occupied keywords, "
                 f"{len(geo_gaps)} GEO gaps, "
                 f"{len(protected)} protected paths", "success")

    def _load_json_setting(self, key: str):
        """Helper: load a JSON setting value or return None."""
        import json as _json
        rows = db_execute(
            "SELECT value FROM settings WHERE tenant_id=? AND key=?",
            (self.tenant_id, key)
        )
        if rows and rows[0]["value"]:
            try:
                return _json.loads(rows[0]["value"])
            except Exception:
                pass
        return None

    # ── Prioritization / Feedback Loop ──────────────────────────────────────

    def _build_content_backlog(self, state: dict):
        """Write a prioritized content_backlog from Capa 6 signals.

        Consumed by Capa 3/4 agents to prioritize what to generate/refresh next.
        Called periodically when enough analytics data exists.
        """
        pg_pub = state["pages"]["published"]
        if pg_pub < 5:
            return  # Not enough data yet

        backlog = []

        # ── Signal 1: Declining rankings (from GSC / Rank Tracker) ──────────
        try:
            from denzo.agents.utils.gsc_client import is_gsc_connected, top_queries
            if is_gsc_connected(self.tenant_id):
                gsc_queries = top_queries(self.tenant_id, days=30, limit=30)
                for q in (gsc_queries or []):
                    pos = q.get("position", 100)
                    if 8 <= pos <= 20:  # Page 2 — opportunity
                        backlog.append({
                            "type": "refresh",
                            "keyword": q.get("query", ""),
                            "reason": f"Position {pos:.1f} — near miss",
                            "priority": "high" if pos <= 12 else "medium",
                        })
        except Exception:
            pass

        # ── Signal 2: GEO gaps (baseline vs monitor comparison) ─────────────
        geo_baseline = self._load_json_setting("geo_baseline")
        geo_monitor_data = self._load_json_setting("geo_monitor_results")
        if geo_baseline and geo_monitor_data:
            bl_cited = geo_baseline.get("citations_found", 0)
            bl_total = geo_baseline.get("total_checks", 0)
            mon_cited = geo_monitor_data.get("citations_found", 0)
            mon_total = geo_monitor_data.get("total_checks", 0)

            if mon_total > 0:
                still_missing = max(0, bl_total - mon_cited)
                if still_missing > 0:
                    backlog.append({
                        "type": "geo_gap",
                        "reason": f"{still_missing} queries still not cited (baseline {bl_cited}/{bl_total}, monitor {mon_cited}/{mon_total})",
                        "priority": "high",
                    })

        # ── Signal 3: Content freshness ─────────────────────────────────────
        stale = state["pages"].get("stale_count", 0)
        if stale > 0:
            backlog.append({
                "type": "refresh_stale",
                "reason": f"{stale} pages >90 days old",
                "priority": "medium",
            })

        if backlog:
            self.save_output("content_backlog", {
                "items": backlog,
                "total": len(backlog),
                "generated_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            })
            self.log(f"[Director] Content backlog: {len(backlog)} prioritized items", "info")

    # ── State Machine ──────────────────────────────────────────────────────────

    def _evaluate(self, state):
        agents = state['agents']
        def next_stage(names, serial=False):
            active = any(self._agent_status(n, agents) in ('working','starting') for n in names)
            choices = self._idle_in_layer(names,agents) + self._retriable_errors(names,agents)
            if serial:
                if active:
                    return []
                for name in names:
                    if self._agent_status(name,agents) not in ('done','skipped'):
                        return [name] if name in choices else []
                return []
            return choices

        if not db_execute("SELECT 1 FROM settings WHERE tenant_id=? AND key='world_state'", (self.tenant_id,)):
            if self._all_done(DISCOVERY_AGENTS, agents):
                self._reconcile_world_state(agents)
                return []
            return next_stage(DISCOVERY_AGENTS, serial=True)

        # Every stage finishes before dependent work can read its outputs.
        for stage, serial in [(LAYER_1, True), (LAYER_2+LAYER_2B, True), (LAYER_3, True), (LAYER_4, True)]:
            if not self._all_done(stage, agents):
                return next_stage(stage, serial=serial)

        publisher = 'WordPress Publisher' if self.ctx.publisher_type == 'wordpress' else 'GitHub Publisher'
        if state['pages'].get('approved',0) and self._agent_status(publisher,agents) not in ('done','skipped'):
            return next_stage([publisher], serial=True)
        if state['pages']['published']:
            if self._agent_status('Indexation Accelerator', agents) not in ('done','skipped'):
                return next_stage(['Indexation Accelerator'], serial=True)
            if not self._all_done(LAYER_6,agents):
                return next_stage(LAYER_6, serial=True)
        return []

    # ── Pipeline complete detection ──────────────────────────────────────────

    def _is_pipeline_complete(self, state):
        agents = state['agents']
        if any(a['status'] in ('working','starting') for a in agents):
            return False
        if not self._all_done(LAYER_1+LAYER_2+LAYER_2B+LAYER_3+LAYER_4,agents):
            return False
        return not self._evaluate(state)

    # ── Deadlock Detection ────────────────────────────────────────────────────

    def _check_deadlock(self, agents: list) -> str | None:
        """Returns reason string if pipeline is deadlocked, None if healthy."""
        publisher = 'WordPress Publisher' if self.ctx.publisher_type=='wordpress' else 'GitHub Publisher'
        scheduled = set(DISCOVERY_AGENTS+LAYER_1+LAYER_2+LAYER_2B+LAYER_3+LAYER_4+LAYER_6+[publisher,'Indexation Accelerator'])
        for agent in agents:
            if agent['name'] not in scheduled or agent['status']!='error':
                continue
            last = db_execute('SELECT retryable FROM agent_jobs WHERE tenant_id=? AND agent_name=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (self.tenant_id,agent['name']))
            if (agent.get('run_count') or 0)>=3 or (last and not last[0]['retryable']):
                return f"Pipeline stopped at {agent['name']}: retry or runtime limit reached. Check its error before restarting."
        return None

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def _watchdog(self):
        from denzo.execution import recover_expired_jobs
        recover_expired_jobs(self.tenant_id)

    # ── Publisher error recovery ───────────────────────────────────────────────

    def _reset_blocked_publishers(self):
        """If publisher creds are now available, reset errored publishers to idle."""
        tid = self.ctx.tenant_id
        if self.ctx.github_repo and self.ctx.github_token:
            db_write(
                """UPDATE agents SET status='idle', current_task=NULL, last_message=NULL
                   WHERE tenant_id=? AND name='GitHub Publisher' AND status='error'""",
                (tid,)
            )
        if self.ctx.wp_url and self.ctx.wp_user and self.ctx.wp_app_password:
            db_write(
                """UPDATE agents SET status='idle', current_task=NULL, last_message=NULL
                   WHERE tenant_id=? AND name='WordPress Publisher' AND status='error'""",
                (tid,)
            )

    # ── Quality Gate ──────────────────────────────────────────────────────────

    def _run_quality_gate(self):
        """Re-queue low-quality published pages for Content Optimizer."""
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
                "UPDATE pages SET status='ready', quality_score=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                (pid, tid)
            )
            self.log(f"[QA Gate] Re-queued page id={pid} score={score}", "warning")
            requeued += 1

        if requeued:
            self.log(f"[QA Gate] {requeued} pages below quality threshold — re-queuing", "warning")
            db_write(
                "UPDATE agents SET status='idle', current_task='Re-running for quality gate' WHERE tenant_id=? AND name='Content Optimizer'",
                (tid,)
            )

        return requeued

    # ── Main Run Loop ─────────────────────────────────────────────────────────

    def run(self):
        # Singleton guard is handled by AgentRunner.start() — it checks the DB
        # before setting status='working'. By the time we reach here, we ARE the
        # only Director instance for this tenant.
        self.log("[Director] Autonomous pipeline director activated (state machine mode).", "success")
        self.set_status("working", "Orchestrating pipeline")
        self._stop_flag = False

        # Generate strategy plan (one Claude call)
        try:
            self._generate_strategy()
        except Exception as e:
            self.log(f"[Director] Strategy generation failed (non-fatal): {e}", "warning")

        MAX_CYCLES = 240  # 240 × 30s = 2 hours max per run (can be restarted, Director persists state)
        cycles = 0
        cycle_errors = 0
        waiting_since = time.monotonic()

        while not self.should_stop() and not self._stop_flag and cycles < MAX_CYCLES:
            cycles += 1
            try:
                self._watchdog()
                state = self._assess_state()

                # Deadlock guard
                deadlock_reason = self._check_deadlock(state.get("agents", []))
                if deadlock_reason:
                    self.log(f"[Director] {deadlock_reason}", "error")
                    self.set_status("error", deadlock_reason[:200])
                    break

                # Check pipeline complete
                if self._is_pipeline_complete(state):
                    self.log("[Director] Current batch finished; approvals and deployment may still be pending.", "info")
                    self.set_status("done", "Batch finished — review content and publication status")
                    break

                # Decide and execute
                to_start = self._evaluate(state)
                has_running = any(a['status']=='working' for a in state['agents'])
                if has_running or to_start:
                    waiting_since = time.monotonic()
                elif time.monotonic()-waiting_since >= 300:
                    self.set_status('error','No agent can progress for 5 minutes; check queues, workers and prerequisites')
                    break
                for agent_name in to_start:
                    if self.should_stop() or self._stop_flag:
                        break
                    self._start_agent(agent_name)

                # Save progress every 10 cycles for resume capability
                if cycles % 10 == 0 and to_start:
                    self.save_output("pipeline_progress", {
                        "cycles_completed": cycles,
                        "agents_started_this_cycle": to_start,
                        "kw_total": state["keywords"]["total"],
                        "pg_pub": state["pages"]["published"],
                    })

                if not to_start and cycles%10==1:
                    self.log(f"[Director] No agents to start this cycle — waiting.", "info")
                cycle_errors = 0
                from denzo.runtime_limits import interruptible_wait
                interruptible_wait(30,self._stop)

            except Exception as exc:
                from denzo.execution import AgentCancelled
                from denzo.runtime_limits import RuntimeLimitExceeded,interruptible_wait
                if isinstance(exc,(AgentCancelled,RuntimeLimitExceeded)):
                    raise
                cycle_errors += 1
                self.log(f"[Director] Cycle error: {exc}", "error")
                if cycle_errors>=3:
                    self.set_status('error','Director stopped after 3 consecutive orchestration errors')
                    break
                interruptible_wait(30*cycle_errors,self._stop)

        if self.should_stop() or self._stop_flag:
            self.set_status("idle", "Stopped by user")
        else:
            if cycles >= MAX_CYCLES:
                # Ran out of cycles — pipeline not complete, save progress for resume
                self.save_output("pipeline_progress", {"cycles_completed": cycles, "last_state": self._assess_state()})
                self.set_status("idle", f"Paused after {cycles} cycles — click Run Pipeline to continue")
                self.log(f"[Director] Paused after {MAX_CYCLES} cycles. Progress saved. Restart to continue.", "warning")
            # Keep the terminal status set in the loop, including errors.
        self.log("[Director] Director shutting down.", "info")

    def _start_agent(self, agent_name: str):
        """Start an agent via the unified AgentRunner."""
        from denzo.agents.runner import AgentRunner

        result = AgentRunner.start(self.ctx.tenant_id, agent_name, ctx=self.ctx)
        status = result.get("status", "error")

        if status == "started":
            self.log(f"[Director] Started {agent_name}", "info")
        elif status == "already_running":
            pass
        elif status == 'busy':
            pass  # Admission control: do not turn overload into another job or retry.
        elif status == "prereq_failed":
            active = db_execute("SELECT 1 FROM agents WHERE tenant_id=? AND name!='Pipeline Director' AND status IN ('working','starting')", (self.tenant_id,))
            if not active:
                db_write("UPDATE agents SET status='error',run_count=3,current_task=? WHERE tenant_id=? AND name=?", (result.get('message','Prerequisites unavailable'),self.tenant_id,agent_name))
            self.log(f"[Director] {agent_name} prerequisites not met: {result.get('message')}", "warning")
        else:
            # Failures before reservation must also consume a bounded attempt.
            if not result.get('job_id'):
                db_write("UPDATE agents SET status='error',run_count=run_count+1,retry_after=datetime('now','+60 seconds'),current_task=? WHERE tenant_id=? AND name=? AND status NOT IN ('starting','working')", (result.get('message','Unable to start'),self.tenant_id,agent_name))
            self.log(f"[Director] Failed to start {agent_name}: {result.get('message')}", "error")

    def _stop_agent(self, agent_name: str):
        """Signal an agent to stop via the unified AgentRunner."""
        from denzo.agents.runner import AgentRunner

        AgentRunner.stop(self.ctx.tenant_id, agent_name)
        self.log(f"[Director] Stopped {agent_name}", "warning")
