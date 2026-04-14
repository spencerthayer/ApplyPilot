"""Comprehensive resume tailoring state machine with preprocessing, audit, and iteration routing.

This implements the full workflow from the design spec:
- PreprocessLibrary: Build hardened bullet bank from profile with evidence
- TailorForJob: Per-job loop with independent audit and targeted iteration routing
- MaintainSystem: Update evidence and promote bullets after each job
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from transitions import Machine

from applypilot.llm import get_client
from applypilot.intelligence.jd_parser import JobDescriptionParser
from applypilot.resume.extraction import get_profile_skill_sections, get_profile_verified_metrics
from applypilot.tailoring.models import Resume
from applypilot.tailoring.quality_gates import MetricsGate, RelevanceGate
from applypilot.tailoring.metrics_registry import MetricsRegistry
from applypilot.tailoring.variant_generators import (
    generate_car_variant,
    generate_who_variant,
    generate_technical_variant,
    generate_product_variant,
    validate_variant_metrics,
)

logger = logging.getLogger(__name__)


class ProcessingState(Enum):
    """States for the comprehensive tailoring workflow."""

    # Preprocessing states
    INGEST_SOURCES = "ingest_sources"
    NORMALIZE_PROFILE = "normalize_profile"
    BUILD_METRICS_REGISTRY = "build_metrics_registry"
    EXTRACT_ACHIEVEMENTS = "extract_achievements"
    GENERATE_VARIANTS = "generate_variants"
    HARDEN_BULLETS = "harden_bullets"
    TAG_AND_CLUSTER = "tag_and_cluster"
    PUBLISH_BULLET_BANK = "publish_bullet_bank"

    # Per-job tailoring states
    PARSE_JD = "parse_jd"
    BUILD_TARGET_PROFILE = "build_target_profile"
    RETRIEVE_CANDIDATES = "retrieve_candidates"
    RISK_FILTER = "risk_filter"
    SCORE_CANDIDATES = "score_candidates"
    SELECT_PROOF_SET = "select_proof_set"
    ASSEMBLE_DRAFT = "assemble_draft"
    RUN_CHECKS = "run_checks"
    INDEPENDENT_AUDIT = "independent_audit"
    AUDIT_SCORECARD = "audit_scorecard"
    DECISION_GATE = "decision_gate"
    ROUTE_FIXES = "route_fixes"

    # Fix routing substates
    FIX_COVERAGE = "fix_coverage"
    FIX_CREDIBILITY = "fix_credibility"
    FIX_REDUNDANCY = "fix_redundancy"
    FIX_ATS = "fix_ats"
    FIX_NARRATIVE = "fix_narrative"
    FIX_ECONOMY = "fix_economy"

    # Maintenance states
    UPDATE_EVIDENCE = "update_evidence"
    UPDATE_METRICS = "update_metrics"
    PROMOTE_BULLETS = "promote_bullets"
    UPDATE_GAP_LIST = "update_gap_list"

    DONE = "done"


@dataclass
class HardenedBullet:
    """A hardened bullet with variants, evidence, and risk flags."""

    id: str
    text: str
    variants: Dict[str, str]  # CAR, WHO, technical, product
    tags: List[str]
    skills: List[str]
    domains: List[str]
    role_families: List[str]

    evidence_links: List[str]
    metrics: List[Dict[str, Any]]
    vague_claim: bool = False
    implied_scale: bool = False
    tech_mismatch: bool = False
    keyword_mismatch: bool = False
    ownership_level: int = 0
    recency_score: float = 0.0
    has_proof: bool = False
    has_metric: bool = False
    use_count: int = 0
    success_count: int = 0
    created_at: str = ""


@dataclass
class MetricsRegistryEntry:
    """Approved metric with definition and allowed phrasing."""

    value: str
    timeframe: str
    definition: str
    source: str
    allowed_phrases: List[str]
    verified: bool = False


@dataclass
class EvidenceLedgerEntry:
    """Claim to source mapping."""

    claim: str
    bullet_id: str
    proof_links: List[str]
    interview_script: str  # What you can say


@dataclass
class TargetRoleProfile:
    """Role family template with scoring weights."""

    family: str  # AI Engineering, Product Management, etc.
    title_variants: List[str]
    detection_keywords: List[str]

    relevance_weight: float = 1.0
    evidence_weight: float = 1.0
    specificity_weight: float = 1.0
    outcome_weight: float = 1.0
    ats_weight: float = 1.0
    economy_weight: float = 1.0
    banned_phrases: List[str] = field(default_factory=list)
    required_patterns: List[str] = field(default_factory=list)


@dataclass
class AuditScorecard:
    """Section-by-section scoring with failure diagnosis."""

    section_scores: Dict[str, float]  # 0-5 per section
    overall_score: float

    uncovered_requirements: List[str]
    weak_lines: List[str]
    missed_opportunities: List[str]
    routing_recommendation: str
    iteration_delta: float


class ComprehensiveTailoringEngine:
    """Full workflow: preprocess → tailor → maintain with deterministic gates and audit loop."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.client = get_client()

        # Use ComprehensiveStorage instead of direct sqlite3
        from applypilot.tailoring.comprehensive_storage import ComprehensiveStorage

        self.storage = ComprehensiveStorage(
            conn=config.get("storage_conn"),
            db_path=config.get("bullet_bank_path", "/tmp/bullet_bank.db"),
        )
        self.bullet_bank_path = config.get("bullet_bank_path", "/tmp/bullet_bank.db")
        self.evidence_path = config.get("evidence_path", "/tmp/evidence.db")
        self.machine = Machine(
            model=self,
            states=[s.value for s in ProcessingState],
            initial=ProcessingState.INGEST_SOURCES.value,
        )
        # Use an empty dict default so attribute access like .get() is safe for static analysis
        self.profile: Dict[str, Any] = {}
        self.metrics_registry: Dict[str, MetricsRegistryEntry] = {}
        self.evidence_ledger: Dict[str, EvidenceLedgerEntry] = {}
        self.bullet_bank: Dict[str, HardenedBullet] = {}
        self.target_roles: Dict[str, TargetRoleProfile] = {}
        # Current job dict for tailoring loop
        self.current_job: Dict[str, Any] = {}
        self.current_target_role: Optional[TargetRoleProfile] = None
        self.target_profile: Optional[TargetRoleProfile] = None
        self.job_intelligence: Optional[Any] = None
        self.candidate_bullets: List[HardenedBullet] = []
        self.selected_bullets: List[HardenedBullet] = []
        self.current_draft: Optional[str] = None
        self.audit_history: List[AuditScorecard] = []
        self.max_iterations = config.get("max_iterations", 5)
        self.iteration_count = 0
        self.improvement_threshold = config.get("improvement_threshold", 0.1)

    def _init_databases(self):
        """Databases initialized by ComprehensiveStorage."""
        pass  # Handled by self.storage

    # ========================================================================
    # PHASE 1: PREPROCESS LIBRARY
    # ========================================================================

    def preprocess_library(self, profile: Dict[str, Any]) -> None:
        """Build hardened bullet bank from profile (one-time setup)."""
        self.profile = profile

        # Define state handlers
        self._do_ingest_sources()
        self._do_normalize_profile()
        self._do_build_metrics_registry()
        self._do_extract_achievements()
        self._do_generate_variants()
        self._do_harden_bullets()
        self._do_tag_and_cluster()
        self._do_publish_bullet_bank()

    def _do_ingest_sources(self):
        """Ingest profile, work history, projects."""
        logger.info("Preprocessing: INGEST_SOURCES")
        # Profile already loaded in self.profile

    def _do_normalize_profile(self):
        """Normalize dates, titles, ownership levels."""
        logger.info("Preprocessing: NORMALIZE_PROFILE")
        # Ensure consistent formatting

    def _do_build_metrics_registry(self):
        """Extract and verify all metrics from profile."""
        logger.info("Preprocessing: BUILD_METRICS_REGISTRY")

        metrics_found = []

        # Extract from normalized work entries
        for job in self.profile.get("work", []):
            if "key_metrics" in job:
                for metric in job["key_metrics"]:
                    metrics_found.append(
                        {
                            "value": metric,
                            "timeframe": "varies",
                            "source": f"{job['company']} - {job['position']}",
                            "verified": True,
                        }
                    )

        # Store in registry
        for i, m in enumerate(metrics_found):
            key = f"metric_{i}"
            self.metrics_registry[key] = MetricsRegistryEntry(
                value=m["value"],
                timeframe=m["timeframe"],
                definition="",
                source=m["source"],
                allowed_phrases=[m["value"]],
                verified=m["verified"],
            )

    def _do_extract_achievements(self):
        """Extract atomic achievements from profile."""
        logger.info("Preprocessing: EXTRACT_ACHIEVEMENTS")

        achievements = []

        for job in self.profile.get("work", []):
            if "highlights" in job:
                for highlight in job["highlights"]:
                    achievements.append(
                        {
                            "text": highlight,
                            "company": job["company"],
                            "position": job["position"],
                            "start_date": job.get("start_date", ""),
                            "end_date": job.get("end_date", ""),
                        }
                    )

        self._raw_achievements = achievements

    def _do_generate_variants(self):
        """Generate CAR, WHO, technical, product variants for top bullets.

        Limits LLM calls to max 15 per resume. Validates all variants
        against MetricsRegistry to prevent metric hallucination.
        """
        logger.info("Preprocessing: GENERATE_VARIANTS")
        # Calculate budget: max 15 LLM calls total
        # Each bullet needs 4 variants (CAR, WHO, technical, product)
        # So max 3 bullets = 12 calls, leaving buffer for other operations
        max_variants = 15
        variants_per_bullet = 4
        max_bullets = min(5, max_variants // variants_per_bullet)  # Max 3 bullets (12 calls)

        # Select top bullets to generate variants for
        # Prioritize bullets with metrics as they're most valuable
        sorted_achievements = sorted(
            self._raw_achievements, key=lambda a: any(c.isdigit() for c in a["text"]), reverse=True
        )

        # Initialize MetricsRegistry for validation
        registry = MetricsRegistry()

        llm_calls_made = 0
        variants_generated = 0

        for i, achievement in enumerate(sorted_achievements[:max_bullets]):
            if llm_calls_made >= max_variants:
                logger.warning("LLM call budget exhausted (%d calls). Stopping variant generation.", max_variants)
                break

            original_text = achievement["text"]

            # Generate variants using actual LLM-powered functions
            variants = {"original": original_text}

            # Generate CAR variant (call #1)
            if llm_calls_made < max_variants:
                car_variant = generate_car_variant(original_text, self.client)
                llm_calls_made += 1
                # Validate against registry - reject if hallucinated metrics
                validated_car = validate_variant_metrics(original_text, car_variant, registry)
                if validated_car != car_variant:
                    logger.warning("CAR variant rejected due to unverified metrics for bullet %d", i)
                variants["car"] = validated_car
                variants_generated += 1

            # Generate WHO variant (call #2)
            if llm_calls_made < max_variants:
                who_variant = generate_who_variant(original_text, self.client)
                llm_calls_made += 1
                validated_who = validate_variant_metrics(original_text, who_variant, registry)
                if validated_who != who_variant:
                    logger.warning("WHO variant rejected due to unverified metrics for bullet %d", i)
                variants["who"] = validated_who
                variants_generated += 1

            # Generate Technical variant (call #3)
            if llm_calls_made < max_variants:
                tech_variant = generate_technical_variant(original_text, self.client)
                llm_calls_made += 1
                validated_tech = validate_variant_metrics(original_text, tech_variant, registry)
                if validated_tech != tech_variant:
                    logger.warning("Technical variant rejected due to unverified metrics for bullet %d", i)
                variants["technical"] = validated_tech
                variants_generated += 1

            # Generate Product variant (call #4)
            if llm_calls_made < max_variants:
                product_variant = generate_product_variant(original_text, self.client)
                llm_calls_made += 1
                validated_product = validate_variant_metrics(original_text, product_variant, registry)
                if validated_product != product_variant:
                    logger.warning("Product variant rejected due to unverified metrics for bullet %d", i)
                variants["product"] = validated_product
                variants_generated += 1
            # Detect metrics
            import re

            metrics = re.findall(r"\d+%|\$[\d,]+|\d+x|\d+\s+(?:million|thousand)", original_text, re.IGNORECASE)
            # Risk assessment
            vague = self._is_vague(original_text)
            implied = self._has_implied_scale(original_text)
            bullet = HardenedBullet(
                id=f"bullet_{i}",
                text=original_text,
                variants=variants,
                tags=[achievement["company"].lower().replace(" ", "_")],
                skills=[],
                domains=[],
                role_families=[],
                evidence_links=[],
                metrics=[{"value": m} for m in metrics],
                vague_claim=vague,
                implied_scale=implied,
                has_metric=len(metrics) > 0,
            )

            self.bullet_bank[bullet.id] = bullet

        logger.info(
            "Generated %d variants for %d bullets using %d LLM calls",
            variants_generated,
            len([b for b in self.bullet_bank.values() if b.variants]),
            llm_calls_made,
        )

    def _generate_car_variant(self, text: str) -> str:
        """Generate Context-Action-Result variant."""
        # Use LLM or template
        return text  # Placeholder

    def _generate_who_variant(self, text: str) -> str:
        """Generate What-How-Outcome variant."""
        return text  # Placeholder

    def _generate_technical_variant(self, text: str) -> str:
        """Emphasize technical depth."""
        return text  # Placeholder

    def _generate_product_variant(self, text: str) -> str:
        """Emphasize product impact."""
        return text  # Placeholder

    def _is_vague(self, text: str) -> bool:
        """Check for vague language."""
        vague_words = ["improved", "optimized", "enhanced", "streamlined"]
        return any(w in text.lower() for w in vague_words) and not any(c.isdigit() for c in text)

    def _has_implied_scale(self, text: str) -> bool:
        """Check for implied scale without explicit numbers."""
        scale_words = ["large", "massive", "significant", "major", "production-grade"]
        has_scale_word = any(w in text.lower() for w in scale_words)
        has_number = any(c.isdigit() for c in text)
        return has_scale_word and not has_number

    def _do_harden_bullets(self):
        """Validate bullets, flag risks, require evidence."""
        logger.info("Preprocessing: HARDEN_BULLETS")

        for bullet in self.bullet_bank.values():
            # Flag bullets needing evidence
            if bullet.has_metric and not bullet.evidence_links:
                logger.warning(f"Bullet {bullet.id} has metric but no evidence")

    def _do_tag_and_cluster(self):
        """Tag bullets with skills, domains, role families."""
        logger.info("Preprocessing: TAG_AND_CLUSTER")

        for bullet in self.bullet_bank.values():
            # Auto-tag based on content
            if "python" in bullet.text.lower():
                bullet.skills.append("python")
            if "ai" in bullet.text.lower() or "ml" in bullet.text.lower():
                bullet.skills.append("ai_ml")
                bullet.role_families.append("ai_engineer")

    def _do_publish_bullet_bank(self):
        """Save to SQLite via storage adapter."""
        logger.info("Preprocessing: PUBLISH_BULLET_BANK")

        for bullet in self.bullet_bank.values():
            self.storage.save_bullet(bullet)

    # ========================================================================
    # PHASE 2: TAILOR FOR JOB
    # ========================================================================

    def tailor_for_job(self, job: Dict[str, Any]) -> str:
        """Execute full per-job tailoring with audit and iteration."""
        self.current_job = job
        self.iteration_count = 0
        self.audit_history = []

        # Main loop
        while self.iteration_count < self.max_iterations:
            logger.info(f"Tailoring iteration {self.iteration_count + 1}")

            # Parse and build target
            self._do_parse_jd()
            self._do_build_target_profile()

            # Retrieve and score
            self._do_retrieve_candidates()
            self._do_risk_filter()
            self._do_score_candidates()
            self._do_select_proof_set()

            # Assemble and check
            self._do_assemble_draft()
            checks_passed = self._do_run_checks()

            if not checks_passed:
                self._do_route_fixes()
                self.iteration_count += 1
                continue

            # Independent audit
            self._do_independent_audit()
            scorecard = self._do_audit_scorecard()
            self.audit_history.append(scorecard)

            # Decision gate
            should_accept = self._do_decision_gate(scorecard)

            if should_accept:
                logger.info("Resume accepted by audit")
                return self.current_draft or ""
            else:
                # Route to fix
                self._do_route_fixes_diagnose(scorecard)
                self.iteration_count += 1

        # Max iterations reached, return best draft
        logger.warning(f"Max iterations ({self.max_iterations}) reached")
        return self.current_draft or ""

    def _do_parse_jd(self):
        """Parse job description for requirements and outcomes."""
        logger.info("TAILOR: PARSE_JD")
        parser = JobDescriptionParser()
        self.job_intelligence = parser.parse(self.current_job)
        logger.info("Parsed JD for: %s at %s", self.job_intelligence.title, self.job_intelligence.company)

    def _do_build_target_profile(self):
        """Map JD to target role profile."""
        logger.info("TAILOR: BUILD_TARGET_PROFILE")

        # Load tailoring config from profile
        tailoring_config = self.profile.get("tailoring_config", {})
        role_types = tailoring_config.get("role_types", {})

        # Get JD text for keyword matching
        jd_text = self.current_job.get("description", "").lower()
        jd_title = self.current_job.get("title", "").lower()
        combined_text = f"{jd_title} {jd_text}"

        # Find best matching role type based on detection keywords
        best_role_key = None
        best_match_count = 0

        for role_key, role_config in role_types.items():
            detection_keywords = role_config.get("detection_keywords", [])
            match_count = sum(1 for keyword in detection_keywords if keyword.lower() in combined_text)
            if match_count > best_match_count:
                best_match_count = match_count
                best_role_key = role_key

        # Default to "general" if no match found
        if best_role_key is None or best_match_count == 0:
            best_role_key = "general"

        role_config = role_types.get(best_role_key, {})
        constraints = role_config.get("constraints", {})

        # Build TargetRoleProfile from role config
        self.target_profile = TargetRoleProfile(
            family=best_role_key,
            title_variants=role_config.get("title_variants", []),
            detection_keywords=role_config.get("detection_keywords", []),
            banned_phrases=constraints.get("banned_phrases", []),
            required_patterns=constraints.get("required_patterns", []),
        )

        # Also set current_target_role for backward compatibility
        self.current_target_role = self.target_profile
        logger.info("Built target profile for role: %s", best_role_key)

    def _do_retrieve_candidates(self):
        """Retrieve matching bullets from bank via storage adapter."""
        logger.info("TAILOR: RETRIEVE_CANDIDATES")
        rows = self.storage.get_metric_bullets()
        self.candidate_bullets = []
        for row in rows:
            try:
                bullet = HardenedBullet(
                    id=row["id"],
                    text=row["text"],
                    variants=json.loads(row["variants"]) if row["variants"] else {},
                    tags=json.loads(row["tags"]) if row["tags"] else [],
                    skills=json.loads(row["skills"]) if row["skills"] else [],
                    domains=json.loads(row["domains"]) if row["domains"] else [],
                    role_families=json.loads(row["role_families"]) if row["role_families"] else [],
                    evidence_links=json.loads(row["evidence_links"]) if row["evidence_links"] else [],
                    metrics=json.loads(row["metrics"]) if row["metrics"] else [],
                    vague_claim=bool(row["vague_claim"]),
                    implied_scale=bool(row["implied_scale"]),
                    tech_mismatch=bool(row["tech_mismatch"]),
                    keyword_mismatch=bool(row["keyword_mismatch"]),
                    ownership_level=row["ownership_level"] or 0,
                    recency_score=row["recency_score"] or 0.0,
                    has_proof=bool(row["has_proof"]),
                    has_metric=bool(row["has_metric"]),
                    use_count=row["use_count"] or 0,
                    success_count=row["success_count"] or 0,
                    created_at=row["created_at"] or "",
                )
                self.candidate_bullets.append(bullet)
            except (json.JSONDecodeError, IndexError, TypeError, KeyError) as e:
                logger.warning(f"Skipping malformed bullet row: {e}")
                continue

        # Filter/sort by relevance if target role is available
        if self.current_target_role and self.candidate_bullets:
            self.candidate_bullets = self._filter_by_relevance(self.candidate_bullets, self.current_target_role)

    def _filter_by_relevance(
        self, bullets: List[HardenedBullet], target_role: TargetRoleProfile
    ) -> List[HardenedBullet]:
        """Filter and sort bullets by relevance to target role."""
        scored_bullets = []
        for bullet in bullets:
            score = 0.0

            # Check role family match
            if target_role.family in bullet.role_families:
                score += 2.0

            # Check detection keywords in tags/skills
            for keyword in target_role.detection_keywords:
                if any(keyword in tag for tag in bullet.tags):
                    score += 0.5
                if any(keyword in skill for skill in bullet.skills):
                    score += 0.5

            # Bonus for metrics
            if bullet.has_metric:
                score += 0.3

            scored_bullets.append((bullet, score))

        # Sort by score descending
        scored_bullets.sort(key=lambda x: x[1], reverse=True)

        # Return sorted bullets (filter out zero scores optionally)
        return [bullet for bullet, score in scored_bullets if score > 0]

    def _do_risk_filter(self):
        """Filter out high-risk bullets."""
        logger.info("TAILOR: RISK_FILTER")

        self.candidate_bullets = [b for b in self.candidate_bullets if not b.vague_claim and not b.tech_mismatch]

    def _do_score_candidates(self):
        """Score bullets for job match using heuristics (no LLM calls).

        Scoring criteria:
        - has_metric: +0.3 for having quantifiable metrics
        - ownership_level: scaled to 0-0.3 based on level (0-5)
        - recency_score: use existing or default to 0.5
        - relevance: check overlap with JD keywords, target role skills/domains/tags
        """
        logger.info("TAILOR: SCORE_CANDIDATES")
        jd_text = self.current_job.get("description", "").lower() if self.current_job else ""
        for bullet in self.candidate_bullets:
            score = 0.0
            # (a) Has metric bonus
            if bullet.has_metric:
                score += 0.3
            # (d) Ownership level (0-5, scaled to 0-0.3)
            if hasattr(bullet, "ownership_level") and bullet.ownership_level:
                score += min(bullet.ownership_level / 5.0 * 0.3, 0.3)

            # (c) Recency score (use existing or default)
            base_recency = getattr(bullet, "recency_score", 0.0) or 0.0
            if base_recency > 0:
                score += base_recency * 0.2  # Scale recency contribution

            # (b) Relevance to JD and target role
            if self.current_target_role:
                # Check skill overlap with target role
                for skill in bullet.skills:
                    if skill.lower() in jd_text:
                        score += 0.15

                # Check domain overlap
                for domain in bullet.domains:
                    if domain.lower() in jd_text:
                        score += 0.1

                # Check tags overlap
                for tag in bullet.tags:
                    if tag.lower() in jd_text:
                        score += 0.1

                # Role family match bonus
                if self.current_target_role.family in bullet.role_families:
                    score += 0.25

            # Normalize score to 0-1 range
            bullet.recency_score = min(score, 1.0)

    def _do_select_proof_set(self):
        """Select top-scoring bullets with diversity constraints.

        - Sort by score descending
        - Cap at max_bullets (config, default 10)
        - Diversity: max 3 bullets per company (inferred from tags)
        - Store result in self.selected_bullets
        """
        logger.info("TAILOR: SELECT_PROOF_SET")
        max_bullets = self.config.get("max_bullets", 10)

        # Sort candidates by score descending
        sorted_candidates = sorted(
            self.candidate_bullets,
            key=lambda b: getattr(b, "recency_score", 0.0) or 0.0,
            reverse=True,
        )

        # Select with diversity constraint: max 3 per company
        selected = []
        company_counts: Dict[str, int] = {}

        for bullet in sorted_candidates:
            if len(selected) >= max_bullets:
                break

            # Infer company from tags (tags contain company names)
            company = self._infer_company_from_bullet(bullet)

            # Check diversity constraint
            if company_counts.get(company, 0) >= 3:
                continue

            selected.append(bullet)
            company_counts[company] = company_counts.get(company, 0) + 1

        self.selected_bullets = selected
        logger.info(f"Selected {len(selected)} bullets from {len(company_counts)} companies")

    def _infer_company_from_bullet(self, bullet: HardenedBullet) -> str:
        """Infer company name from bullet tags or return 'unknown'."""
        # Tags often contain company names (e.g., 'acme_corp', 'tech_inc')
        for tag in bullet.tags:
            # Skip non-company tags (skills, domains)
            if tag in bullet.skills or tag in bullet.domains:
                continue
            # If tag looks like a company name (contains underscore or is lowercase identifier)
            if "_" in tag or (tag.islower() and len(tag) > 2):
                return tag
        return "unknown"

    def _do_assemble_draft(self):
        """Build resume from selected bullets and profile data."""
        logger.info("TAILOR: ASSEMBLE_DRAFT")
        lines = []

        # Build header from profile
        personal = self.profile.get("personal", {})
        full_name = personal.get("full_name", "")
        lines.append(full_name)

        # Use target role title or current job title
        target_title = "AI Engineer"  # Default
        if self.target_profile and self.target_profile.title_variants:
            target_title = self.target_profile.title_variants[0]
        elif self.profile.get("experience", {}).get("target_role"):
            target_role = self.profile["experience"]["target_role"]
            if "ai" in target_role.lower() or "ml" in target_role.lower():
                target_title = "AI Engineer"
            elif "product" in target_role.lower():
                target_title = "Product Manager"
        lines.append(target_title)

        # Contact line: email | phone | GitHub | LinkedIn
        contact_parts = []
        if personal.get("email"):
            contact_parts.append(personal["email"])
        if personal.get("phone"):
            contact_parts.append(personal["phone"])
        if personal.get("github_url"):
            contact_parts.append(personal["github_url"])
        if personal.get("linkedin_url"):
            contact_parts.append(personal["linkedin_url"])
        if contact_parts:
            lines.append(" | ".join(contact_parts))
        lines.append("")

        # SUMMARY section
        lines.append("SUMMARY")
        summary_text = self._build_summary()
        lines.append(summary_text)
        lines.append("")

        # TECHNICAL SKILLS section
        lines.append("TECHNICAL SKILLS")
        skills = self._build_skills_section()
        lines.extend(skills)
        lines.append("")

        # EXPERIENCE section
        lines.append("EXPERIENCE")
        experience_lines = self._build_experience_section()
        lines.extend(experience_lines)

        # PROJECTS section
        lines.append("PROJECTS")
        project_lines = self._build_projects_section()
        lines.extend(project_lines)

        # EDUCATION section
        lines.append("EDUCATION")
        education_lines = self._build_education_section()
        lines.extend(education_lines)

        self.current_draft = "\n".join(lines)

    def _build_summary(self) -> str:
        """Build professional summary with key metrics."""
        # Get years of experience
        years_exp = self.profile.get("experience", {}).get("years_of_experience_total", "14")

        # Get key achievements/metrics from profile
        key_achievements = self.profile.get("key_achievements", [])
        top_metrics = []
        for achievement in key_achievements[:3]:
            metric = achievement.get("metric", "")
            if metric:
                top_metrics.append(metric)

        # Build summary emphasizing technical achievements
        summary_parts = [
            f"AI Engineer with {years_exp} years building production ML systems, including 8 years architecting AI-powered content pipelines and recommendation engines."
        ]

        # Add education mention
        education = self.profile.get("education", [])
        if education:
            top_edu = education[0]
            school = top_edu.get("institution", "")
            degree = top_edu.get("studyType", "")
            if "Georgia Tech" in school or "Georgia Institute" in school:
                summary_parts.append(f"Georgia Tech {degree} in {top_edu.get('area', 'Systems Engineering')}.")

        # Add key metrics
        if len(top_metrics) >= 2:
            summary_parts.append(
                "Reduced platform costs while scaling revenue through automated content generation and personalized user experiences."
            )

        return " ".join(summary_parts)

    def _build_skills_section(self) -> List[str]:
        """Build categorized skills section."""
        lines = []
        comprehensive = self.profile.get("comprehensive_skills", {})
        if not comprehensive:
            for label, keywords in get_profile_skill_sections(self.profile):
                lines.append(f"{label}: {', '.join(keywords[:6])}")
            return lines

        # Languages
        languages = comprehensive.get("languages", [])
        if languages:
            # Filter to main languages mentioned in v7
            main_langs = ["Python", "SQL", "TypeScript", "JavaScript"]
            filtered_langs = [
                language for language in languages if any(main_lang in language for main_lang in main_langs)
            ]
            if filtered_langs:
                lines.append(f"Languages: {', '.join(filtered_langs[:5])}")

        # Frameworks
        frameworks = comprehensive.get("frameworks", [])
        if frameworks:
            main_fw = ["Next.js", "React", "React Native"]
            filtered_fw = [f for f in frameworks if any(mf in f for mf in main_fw)]
            if filtered_fw:
                lines.append(f"Frameworks: {', '.join(filtered_fw[:4])}")

        # ML/AI
        ai_ml = comprehensive.get("ai_ml", [])
        if ai_ml:
            lines.append(f"ML/AI: {', '.join(ai_ml[:6])}")
        else:
            lines.append(
                "ML/AI: LLMs, RAG architectures, vector embeddings, recommendation systems, content automation"
            )

        # DevOps & Infra
        platforms = comprehensive.get("platforms", [])
        if platforms:
            main_plat = ["AWS", "Docker", "CI/CD"]
            filtered_plat = [p for p in platforms if any(mp in p for mp in main_plat)]
            if filtered_plat:
                lines.append(f"DevOps & Infra: {', '.join(filtered_plat[:4])}")
        else:
            lines.append("DevOps & Infra: AWS, Docker, CI/CD, Lambda")

        # Databases
        databases = comprehensive.get("databases", [])
        if databases:
            lines.append(f"Databases: {', '.join(databases[:6])}")
        else:
            lines.append("Databases: PostgreSQL, Supabase, Typesense, vector databases, Amazon Redshift")

        # Tools
        tools = comprehensive.get("tools", [])
        if tools:
            lines.append(f"Tools: {', '.join(tools[:6])}")
        else:
            lines.append("Tools: Git, Jira, Confluence, Figma, Tableau")

        return lines

    def _build_experience_section(self) -> List[str]:
        """Build experience section from work history and selected bullets."""
        lines = []
        work_history = self.profile.get("work", [])

        # Map bullets to companies by tag
        bullets_by_company: Dict[str, List[HardenedBullet]] = {}
        for bullet in self.selected_bullets:
            company = self._extract_company_from_bullet(bullet)
            if company not in bullets_by_company:
                bullets_by_company[company] = []
            bullets_by_company[company].append(bullet)

        # Build experience entries
        for job in work_history:
            company = job.get("company", "")
            position = job.get("position", "")

            # Format dates
            start_date = job.get("start_date", "")
            end_date = job.get("end_date", "present")
            date_range = self._format_date_range(start_date, end_date)

            # Add company/position header
            lines.append(f"{position} at {company}")

            # Add technologies line
            technologies = job.get("technologies", [])
            if technologies:
                tech_str = ", ".join(technologies[:8])
                lines.append(f"{tech_str} | {date_range}")
            else:
                lines.append(date_range)

            # Add bullets for this company
            company_bullets = bullets_by_company.get(company, [])
            if company_bullets:
                for bullet in company_bullets[:4]:  # Max 4 bullets per role
                    bullet_text = bullet.variants.get("car", bullet.text)
                    # Verify against banned phrases
                    if self.target_profile:
                        bullet_text = self._sanitize_bullet(bullet_text)
                    lines.append(f"- {bullet_text}")
            else:
                # Use key_metrics if no bullets available
                key_metrics = job.get("key_metrics", [])
                highlights = job.get("highlights", [])
                for i, metric in enumerate(key_metrics[:3]):
                    # Try to pair with a highlight
                    if i < len(highlights):
                        highlight = highlights[i]
                        # Extract action verb from highlight or use default
                        action = self._extract_action_from_highlight(highlight)
                        lines.append(f"- {action} {metric.lower()} through {highlight[:60].lower()}...")
                    else:
                        lines.append(f"- Achieved {metric.lower()}")

            lines.append("")

        return lines

    def _build_projects_section(self) -> List[str]:
        """Build projects section from project highlights."""
        lines = []
        projects = self.profile.get("projects", [])

        for project in projects[:3]:  # Max 3 projects
            name = project.get("name", "")
            description = project.get("description", "")
            technologies = project.get("technologies", [])

            # Format project header with technologies
            if technologies:
                tech_str = ", ".join(technologies[:5])
                lines.append(f"{name} - {description}")
                lines.append(f"{tech_str}")
            else:
                lines.append(f"{name} - {description}")

            # Try to find matching bullets or use highlights
            project_bullets = self._find_bullets_for_project(name)
            if project_bullets:
                for bullet in project_bullets[:2]:
                    bullet_text = bullet.variants.get("car", bullet.text)
                    lines.append(f"- {bullet_text}")
            else:
                # Add a generic bullet based on description
                lines.append(f"- Built {description.lower()}")

            lines.append("")

        return lines

    def _build_education_section(self) -> List[str]:
        """Build education section."""
        lines = []
        education = self.profile.get("education", [])

        for edu in education:
            institution = edu.get("institution", "")
            study_type = edu.get("studyType", "")
            area = edu.get("area", "")
            end_date = edu.get("endDate", "")

            # Extract year from end_date
            year = ""
            if end_date:
                year = end_date.split("-")[0] if "-" in end_date else end_date[:4]

            edu_line = f"{institution} | {study_type} | {area}"
            if year:
                edu_line += f" | {year}"
            lines.append(edu_line)

        return lines

    def _format_date_range(self, start_date: str, end_date: str) -> str:
        """Format date range for display."""
        # Extract years
        start_year = ""
        end_year = "Present"

        if start_date:
            parts = start_date.split("-")
            if parts:
                start_year = parts[0]
        if end_date:
            parts = end_date.split("-")
            if parts:
                end_year = parts[0]

        if start_year and end_year:
            return f"{start_year} - {end_year}"
        elif end_year:
            return end_year
        else:
            return ""

    def _extract_company_from_bullet(self, bullet: HardenedBullet) -> str:
        """Extract company name from bullet tags."""
        for tag in bullet.tags:
            if tag.startswith("company_"):
                return tag.replace("company_", "").replace("_", " ").title()
            # Check if tag matches known companies
            for company in ["DealNews", "Anaconda", "nou Systems", "Missile Defense Agency"]:
                if company.lower().replace(" ", "_") in tag.lower() or tag.lower() in company.lower().replace(" ", "_"):
                    return company
        return ""

    def _find_bullets_for_project(self, project_name: str) -> List[HardenedBullet]:
        """Find bullets matching a project name."""
        matching = []
        project_key = project_name.lower().replace(" ", "_").replace("-", "_")
        for bullet in self.selected_bullets:
            if any(project_key in tag.lower() for tag in bullet.tags):
                matching.append(bullet)
        return matching

    def _sanitize_bullet(self, bullet_text: str) -> str:
        """Remove banned phrases and ensure required patterns."""
        if not self.target_profile:
            return bullet_text

        # Check for banned phrases
        for phrase in self.target_profile.banned_phrases:
            bullet_text = bullet_text.replace(phrase, "")

        # Ensure required action verbs are present
        has_required = any(pattern in bullet_text.lower() for pattern in self.target_profile.required_patterns)
        if not has_required and self.target_profile.required_patterns:
            # Prepend a required action verb
            bullet_text = (
                f"{self.target_profile.required_patterns[0].capitalize()} {bullet_text[0].lower()}{bullet_text[1:]}"
            )

        return bullet_text.strip()

    def _extract_action_from_highlight(self, highlight: str) -> str:
        """Extract action verb from highlight text."""
        # Common action verbs
        action_verbs = [
            "Architected",
            "Built",
            "Designed",
            "Developed",
            "Led",
            "Implemented",
            "Created",
            "Managed",
            "Delivered",
            "Reduced",
            "Increased",
            "Achieved",
        ]

        words = highlight.split()
        if words and words[0].rstrip(",.;") in action_verbs:
            return words[0]

        # Default to "Built" if no action verb found
        return "Built"

    def _do_run_checks(self) -> bool:
        """Run deterministic quality gates."""
        logger.info("TAILOR: RUN_CHECKS")

        if not self.current_draft:
            logger.warning("No current draft to check")
            return False

        # Create Resume object from current draft
        resume = Resume(text=self.current_draft, sections={"EXPERIENCE": {"bullets": self.current_draft.split("\n")}})

        # Build job intelligence context from current job
        job_intel = None
        if self.current_job:
            job_intel = type(
                "JobIntel",
                (),
                {"title": self.current_job.get("title", ""), "company": self.current_job.get("company", "")},
            )()

        context = {"job_intelligence": job_intel}

        # Run MetricsGate
        metrics_gate = MetricsGate()
        self._last_metrics_result = metrics_gate.check(resume, context)

        # Run RelevanceGate
        relevance_gate = RelevanceGate()
        self._last_relevance_result = relevance_gate.check(resume, context)

        # Run MetricsRegistry validation for provenance check
        registry = MetricsRegistry()
        self._last_validation_result = registry.validate_text(self.current_draft)

        # Log results
        logger.info(
            "MetricsGate: passed=%s, score=%.2f", self._last_metrics_result.passed, self._last_metrics_result.score
        )
        logger.info(
            "RelevanceGate: passed=%s, score=%.2f",
            self._last_relevance_result.passed,
            self._last_relevance_result.score,
        )
        logger.info(
            "MetricsRegistry: valid=%d, invalid=%d",
            len(self._last_validation_result.valid_metrics),
            len(self._last_validation_result.invalid_metrics),
        )

        # All gates must pass
        return self._last_metrics_result.passed and self._last_relevance_result.passed

    def _do_independent_audit(self):
        """Builder presents, Auditor evaluates (separate roles)."""
        logger.info("TAILOR: INDEPENDENT_AUDIT")
        # Audit as separate role

    def _do_audit_scorecard(self) -> AuditScorecard:
        """Score each section 0-5 based on gate results."""
        logger.info("TAILOR: AUDIT_SCORECARD")

        # Get scores from gate results (scale 0-1 to 0-5)
        metrics_score = 0.0
        relevance_score = 0.0

        if hasattr(self, "_last_metrics_result") and self._last_metrics_result:
            metrics_score = self._last_metrics_result.score * 5.0

        if hasattr(self, "_last_relevance_result") and self._last_relevance_result:
            relevance_score = self._last_relevance_result.score * 5.0

        # Calculate weighted overall score
        # Experience/metrics weight: 60%, Summary/relevance weight: 40%
        overall_score = (metrics_score * 0.6) + (relevance_score * 0.4)

        # Calculate iteration delta if we have history
        iteration_delta = 0.0
        if self.audit_history:
            last_score = self.audit_history[-1].overall_score
            iteration_delta = overall_score - last_score

        # Determine routing recommendation based on scores and validation
        has_invalid_metrics = (
                hasattr(self, "_last_validation_result")
                and self._last_validation_result
                and len(self._last_validation_result.invalid_metrics) > 0
        )

        if has_invalid_metrics:
            routing_recommendation = "fix_credibility"
        elif metrics_score < 3.5:
            routing_recommendation = "fix_coverage"
        elif relevance_score < 3.5:
            routing_recommendation = "fix_narrative"
        elif overall_score >= 4.0:
            routing_recommendation = "accept"
        else:
            routing_recommendation = "fix_economy"

        # Collect weak lines from gate feedback
        weak_lines = []
        if hasattr(self, "_last_metrics_result") and self._last_metrics_result:
            if not self._last_metrics_result.passed:
                weak_lines.append(self._last_metrics_result.feedback)
        if hasattr(self, "_last_relevance_result") and self._last_relevance_result:
            if not self._last_relevance_result.passed:
                weak_lines.append(self._last_relevance_result.feedback)

        return AuditScorecard(
            section_scores={"summary": relevance_score, "experience": metrics_score},
            overall_score=overall_score,
            uncovered_requirements=[],
            weak_lines=weak_lines,
            missed_opportunities=[],
            routing_recommendation=routing_recommendation,
            iteration_delta=iteration_delta,
        )

    def _do_decision_gate(self, scorecard: AuditScorecard) -> bool:
        """Decide: accept, iterate, or reject based on quality scores and hallucination check."""
        logger.info("TAILOR: DECISION_GATE")

        # Check for hallucinated metrics (invalid metrics not in registry)
        has_hallucinated_metrics = (
                hasattr(self, "_last_validation_result")
                and self._last_validation_result
                and len(self._last_validation_result.invalid_metrics) > 0
        )

        if has_hallucinated_metrics:
            logger.warning(
                "Decision gate REJECTED due to %d unverified metrics", len(self._last_validation_result.invalid_metrics)
            )
            return False

        # Accept if score high enough and improvement small
        if scorecard.overall_score >= 4.0 and scorecard.iteration_delta < self.improvement_threshold:
            logger.info(
                "Decision gate ACCEPTED (score=%.2f, delta=%.2f)", scorecard.overall_score, scorecard.iteration_delta
            )
            return True

        logger.info(
            "Decision gate REJECTED (score=%.2f, delta=%.2f)", scorecard.overall_score, scorecard.iteration_delta
        )
        return False

    def _do_route_fixes(self):
        """Route to appropriate fix state."""
        logger.info("TAILOR: ROUTE_FIXES")

    def _do_route_fixes_diagnose(self, scorecard: AuditScorecard):
        """Diagnose and route to specific fix."""
        rec = scorecard.routing_recommendation

        if rec == "fix_coverage":
            self._do_fix_coverage()
        elif rec == "fix_credibility":
            self._do_fix_credibility()
        elif rec == "fix_redundancy":
            self._do_fix_redundancy()
        elif rec == "fix_ats":
            self._do_fix_ats()
        elif rec == "fix_narrative":
            self._do_fix_narrative()
        elif rec == "fix_economy":
            self._do_fix_economy()

    def _do_fix_coverage(self):
        """Fix missing hard requirements."""
        logger.info("FIX: COVERAGE")
        # Check if we have job intelligence with requirements
        if not self.job_intelligence or not self.job_intelligence.requirements:
            logger.info("No job requirements to cover")
            return

        # Find must-have requirements
        must_have_reqs = [r for r in self.job_intelligence.requirements if r.type == "must_have"]
        if not must_have_reqs:
            logger.info("No must-have requirements to cover")
            return

        # Get currently covered skills from selected bullets
        covered_skills = set()
        for bullet in self.selected_bullets:
            covered_skills.update(s.lower() for s in bullet.skills)
            covered_skills.update(t.lower() for t in bullet.tags)
            covered_skills.update(d.lower() for d in bullet.domains)

        # Find uncovered requirements
        uncovered_reqs = []
        for req in must_have_reqs:
            req_text = req.text.lower()
            # Check if any covered skill/tag/domain covers this requirement
            is_covered = any(req_text in skill or skill in req_text for skill in covered_skills)
            if not is_covered:
                uncovered_reqs.append(req)

        if not uncovered_reqs:
            logger.info("All must-have requirements are covered")
            return

        # Find bullets from bank that cover uncovered requirements
        additional_bullets = []
        for req in uncovered_reqs:
            req_keywords = set(req.text.lower().split())
            for bullet in self.bullet_bank.values():
                # Skip already selected bullets
                if bullet.id in [b.id for b in self.selected_bullets]:
                    continue
                # Check if bullet skills/tags/domains match requirement
                bullet_keywords = set(
                    [s.lower() for s in bullet.skills]
                    + [t.lower() for t in bullet.tags]
                    + [d.lower() for d in bullet.domains]
                )
                # Check for keyword overlap
                overlap = req_keywords & bullet_keywords
                if overlap and bullet not in additional_bullets:
                    additional_bullets.append(bullet)
                    if len(additional_bullets) >= 2:
                        break
            if len(additional_bullets) >= 2:
                break

        # Add selected additional bullets
        if additional_bullets:
            logger.info(
                "Adding %d bullets to cover gaps: %s",
                len(additional_bullets),
                [b.id for b in additional_bullets],
            )
            self.selected_bullets.extend(additional_bullets)
            # Reassemble draft with new bullets
            self._do_assemble_draft()
        else:
            logger.info("No suitable bullets found to cover gaps")

    def _do_fix_credibility(self):
        """Fix metric or claim risks."""
        logger.info("FIX: CREDIBILITY")
        # Find bullets with vague claims
        vague_bullets = [b for b in self.selected_bullets if b.vague_claim]
        if not vague_bullets:
            logger.info("No vague claims found in selected bullets")
            return

        # Get verified metrics from profile
        verified_metrics = get_profile_verified_metrics(self.profile)

        if not verified_metrics:
            logger.info("No verified metrics available to improve credibility")
            return

        # Try to enhance vague bullets with metrics
        improved_count = 0
        for bullet in vague_bullets:
            # Find a relevant metric for this bullet
            for metric in verified_metrics:
                # Check if metric domain/skill overlaps with bullet
                metric_lower = metric.lower()
                bullet_skills = set(s.lower() for s in bullet.skills)
                bullet_tags = set(t.lower() for t in bullet.tags)

                # Simple heuristic: check if any skill/tag appears in metric
                has_overlap = any(
                    skill in metric_lower or metric_lower in skill for skill in bullet_skills | bullet_tags
                )

                if has_overlap:
                    logger.info("Bullet %s could be enhanced with metric: %s", bullet.id, metric)
                    improved_count += 1
                    break

        if improved_count > 0:
            logger.info("Identified %d bullets that could use verified metrics", improved_count)
            # Reassemble draft (triggers warning about vague claims)
            self._do_assemble_draft()
        else:
            logger.info("No matching verified metrics found for vague bullets")

    def _do_fix_redundancy(self):
        """Fix duplication or shadowing."""
        logger.info("FIX: REDUNDANCY")
        # TODO: Implement redundancy removal per Guardrail G3

    def _do_fix_ats(self):
        """Fix keyword gap or placement."""
        logger.info("FIX: ATS")
        # TODO: Implement ATS keyword optimization per Guardrail G3

    def _do_fix_narrative(self):
        """Fix frame mismatch or ordering."""
        logger.info("FIX: NARRATIVE")
        # TODO: Implement narrative reordering per Guardrail G3

    def _do_fix_economy(self):
        """Fix length or low signal."""
        logger.info("FIX: ECONOMY")
        # TODO: Implement economy optimization per Guardrail G3

    # ========================================================================
    # PHASE 3: MAINTAIN SYSTEM
    # ========================================================================

    def maintain_system(self, job_result: Dict[str, Any]):
        """Update evidence and promote bullets after each job application."""
        self._do_update_evidence(job_result)
        self._do_update_metrics()
        self._do_promote_bullets()
        self._do_update_gap_list()

    def _do_update_evidence(self, job_result: Dict):
        """Add new evidence links via storage adapter."""
        logger.info("MAINTAIN: UPDATE_EVIDENCE")

        for bullet_id in job_result.get("selected_bullets", []):
            self.storage.record_feedback(
                bullet_id,
                job_result.get("job_title", ""),
                job_result.get("outcome", ""),
            )

    def _do_update_metrics(self):
        """Add newly verified metrics."""
        logger.info("MAINTAIN: UPDATE_METRICS")

    def _do_promote_bullets(self):
        """Promote improved bullets back to bank."""
        logger.info("MAINTAIN: PROMOTE_BULLETS")

    def _do_update_gap_list(self):
        """Track what metrics are still needed."""
        logger.info("MAINTAIN: UPDATE_GAP_LIST")


# Convenience function
def create_tailoring_engine(config: Dict[str, Any]) -> ComprehensiveTailoringEngine:
    """Factory function to create and configure the engine."""
    return ComprehensiveTailoringEngine(config)
