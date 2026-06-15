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

LAYER_1 = [
    "Keyword Strategist", "Keyword Clusterer", "Competitor Intel",
    "Technical Auditor", "Site Style Analyzer", "Data Intelligence",
    "GBP Optimizer",
]
LAYER_2 = ["E-E-A-T Architect", "Schema Engineer"]
LAYER_2B = ["Vertical Matrix Generator"]  # needs EEAT done
LAYER_3 = ["Programmatic SEO"]
LAYER_4 = ["Content Optimizer", "Visual Content Optimizer", "GEO Optimizer", "Internal Linker"]
LAYER_4B = ["Content Freshness"]  # needs pages published
LAYER_5 = ["GitHub Publisher", "WordPress Publisher", "Indexation Accelerator"]
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
            """SELECT name, status, current_task, run_count, layer
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
            "pages": {"total": page_total, "draft": page_draft, "ready": page_ready, "published": page_pub},
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

    def _all_done(self, names: list[str], agents: list) -> bool:
        """True if ALL named agents are 'done'."""
        statuses = {a["name"]: a["status"] for a in agents}
        for name in names:
            st = statuses.get(name, "unknown")
            # Publisher self-skip counts as done
            if st == "done":
                continue
            if name in LAYER_5 and st == "error":
                # Check if it self-skipped (missing config)
                task = next((a.get("current_task", "") for a in agents if a["name"] == name), "")
                if "Skipped" in task or "skip" in task.lower():
                    continue
            return False
        return True

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

    # ── State Machine ──────────────────────────────────────────────────────────

    def _evaluate(self, state: dict) -> list[str]:
        """
        Python state machine — evaluates pipeline state and returns
        a list of agent names to start this cycle. NO Claude API calls.
        """
        agents = state["agents"]
        kw_total = state["keywords"]["total"]
        kw_high = state["keywords"]["high_priority"]
        pg_ready = state["pages"]["ready"]
        pg_pub = state["pages"]["published"]
        pg_draft = state["pages"]["draft"]
        comp_count = state["competitors"]
        avg_score = state["quality"].get("avg_score", 0) or 0
        unscored = state["quality"].get("unscored", 0) or 0

        to_start = []

        # ── Layer 1: Intelligence ───────────────────────────────────────────
        l1_idle = self._idle_in_layer(LAYER_1, agents)
        l1_retry = self._retriable_errors(LAYER_1, agents)

        if kw_total < 20:
            # Need keywords → start ALL idle Layer 1
            for name in l1_idle:
                to_start.append(name)
            for name in l1_retry:
                to_start.append(name)
            if to_start:
                self.log(f"[Director] L1 — starting intelligence agents (keywords={kw_total}, need ≥20)", "info")
        elif comp_count < 3 and "Competitor Intel" in (l1_idle + l1_retry):
            # Still need competitors
            if "Competitor Intel" in l1_idle:
                to_start.append("Competitor Intel")
            elif "Competitor Intel" in l1_retry:
                to_start.append("Competitor Intel")

        # Keyword Clusterer: needs ≥10 keywords
        if kw_total >= 10:
            kc_status = self._agent_status("Keyword Clusterer", agents)
            if kc_status == "idle":
                to_start.append("Keyword Clusterer")
            elif kc_status == "error" and self._agent_run_count("Keyword Clusterer", agents) < 3:
                to_start.append("Keyword Clusterer")

        # ── Layer 2: Strategy ──────────────────────────────────────────────
        l2_idle = self._idle_in_layer(LAYER_2, agents)
        l2_retry = self._retriable_errors(LAYER_2, agents)

        if kw_total >= 20:
            for name in l2_idle:
                to_start.append(name)
            for name in l2_retry:
                to_start.append(name)

        # Vertical Matrix Generator: needs E-E-A-T Architect done
        if self._agent_status("E-E-A-T Architect", agents) == "done":
            vmg_status = self._agent_status("Vertical Matrix Generator", agents)
            if vmg_status == "idle":
                to_start.append("Vertical Matrix Generator")
            elif vmg_status == "error" and self._agent_run_count("Vertical Matrix Generator", agents) < 3:
                to_start.append("Vertical Matrix Generator")

        # ── Layer 3: Generation ────────────────────────────────────────────
        l2_all = LAYER_2 + LAYER_2B
        if kw_total >= 20 and self._all_done(l2_all, agents):
            l3_idle = self._idle_in_layer(LAYER_3, agents)
            l3_retry = self._retriable_errors(LAYER_3, agents)
            for name in l3_idle:
                to_start.append(name)
            for name in l3_retry:
                to_start.append(name)

        # ── Layer 4: Optimization ──────────────────────────────────────────
        if self._agent_status("Programmatic SEO", agents) == "done" and pg_ready >= 5:
            l4_idle = self._idle_in_layer(LAYER_4, agents)
            l4_retry = self._retriable_errors(LAYER_4, agents)
            for name in l4_idle:
                to_start.append(name)
            for name in l4_retry:
                to_start.append(name)

        # ── Layer 5: Publishing ────────────────────────────────────────────
        quality_ok = avg_score >= 70 or self._agent_run_count("Content Optimizer", agents) >= 5
        publisher_ran = self._any_done(LAYER_5, agents)

        _PUBLISHERS = ["GitHub Publisher", "WordPress Publisher"]
        _INDEXERS  = ["Indexation Accelerator"]

        # Publishers: start when pages are ready and quality is OK
        if pg_ready >= 1 and quality_ok and not publisher_ran:
            for pub_name in _PUBLISHERS:
                pub_status = self._agent_status(pub_name, agents)
                if pub_status == "idle":
                    to_start.append(pub_name)
                elif pub_status == "error":
                    task = next((a.get("current_task", "") for a in agents if a["name"] == pub_name), "")
                    if "Skipped" not in task and self._agent_run_count(pub_name, agents) < 3:
                        to_start.append(pub_name)

        # Indexation Accelerator: start AFTER at least one publisher is done
        if self._any_done(_PUBLISHERS, agents):
            ia_status = self._agent_status("Indexation Accelerator", agents)
            if ia_status == "idle" and self._agent_run_count("Indexation Accelerator", agents) == 0:
                to_start.append("Indexation Accelerator")

        # ── Layer 4B: Content Freshness ────────────────────────────────────
        if pg_pub >= 1:
            cf_status = self._agent_status("Content Freshness", agents)
            if cf_status == "idle":
                to_start.append("Content Freshness")

        # ── Layer 6: Analytics ─────────────────────────────────────────────
        if pg_pub >= 1 or publisher_ran:
            l6_idle = self._idle_in_layer(LAYER_6, agents)
            for name in l6_idle:
                # Only start analytics once
                rc = self._agent_run_count(name, agents)
                if rc == 0:
                    to_start.append(name)

        # ── Quality gate: re-run Content Optimizer if needed ───────────────
        if unscored > 0 and self._agent_status("Content Optimizer", agents) == "idle":
            co_rc = self._agent_run_count("Content Optimizer", agents)
            if co_rc < 5:
                to_start.append("Content Optimizer")

        # Log the decision
        if to_start:
            self.log(f"[Director] This cycle: starting {to_start}", "info")
            self.log(
                f"[Director] State: kw={kw_total} comp={comp_count} "
                f"pages(draft={pg_draft} ready={pg_ready} pub={pg_pub}) "
                f"quality(avg={avg_score} unscored={unscored})",
                "info"
            )

        return to_start

    # ── Pipeline complete detection ──────────────────────────────────────────

    def _is_pipeline_complete(self, state: dict) -> bool:
        """Determine if the pipeline has reached a natural end state."""
        agents = state["agents"]
        pg_ready = state["pages"]["ready"]
        pg_pub = state["pages"]["published"]
        avg_score = state["quality"].get("avg_score", 0) or 0

        quality_ok = avg_score >= 70
        co_exhausted = self._agent_run_count("Content Optimizer", agents) >= 5

        publisher_done = False
        publisher_skipped = False
        for pub_name in LAYER_5:
            st = self._agent_status(pub_name, agents)
            task = next((a.get("current_task", "") for a in agents if a["name"] == pub_name), "")
            if st == "done":
                if "Skipped" in task:
                    publisher_skipped = True
                else:
                    publisher_done = True

        l6_all_ran = all(
            self._agent_run_count(n, agents) > 0
            for n in LAYER_6
        )

        # Case A: real publishing succeeded
        if publisher_done and pg_pub >= 10 and (quality_ok or co_exhausted):
            return True

        # Case B: publishers skipped but everything else is done
        if publisher_skipped and pg_ready >= 10 and (quality_ok or co_exhausted) and l6_all_ran:
            if not self._publisher_skip_warned:
                self.log(
                    "[Director] Publishers skipped (no credentials configured). "
                    "Pipeline did everything it could. Configure publisher settings to go live.",
                    "warning"
                )
                self._publisher_skip_warned = True
            return True

        return False

    # ── Deadlock Detection ────────────────────────────────────────────────────

    def _check_deadlock(self, agents: list) -> str | None:
        """Returns reason string if pipeline is deadlocked, None if healthy."""
        active = [a for a in agents if a["status"] not in ("done", "idle")]
        if not active:
            return None

        min_layer = min(a["layer"] for a in active if a["layer"] is not None)
        layer_agents = [a for a in active if a["layer"] == min_layer]

        errored = [a for a in layer_agents if a["status"] == "error" and (a.get("run_count") or 0) >= 3]
        if errored and len(errored) == len(layer_agents):
            names = ", ".join(a["name"] for a in errored)
            return f"Layer {min_layer} deadlock: all agents failed ≥3 times — {names}. Manual intervention required."
        return None

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def _watchdog(self):
        """Reset agents that have been stuck in 'working' for >10 minutes."""
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

        while not self.should_stop() and not self._stop_flag and cycles < MAX_CYCLES:
            cycles += 1
            try:
                self._watchdog()
                self._reset_blocked_publishers()

                # Quality gate: run after publisher is done
                pub_rows = db_execute(
                    "SELECT status, run_count FROM agents WHERE tenant_id=? AND name='GitHub Publisher'",
                    (self.ctx.tenant_id,)
                )
                if pub_rows and pub_rows[0]["status"] == "done" and pub_rows[0]["run_count"] > 0:
                    self._run_quality_gate()

                state = self._assess_state()

                # Deadlock guard
                deadlock_reason = self._check_deadlock(state.get("agents", []))
                if deadlock_reason:
                    self.log(f"[Director] {deadlock_reason}", "error")
                    self.set_status("error", deadlock_reason[:200])
                    break

                # Check pipeline complete
                if self._is_pipeline_complete(state):
                    self.log("[Director] Pipeline complete — all layers executed successfully.", "success")
                    self.set_status("done", "Pipeline complete")
                    break

                # Decide and execute
                to_start = self._evaluate(state)
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

                if not to_start:
                    self.log(f"[Director] No agents to start this cycle — waiting.", "info")

                # Wait 30s, polling for stop signal
                for _ in range(60):
                    if self.should_stop() or self._stop_flag:
                        break
                    time.sleep(0.5)

            except Exception as exc:
                self.log(f"[Director] Cycle error: {exc}", "error")
                for _ in range(60):
                    if self.should_stop() or self._stop_flag:
                        break
                    time.sleep(0.5)

        if not self._stop_flag:
            if cycles >= MAX_CYCLES:
                # Ran out of cycles — pipeline not complete, save progress for resume
                self.save_output("pipeline_progress", {"cycles_completed": cycles, "last_state": self._assess_state()})
                self.set_status("idle", f"Paused after {cycles} cycles — click Run Pipeline to continue")
                self.log(f"[Director] Paused after {MAX_CYCLES} cycles. Progress saved. Restart to continue.", "warning")
            else:
                self.set_status("done", "Pipeline complete — all layers executed")
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
        elif status == "prereq_failed":
            self.log(f"[Director] {agent_name} prerequisites not met: {result.get('message')}", "warning")
        else:
            self.log(f"[Director] Failed to start {agent_name}: {result.get('message')}", "error")

    def _stop_agent(self, agent_name: str):
        """Signal an agent to stop via the unified AgentRunner."""
        from denzo.agents.runner import AgentRunner

        AgentRunner.stop(self.ctx.tenant_id, agent_name)
        self.log(f"[Director] Stopped {agent_name}", "warning")
