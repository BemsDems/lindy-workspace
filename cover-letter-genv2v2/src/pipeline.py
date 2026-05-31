"""
3-pass orchestrator: Analyze -> Write -> Validate -> (Repair | Rewrite up to N).

v2 changes:
- Builds a `CanonicalFacts` from the profile once at init.
- Passes CanonicalFacts (read-only) into Analyzer and Validator.
- Routes low-confidence vacancies to universal-letter mode.
- Emits richer `GenerationResult` (selected_project, confidence,
  used_numbers, used_tech, attempts, semantic_validator_used).

v3 changes:
- repair_on_validation_failed=True now actually calls repair_letter_after_validation()
  instead of falling through to a full write_letter() retry.
- ProjectSelector module-level singleton has been removed to prevent
  _recent_picks state leaks. Currently the pipeline relies on the Analyzer's
  LLM-based project selection; if deterministic ProjectSelector override
  is reintroduced, it should be instantiated per-pipeline and merged with
  the analyzer output explicitly.

v4 changes:
- Stopped force-injecting `facts.experience_years` into `selected_numbers`.
  The previous behavior unconditionally pushed "3" to the front of the
  whitelist, which (combined with the years-anchored opener pool and the
  writer's metric whitelist) caused the canned "3+ years Flutter-разработчик"
  opener to surface across unrelated vacancies. If the Analyzer decides
  years matter for a specific vacancy, it will include them in
  selected_numbers explicitly; otherwise the letter is anchored on
  project achievements instead.
- Removed the `priority_numbers` preserve block. With years no longer
  force-injected, the only special-case numbers are the Flutter migration
  versions (3.0.2, 3.29.0), which are extended directly from selected
  achievements below.

v5 changes:
- Fixed `asyncion` typo (was ImportError on every load) -> `asyncio`.
- Raised default `max_writer_retries` from 0 to 2 so that the
  repair_letter_after_validation path actually runs when deterministic
  validation flags a fixable issue (e.g. word_count_too_high).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .analyzer import analyze
from .facts import CanonicalFacts, extract_canonical_facts
from .llm_client import LLMClient
from .models import Profile, Vacancy
from .validator import ValidationResult, validate_deterministic, validate_semantic
from .postprocess import postprocess_letter
from .writer import repair_letter_after_validation, write_letter


logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    vacancy_id: str
    company: str
    title: str
    letter: Optional[str]
    analyzer_json: Optional[Dict[str, Any]]
    selected_project: Optional[str]
    confidence: float
    confidence_reason: str
    used_numbers: List[str]
    used_tech: List[str]
    universal_mode: bool
    semantic_validator_used: bool
    word_count: int
    passed: bool
    attempts: int
    violations: List[Dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vacancy_id": self.vacancy_id,
            "company": self.company,
            "title": self.title,
            "letter": self.letter,
            "analyzer_json": self.analyzer_json,
            "selected_project": self.selected_project,
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "used_numbers": self.used_numbers,
            "used_tech": self.used_tech,
            "universal_mode": self.universal_mode,
            "semantic_validator_used": self.semantic_validator_used,
            "word_count": self.word_count,
            "passed": self.passed,
            "attempts": self.attempts,
            "violations": self.violations,
            "error": self.error,
        }


@dataclass
class PipelineConfig:
    min_words: int = 70
    max_words: int = 110
    max_writer_retries: int = 2
    use_semantic_validator: bool = True
    # If true, skip deterministic validation entirely (numbers, anglicisms, etc.).
    # Useful for debugging / prompt iteration.
    skip_deterministic_validation: bool = False
    writer_temperature: float = 0.25
    writer_max_tokens: int = 900
    writer_two_pass_editing: bool = False
    # When True, a failed validation triggers repair_letter_after_validation()
    # (targeted fix) instead of a full write_letter() retry.
    repair_on_validation_failed: bool = True
    # Confidence threshold: vacancies below this trigger universal-letter mode.
    low_confidence_threshold: float = 0.5
    # Hard cutoff: below this we don't generate at all.
    skip_below_confidence: float = 0.2
    # Per-stage timeout (seconds) for each LLM call wrapped in asyncio.wait_for.
    stage_timeout: float = 300.0


class CoverLetterPipeline:
    """Stateful pipeline.

    Tracks `used_starts` across `.generate()` calls to encourage variety in
    the first sentence within a single batch.
    """

    def __init__(
        self,
        llm: LLMClient,
        profile: Profile,
        config: Optional[PipelineConfig] = None,
        *,
        forbidden_claims: Optional[List[str]] = None,
    ):
        self.llm = llm
        self.profile = profile
        self.config = config or PipelineConfig()
        self.used_starts: List[str] = []
        self.facts: CanonicalFacts = extract_canonical_facts(
            profile, forbidden_claims=forbidden_claims
        )

    async def generate(self, vacancy: Vacancy) -> GenerationResult:
        try:
            analyzer_json = await asyncio.wait_for(
                analyze(self.llm, vacancy, self.facts),
                timeout=self.config.stage_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Analyzer failed for vacancy %s", vacancy.id)
            return _error_result(vacancy, error=f"analyzer: {exc}")

        confidence = float(analyzer_json.get("confidence", 0.0))
        confidence_reason = str(analyzer_json.get("confidence_reason") or "")
        selected_project = str(analyzer_json.get("selected_project") or "")
        selected_numbers: List[str] = list(analyzer_json.get("selected_numbers") or [])
        selected_achievements_for_numbers: List[str] = list(
            analyzer_json.get("selected_achievements") or []
        )
        achievements_text = "\n".join(str(item) for item in selected_achievements_for_numbers)

        # Extend selected_numbers with real Flutter migration versions when they
        # appear in the selected achievements. This is the only auto-inject we
        # still do — years of experience are NO LONGER force-injected
        # (see v4 changes in the module docstring).
        for version in ("3.0.2", "3.29.0"):
            if version in achievements_text and version not in selected_numbers:
                selected_numbers.append(version)

        # Cap at 5 numbers max. With years removed from the priority list,
        # we preserve only Flutter migration versions explicitly; the rest
        # keeps the Analyzer's original ordering.
        priority_numbers = ["3.0.2", "3.29.0"]
        preserved: List[str] = []

        for number in priority_numbers:
            if number in selected_numbers and number not in preserved:
                preserved.append(number)

        for number in selected_numbers:
            if number not in preserved:
                preserved.append(number)

        selected_numbers = preserved[:5]
        analyzer_json["selected_numbers"] = selected_numbers

        # Hard skip on very low confidence — emit a result with no letter.
        if confidence < self.config.skip_below_confidence:
            logger.info(
                "Vacancy %s: confidence %.2f below skip threshold %.2f — skipping",
                vacancy.id,
                confidence,
                self.config.skip_below_confidence,
            )
            return GenerationResult(
                vacancy_id=vacancy.id,
                company=vacancy.company,
                title=vacancy.title,
                letter=None,
                analyzer_json=analyzer_json,
                selected_project=selected_project,
                confidence=confidence,
                confidence_reason=confidence_reason,
                used_numbers=[],
                used_tech=[],
                universal_mode=False,
                semantic_validator_used=False,
                word_count=0,
                passed=False,
                attempts=0,
                error="skipped_low_confidence",
            )

        universal_mode = confidence < self.config.low_confidence_threshold
        if universal_mode:
            logger.info(
                "Vacancy %s: confidence %.2f below %.2f — universal mode",
                vacancy.id,
                confidence,
                self.config.low_confidence_threshold,
            )

        feedback: Optional[str] = None
        last_letter: str = ""
        last_result: Optional[ValidationResult] = None
        semantic_used = False

        # Build a compact facts brief for repair calls (mirrors what write_letter uses).
        from .writer import build_canonical_facts_brief
        canonical_facts_brief = build_canonical_facts_brief(self.facts, selected_project)

        for attempt in range(1, self.config.max_writer_retries + 2):
            # On attempt > 1 with repair enabled: use targeted repair instead of full rewrite.
            if attempt > 1 and self.config.repair_on_validation_failed and feedback and last_letter:
                try:
                    last_letter = await asyncio.wait_for(
                        repair_letter_after_validation(
                            self.llm,
                            letter=last_letter,
                            validation_feedback=feedback,
                            analyzer_json=analyzer_json,
                            canonical_facts_brief=canonical_facts_brief,
                            max_tokens=self.config.writer_max_tokens,
                        ),
                        timeout=self.config.stage_timeout,
                    )
                    last_letter = postprocess_letter(last_letter)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Repair failed (attempt %d) for vacancy %s", attempt, vacancy.id
                    )
                    return _error_result(
                        vacancy,
                        error=f"repair: {exc}",
                        analyzer_json=analyzer_json,
                        confidence=confidence,
                        confidence_reason=confidence_reason,
                        selected_project=selected_project,
                        universal_mode=universal_mode,
                        attempts=attempt,
                    )
            else:
                try:
                    last_letter = await asyncio.wait_for(
                        write_letter(
                            self.llm,
                            analyzer_json=analyzer_json,
                            facts=self.facts,
                            used_starts=self.used_starts,
                            feedback=feedback,
                            universal_mode=universal_mode,
                            temperature=self.config.writer_temperature,
                            max_tokens=self.config.writer_max_tokens,
                            two_pass_editing=self.config.writer_two_pass_editing,
                            vacancy_title=vacancy.title or "",
                            vacancy_company=vacancy.company or "",
                            vacancy_description=vacancy.description or "",
                            vacancy_requirements=list(vacancy.requirements or []),
                        ),
                        timeout=self.config.stage_timeout,
                    )
                    last_letter = postprocess_letter(last_letter)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "Writer failed (attempt %d) for vacancy %s", attempt, vacancy.id
                    )
                    return _error_result(
                        vacancy,
                        error=f"writer: {exc}",
                        analyzer_json=analyzer_json,
                        confidence=confidence,
                        confidence_reason=confidence_reason,
                        selected_project=selected_project,
                        universal_mode=universal_mode,
                        attempts=attempt,
                    )

            if self.config.skip_deterministic_validation:
                det = ValidationResult(
                    passed=True,
                    violations=[],
                    word_count=len(last_letter.split()),
                    used_numbers=list(selected_numbers),
                    used_tech=[],
                )
            else:
                det = validate_deterministic(
                    last_letter,
                    facts=self.facts,
                    allowed_numbers=selected_numbers,
                    selected_achievements=analyzer_json.get("selected_achievements") or [],
                    min_words=self.config.min_words,
                    max_words=self.config.max_words,
                    universal_mode=universal_mode,
                )
                if not det.passed:
                    feedback = det.format_feedback()
                    last_result = det
                    logger.info(
                        "Vacancy %s: attempt %d failed deterministic validation (%d violations)",
                        vacancy.id,
                        attempt,
                        len(det.violations),
                    )
                    if attempt >= self.config.max_writer_retries + 1:
                        return GenerationResult(
                            vacancy_id=vacancy.id,
                            company=vacancy.company,
                            title=vacancy.title,
                            letter=last_letter,
                            analyzer_json=analyzer_json,
                            selected_project=selected_project,
                            confidence=confidence,
                            confidence_reason=confidence_reason,
                            used_numbers=det.used_numbers,
                            used_tech=det.used_tech,
                            universal_mode=universal_mode,
                            semantic_validator_used=False,
                            word_count=det.word_count,
                            passed=False,
                            attempts=attempt,
                            error="validation_failed_after_retries",
                            violations=[v.to_dict() for v in det.violations],
                        )
                    continue

            if self.config.use_semantic_validator:
                semantic_used = True
                try:
                    sem = await asyncio.wait_for(
                        validate_semantic(
                            self.llm,
                            last_letter,
                            analyzer_json,
                            selected_numbers or list(self.facts.allowed_numbers),
                        ),
                        timeout=self.config.stage_timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Semantic validator errored, treating as passed: %s", exc)
                    sem = ValidationResult(
                        passed=True, violations=[], word_count=det.word_count
                    )

                if not sem.passed:
                    feedback = sem.format_feedback()
                    sem.used_numbers = det.used_numbers
                    sem.used_tech = det.used_tech
                    last_result = sem
                    logger.info(
                        "Vacancy %s: attempt %d failed semantic validation (%d violations)",
                        vacancy.id,
                        attempt,
                        len(sem.violations),
                    )
                    continue
                sem.used_numbers = det.used_numbers
                sem.used_tech = det.used_tech
                last_result = sem
            else:
                last_result = det

            # Success: record opener for variety.
            opener = last_letter.strip().split(".", 1)[0]
            if opener:
                self.used_starts.append(opener[:80])

            return GenerationResult(
                vacancy_id=vacancy.id,
                company=vacancy.company,
                title=vacancy.title,
                letter=last_letter,
                analyzer_json=analyzer_json,
                selected_project=selected_project,
                confidence=confidence,
                confidence_reason=confidence_reason,
                used_numbers=list(last_result.used_numbers),
                used_tech=list(last_result.used_tech),
                universal_mode=universal_mode,
                semantic_validator_used=semantic_used,
                word_count=det.word_count,
                passed=True,
                attempts=attempt,
            )

        # Out of retries.
        violations = [v.to_dict() for v in (last_result.violations if last_result else [])]
        return GenerationResult(
            vacancy_id=vacancy.id,
            company=vacancy.company,
            title=vacancy.title,
            letter=last_letter or None,
            analyzer_json=analyzer_json,
            selected_project=selected_project,
            confidence=confidence,
            confidence_reason=confidence_reason,
            used_numbers=list(last_result.used_numbers) if last_result else [],
            used_tech=list(last_result.used_tech) if last_result else [],
            universal_mode=universal_mode,
            semantic_validator_used=semantic_used,
            word_count=(last_result.word_count if last_result else 0),
            passed=False,
            attempts=self.config.max_writer_retries + 1,
            violations=violations,
            error="validation_failed_after_retries",
        )

    async def generate_batch(
        self,
        vacancies: List[Vacancy],
        *,
        max_concurrent: int = 5,
    ) -> List[GenerationResult]:
        sem = asyncio.Semaphore(max_concurrent)

        async def _one(v: Vacancy) -> GenerationResult:
            async with sem:
                return await self.generate(v)

        return await asyncio.gather(*(_one(v) for v in vacancies))


def _error_result(
    vacancy: Vacancy,
    *,
    error: str,
    analyzer_json: Optional[Dict[str, Any]] = None,
    confidence: float = 0.0,
    confidence_reason: str = "",
    selected_project: Optional[str] = None,
    universal_mode: bool = False,
    attempts: int = 0,
) -> GenerationResult:
    return GenerationResult(
        vacancy_id=vacancy.id,
        company=vacancy.company,
        title=vacancy.title,
        letter=None,
        analyzer_json=analyzer_json,
        selected_project=selected_project,
        confidence=confidence,
        confidence_reason=confidence_reason,
        used_numbers=[],
        used_tech=[],
        universal_mode=universal_mode,
        semantic_validator_used=False,
        word_count=0,
        passed=False,
        attempts=attempts,
        error=error,
    )
