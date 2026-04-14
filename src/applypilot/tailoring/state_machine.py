"""Smart tailoring state machine: iterative resume optimization with quality gates."""

import logging
from typing import Any, Dict, List, Optional

from transitions import Machine

from applypilot.intelligence import JobDescriptionParser, ResumeMatcher
from applypilot.llm import get_client
from applypilot.tailoring.bullet_bank import BulletBank
from applypilot.tailoring.models import BulletVariant, GateResult, Resume, TailoringResult
from applypilot.tailoring.quality_gates import MetricsGate, RelevanceGate

logger = logging.getLogger(__name__)


class SmartTailoringEngine:
    """9-state machine for iterative resume tailoring with quality gates.

    States: ANALYZE → EXTRACT → GENERATE → SCORE → SELECT → VALIDATE → JUDGE → ASSEMBLE → LEARN

    The JUDGE state can loop back to GENERATE if quality gates fail,
    enabling iterative improvement up to max_iterations.
    """

    states = [
        "ANALYZE",
        "EXTRACT",
        "GENERATE",
        "SCORE",
        "SELECT",
        "VALIDATE",
        "JUDGE",
        "ASSEMBLE",
        "LEARN",
    ]

    transitions = [
        {"trigger": "analyze_complete", "source": "ANALYZE", "dest": "EXTRACT"},
        {"trigger": "extract_complete", "source": "EXTRACT", "dest": "GENERATE"},
        {"trigger": "generate_complete", "source": "GENERATE", "dest": "SCORE"},
        {"trigger": "score_complete", "source": "SCORE", "dest": "SELECT"},
        {"trigger": "select_complete", "source": "SELECT", "dest": "VALIDATE"},
        {"trigger": "validation_passed", "source": "VALIDATE", "dest": "JUDGE"},
        {"trigger": "validation_failed", "source": "VALIDATE", "dest": "GENERATE"},
        {"trigger": "judge_passed", "source": "JUDGE", "dest": "ASSEMBLE"},
        {"trigger": "judge_failed", "source": "JUDGE", "dest": "GENERATE"},
        {"trigger": "assembly_complete", "source": "ASSEMBLE", "dest": "LEARN"},
        {"trigger": "learning_complete", "source": "LEARN", "dest": "ANALYZE"},
    ]

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.client = get_client()
        self.bullet_bank = self._make_bullet_bank(config)
        self.quality_gates: List[Any] = [MetricsGate(), RelevanceGate()]

        self.machine = Machine(
            model=self,
            states=SmartTailoringEngine.states,
            transitions=SmartTailoringEngine.transitions,
            initial="ANALYZE",
        )

        # Runtime state
        self.current_job: Optional[Dict[str, Any]] = None
        self.current_resume: Optional[Resume] = None
        self.job_intel: Optional[Any] = None
        self.match_analysis: Optional[Any] = None
        self.generated_variants: List[BulletVariant] = []
        self.selected_bullets: List[BulletVariant] = []
        self.quality_results: List[GateResult] = []
        self.final_resume: Optional[Resume] = None
        self.final_score: float = 0.0
        self._done: bool = False

    # -- Step-based state handlers --------------------------------------------
    # Each handler performs work for its state and fires the appropriate trigger
    # to advance the machine. The run() loop calls step() once per iteration.

    @staticmethod
    def _make_bullet_bank(config: Dict[str, Any]) -> BulletBank:
        """Create BulletBank from DI container or fallback to path-based repo."""
        repo = config.get("bullet_bank_repo")
        if repo is not None:
            return BulletBank(repo)
        # Fallback: create a repo from a db path
        from applypilot.db.sqlite.connection import get_connection
        from applypilot.db.sqlite.bullet_bank_repo import SqliteBulletBankRepository
        from applypilot.db.schema import init_db

        path = config.get("bullet_bank_path", "")
        conn = get_connection(path) if path else get_connection()
        init_db(conn)
        return BulletBank(SqliteBulletBankRepository(conn))

    def step(self) -> None:
        """Execute the current state's logic and transition to the next state."""
        handler = {
            "ANALYZE": self._do_analyze,
            "EXTRACT": self._do_extract,
            "GENERATE": self._do_generate,
            "SCORE": self._do_score,
            "SELECT": self._do_select,
            "VALIDATE": self._do_validate,
            "JUDGE": self._do_judge,
            "ASSEMBLE": self._do_assemble,
            "LEARN": self._do_learn,
        }.get(self.state)

        if handler:
            handler()

    def _do_analyze(self) -> None:
        """Parse the job description and analyze resume match."""
        logger.info("State: ANALYZE — parsing job and analyzing match")
        parser = JobDescriptionParser()
        matcher = ResumeMatcher()
        self.job_intel = parser.parse(self.current_job)
        self.match_analysis = matcher.analyze(self.current_resume.text, self.job_intel)
        logger.info("Match score: %.1f", self.match_analysis.overall_score)
        self.analyze_complete()

    def _do_extract(self) -> None:
        """Extract existing bullets from the resume into the bullet bank."""
        logger.info("State: EXTRACT — extracting bullets from resume")
        achievements = self._extract_achievements(self.current_resume)
        for achievement in achievements:
            self.bullet_bank.add_bullet(
                text=achievement,
                context={"job_title": self.job_intel.title if self.job_intel else ""},
                tags=["original"],
                metrics=[],
            )
        logger.info("Extracted %d achievements", len(achievements))
        self.extract_complete()

    def _do_generate(self) -> None:
        """Generate tailored bullet variants using LLM."""
        logger.info("State: GENERATE — creating tailored bullet variants")
        achievements = self._extract_achievements(self.current_resume)
        self.generated_variants = []

        for achievement in achievements:
            variant_text = self._generate_variant(achievement)
            self.generated_variants.append(
                BulletVariant(
                    original_bullet_id="",
                    text=variant_text,
                    strategy="quantify_impact",
                )
            )

        logger.info("Generated %d variants", len(self.generated_variants))
        self.generate_complete()

    def _do_score(self) -> None:
        """Score each generated variant for quality."""
        logger.info("State: SCORE — scoring %d variants", len(self.generated_variants))
        self._score_variants()
        self.score_complete()

    def _do_select(self) -> None:
        """Select the best-scoring variants."""
        logger.info("State: SELECT — picking top variants")
        self._select_best_variants()
        logger.info("Selected %d bullets", len(self.selected_bullets))
        self.select_complete()

    def _do_validate(self) -> None:
        """Run quality gates on assembled resume."""
        logger.info("State: VALIDATE — running quality gates")
        resume = self._assemble_resume()
        self.quality_results = []
        all_passed = True

        for gate in self.quality_gates:
            result = gate.check(resume, {"job_intelligence": self.job_intel})
            self.quality_results.append(result)
            if not result.passed:
                all_passed = False
                logger.warning("Gate failed: %s", result.feedback)

        if all_passed:
            logger.info("All quality gates passed")
            self.validation_passed()
        else:
            logger.info("Quality gates failed — looping back to GENERATE")
            self.validation_failed()

    def _do_judge(self) -> None:
        """Final judgment: approve or send back for revision."""
        logger.info("State: JUDGE — final quality check")
        avg_score = (
            sum(r.score for r in self.quality_results) / len(self.quality_results) if self.quality_results else 0.0
        )

        if avg_score >= 0.6:
            logger.info("Judge approved (avg score: %.2f)", avg_score)
            self.judge_passed()
        else:
            logger.info("Judge rejected (avg score: %.2f) — retrying", avg_score)
            self.judge_failed()

    def _do_assemble(self) -> None:
        """Assemble the final tailored resume."""
        logger.info("State: ASSEMBLE — building final resume")
        self.final_resume = self._assemble_resume()
        self.final_score = (
            sum(r.score for r in self.quality_results) / len(self.quality_results) if self.quality_results else 0.0
        )
        self.assembly_complete()

    def _do_learn(self) -> None:
        """Record outcomes for future improvement."""
        logger.info("State: LEARN — recording feedback")
        for variant in self.selected_bullets:
            if variant.original_bullet_id:
                self.bullet_bank.record_feedback(
                    variant.original_bullet_id,
                    self.job_intel.title if self.job_intel else "",
                    "selected",
                )
        self._done = True

    # -- Orchestrator ---------------------------------------------------------

    def run(self, job: Dict[str, Any], resume: Resume) -> TailoringResult:
        """Execute the full tailoring pipeline for one job/resume pair.

        Returns a TailoringResult with the optimized resume and quality metrics.
        """
        self.current_job = job
        self.current_resume = resume
        self._done = False

        max_iterations = self.config.get("max_iterations", 3)
        iteration = 0

        # Step through states until LEARN is reached or max_iterations exceeded.
        # Each step() call processes the current state and transitions to the next.
        while not self._done and iteration < max_iterations:
            logger.info("Iteration %d, state: %s", iteration + 1, self.state)
            self.step()
            iteration += 1

        if self.final_resume is None:
            # Safety fallback: return original resume if pipeline didn't complete
            self.final_resume = resume

        return TailoringResult(
            resume=self.final_resume,
            score=self.final_score,
            iterations=iteration,
            quality_results=self.quality_results,
        )

    # -- Helpers --------------------------------------------------------------

    def _extract_achievements(self, resume: Optional[Resume]) -> list:
        """Pull bullet points from resume EXPERIENCE section."""
        if resume is None:
            return []
        achievements = []
        if "EXPERIENCE" in resume.sections:
            exp = resume.sections["EXPERIENCE"]
            if isinstance(exp, dict) and "bullets" in exp:
                achievements.extend(exp["bullets"])
            elif isinstance(exp, list):
                achievements.extend(exp)
        return achievements

    def _generate_variant(self, achievement: str) -> str:
        """Use LLM to rewrite a bullet emphasizing metrics."""
        job_title = self.job_intel.title if self.job_intel else "the target role"
        prompt = (
            f"Rewrite this resume bullet to emphasize quantifiable metrics "
            f"and impact. Keep it to one concise line.\n\n"
            f"Original: {achievement}\n"
            f"Target Job: {job_title}\n\n"
            f"Rewritten bullet:"
        )
        return self.client.ask(prompt, temperature=0.7)

    def _score_variants(self) -> None:
        """Assign quality scores to each generated variant."""
        for variant in self.generated_variants:
            # Simple heuristic: has metrics => higher score
            has_numbers = any(c.isdigit() for c in variant.text)
            length_ok = 50 <= len(variant.text) <= 200
            variant.score = (0.5 if has_numbers else 0.2) + (0.3 if length_ok else 0.1)

    def _select_best_variants(self) -> None:
        """Pick top-scoring variants."""
        max_bullets = self.config.get("max_bullets", 5)
        self.selected_bullets = sorted(
            self.generated_variants,
            key=lambda v: v.score or 0,
            reverse=True,
        )[:max_bullets]

    def _assemble_resume(self) -> Resume:
        """Build a Resume from selected bullets."""
        sections: Dict[str, Any] = {}
        if self.selected_bullets:
            sections["EXPERIENCE"] = {"bullets": [b.text for b in self.selected_bullets]}

        # Preserve non-experience sections from original resume
        if self.current_resume:
            for key, val in self.current_resume.sections.items():
                if key != "EXPERIENCE":
                    sections[key] = val

        text = "\n".join(b.text for b in self.selected_bullets) if self.selected_bullets else ""
        return Resume(text=text, sections=sections)
