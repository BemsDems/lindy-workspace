"""Pipeline orchestrator for the cover-letter pipeline.

Wires together the stages from analyzer/writer/validator and exposes a single
`run_pipeline()` entry point used by the CLI/notebook.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .analyzer import (
    AnalyzerOutput,
    analyze_vacancy,
    build_canonical_facts_brief,
)
from .postprocess import (
    enforce_letter_constraints,
    extract_letter_from_text,
)
from .validator import (
    LetterValidationReport,
    validate_letter_against_facts,
)
from .writer import (
    WriterOutput,
    repair_letter_after_validation,
    write_letter,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vacancy + Resume input dataclasses
# ---------------------------------------------------------------------------


@dataclass
class VacancyInput:
    """Input vacancy as it arrives from the upstream feed."""

    id: str
    title: str = ""
    description: str = ""
    company: str = ""
    domain: str = ""
    salary: str = ""
    seniority: str = ""
    location: str = ""
    employment: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "VacancyInput":
        return cls(
            id=str(row.get("id") or row.get("vacancy_id") or ""),
            title=str(row.get("title") or row.get("name") or ""),
            description=str(row.get("description") or row.get("text") or ""),
            company=str(row.get("company") or row.get("employer") or ""),
            domain=str(row.get("domain") or ""),
            salary=str(row.get("salary") or ""),
            seniority=str(row.get("seniority") or row.get("level") or ""),
            location=str(row.get("location") or row.get("city") or ""),
            employment=str(row.get("employment") or row.get("schedule") or ""),
            raw=dict(row),
        )


@dataclass
class ResumeInput:
    """The candidate's resume, as canonical facts."""

    full_text: str
    canonical_facts: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline configuration
# ---------------------------------------------------------------------------


@dataclass
class PipelineConfig:
    """Runtime configuration knobs for `run_pipeline()`."""

    # Whether to attempt LLM repair after a failed validation pass.
    enable_repair_pass: bool = True
    # Max number of validate -> repair -> revalidate loops.
    max_repair_attempts: int = 2
    # Per-stage soft timeout (seconds). The pipeline still completes, but
    # stages that take longer than this get logged as slow.
    stage_timeout: float = 90.0
    # If true, run the analyzer in strict mode (raises on missing fields).
    analyzer_strict: bool = False
    # Words: hard min/max bounds the final letter must satisfy.
    min_words: int = 80
    max_words: int = 110
    # If True, skip the LLM rewrite when the vacancy looks low-confidence
    # and only emit a universal letter via the writer's "safe mode".
    universal_letter_on_low_confidence: bool = True
    # If true, skip deterministic validation entirely (numbers, anglicisms, etc.).
    # Useful for debugging / prompt iteration.
    skip_deterministic_validation: bool = False
    writer_temperature: float = 0.25
    writer_max_tokens: int = 900
    # Separate token cap for repair passes — tighter than writer_max_tokens
    # to physically prevent the repair LLM from generating over-length letters.
    # 400 tokens ≈ 300 Russian words, well above the 110-word letter limit.
    repair_max_tokens: int = 400
    writer_two_pass_editing: bool = False
    # When True, a failed validation triggers repair_letter_after_validation()
    # (targeted fix) instead of a full write_letter() retry.
    repair_on_validation_failed: bool = True
    # Confidence threshold: vacancies below this trigger universal-letter mode.
    low_confidence_threshold: float = 0.5
    # Hard cutoff: below this we don't generate at all.
    min_confidence_threshold: float = 0.2
    # Callback for emitting metrics (telemetry hook).
    metrics_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------


@dataclass
class PipelineStageResult:
    """Outcome of a single stage."""

    name: str
    started_at: float
    finished_at: float
    success: bool
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        return max(self.finished_at - self.started_at, 0.0)


@dataclass
class PipelineResult:
    """Full pipeline outcome for a single vacancy."""

    vacancy_id: str
    letter: str
    universal_mode: bool
    confidence: float
    analyzer: Optional[AnalyzerOutput] = None
    writer: Optional[WriterOutput] = None
    validation_passes: List[LetterValidationReport] = field(default_factory=list)
    stages: List[PipelineStageResult] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    skipped: bool = False
    skipped_reason: Optional[str] = None

    @property
    def final_validation(self) -> Optional[LetterValidationReport]:
        return self.validation_passes[-1] if self.validation_passes else None

    @property
    def passed_validation(self) -> bool:
        rep = self.final_validation
        return bool(rep and rep.passed)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _emit(cfg: PipelineConfig, name: str, payload: Dict[str, Any]) -> None:
    if cfg.metrics_callback is None:
        return
    try:
        cfg.metrics_callback(name, payload)
    except Exception:  # noqa: BLE001
        logger.exception("metrics_callback failed for %s", name)


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or "", flags=re.UNICODE))


def _classify_confidence(analyzer: AnalyzerOutput, cfg: PipelineConfig) -> str:
    conf = float(analyzer.confidence or 0.0)
    if conf < cfg.min_confidence_threshold:
        return "skip"
    if conf < cfg.low_confidence_threshold:
        return "universal"
    return "tailored"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_pipeline(
    vacancy: VacancyInput,
    resume: ResumeInput,
    *,
    config: Optional[PipelineConfig] = None,
) -> PipelineResult:
    """Run the full pipeline for one vacancy.

    This is the only function downstream code is expected to call.
    """
    return await _Pipeline(config or PipelineConfig()).run(vacancy, resume)


class _Pipeline:
    """Internal orchestrator. Keeps run state on self to avoid threading a
    big bag of locals through every helper.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.stages: List[PipelineStageResult] = []

    # -- stage utilities -------------------------------------------------

    async def _stage(self, name: str, coro):
        started = time.time()
        try:
            value = await coro
        except Exception as exc:  # noqa: BLE001
            finished = time.time()
            self.stages.append(
                PipelineStageResult(
                    name=name,
                    started_at=started,
                    finished_at=finished,
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            _emit(self.config, "stage_failed", {"stage": name, "error": str(exc)})
            raise
        finished = time.time()
        self.stages.append(
            PipelineStageResult(
                name=name,
                started_at=started,
                finished_at=finished,
                success=True,
            )
        )
        _emit(
            self.config,
            "stage_completed",
            {"stage": name, "duration_sec": finished - started},
        )
        return value

    # -- main run --------------------------------------------------------

    async def run(self, vacancy: VacancyInput, resume: ResumeInput) -> PipelineResult:
        cfg = self.config
        result = PipelineResult(
            vacancy_id=vacancy.id,
            letter="",
            universal_mode=False,
            confidence=0.0,
        )

        # ----- 1. Analyzer ----------------------------------------------
        analyzer_json = await self._stage(
            "analyze_vacancy",
            asyncio.wait_for(
                analyze_vacancy(
                    vacancy=vacancy,
                    resume=resume,
                    strict=cfg.analyzer_strict,
                ),
                timeout=cfg.stage_timeout,
            ),
        )
        result.analyzer = analyzer_json
        result.confidence = float(analyzer_json.confidence or 0.0)

        # ----- 2. Branch on confidence -----------------------------------
        decision = _classify_confidence(analyzer_json, cfg)
        if decision == "skip":
            result.skipped = True
            result.skipped_reason = (
                f"confidence={result.confidence:.2f} below "
                f"min_confidence_threshold={cfg.min_confidence_threshold}"
            )
            _emit(cfg, "vacancy_skipped", {"vacancy_id": vacancy.id, "reason": result.skipped_reason})
            result.stages = self.stages
            return result

        universal_mode = decision == "universal"
        result.universal_mode = universal_mode

        canonical_facts_brief = build_canonical_facts_brief(
            resume=resume,
            analyzer=analyzer_json,
        )

        # ----- 3. Initial write ------------------------------------------
        writer_out: WriterOutput = await self._stage(
            "write_letter",
            asyncio.wait_for(
                write_letter(
                    vacancy=vacancy,
                    resume=resume,
                    analyzer=analyzer_json,
                    canonical_facts_brief=canonical_facts_brief,
                    universal_mode=universal_mode,
                    temperature=cfg.writer_temperature,
                    max_tokens=cfg.writer_max_tokens,
                    two_pass_editing=cfg.writer_two_pass_editing,
                ),
                timeout=cfg.stage_timeout,
            ),
        )
        result.writer = writer_out
        letter = extract_letter_from_text(writer_out.letter)
        letter = enforce_letter_constraints(
            letter,
            min_words=cfg.min_words,
            max_words=cfg.max_words,
        )

        # ----- 4. Validate + (maybe) repair ------------------------------
        if cfg.skip_deterministic_validation:
            result.letter = letter
            result.stages = self.stages
            return result

        for attempt in range(cfg.max_repair_attempts + 1):
            report = await self._stage(
                f"validate_letter_attempt_{attempt}",
                asyncio.wait_for(
                    validate_letter_against_facts(
                        letter=letter,
                        analyzer=analyzer_json,
                        canonical_facts_brief=canonical_facts_brief,
                        vacancy_title=vacancy.title or "",
                        vacancy_domain=vacancy.domain or analyzer_json.vacancy_domain or "",
                        min_words=cfg.min_words,
                        max_words=cfg.max_words,
                    ),
                    timeout=cfg.stage_timeout,
                ),
            )
            result.validation_passes.append(report)

            if report.passed:
                break
            if attempt >= cfg.max_repair_attempts:
                break
            if not cfg.enable_repair_pass:
                break

            feedback = report.format_feedback()
            if cfg.repair_on_validation_failed:
                repaired = await self._stage(
                    f"repair_letter_attempt_{attempt}",
                    asyncio.wait_for(
                        repair_letter_after_validation(
                            letter=letter,
                            validation_feedback=feedback,
                            analyzer_json=analyzer_json,
                            canonical_facts_brief=canonical_facts_brief,
                            max_tokens=self.config.repair_max_tokens,
                        ),
                        timeout=self.config.stage_timeout,
                    ),
                )
                letter = extract_letter_from_text(repaired)
            else:
                rewritten = await self._stage(
                    f"rewrite_letter_attempt_{attempt}",
                    asyncio.wait_for(
                        write_letter(
                            vacancy=vacancy,
                            resume=resume,
                            analyzer=analyzer_json,
                            canonical_facts_brief=canonical_facts_brief,
                            feedback=feedback,
                            universal_mode=universal_mode,
                            temperature=self.config.writer_temperature,
                            max_tokens=self.config.repair_max_tokens,
                            two_pass_editing=self.config.writer_two_pass_editing,
                            vacancy_title=vacancy.title or "",
                            vacancy_domain=vacancy.domain or analyzer_json.vacancy_domain or "",
                        ),
                        timeout=self.config.stage_timeout,
                    ),
                )
                letter = extract_letter_from_text(rewritten.letter)

            letter = enforce_letter_constraints(
                letter,
                min_words=cfg.min_words,
                max_words=cfg.max_words,
            )

        result.letter = letter
        result.stages = self.stages
        result.metadata.update(
            {
                "word_count": _word_count(letter),
                "attempts_used": len(result.validation_passes),
            }
        )
        return result


# ---------------------------------------------------------------------------
# Batch helper
# ---------------------------------------------------------------------------


async def run_pipeline_batch(
    vacancies: Sequence[VacancyInput],
    resume: ResumeInput,
    *,
    config: Optional[PipelineConfig] = None,
    concurrency: int = 4,
) -> List[PipelineResult]:
    """Run the pipeline over many vacancies with bounded concurrency."""

    cfg = config or PipelineConfig()
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(vac: VacancyInput) -> PipelineResult:
        async with sem:
            try:
                return await run_pipeline(vac, resume, config=cfg)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Pipeline crashed for vacancy=%s", vac.id)
                return PipelineResult(
                    vacancy_id=vac.id,
                    letter="",
                    universal_mode=False,
                    confidence=0.0,
                    metadata={"crash": f"{type(exc).__name__}: {exc}"},
                )

    return await asyncio.gather(*[_one(v) for v in vacancies])


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


def result_to_json(result: PipelineResult) -> Dict[str, Any]:
    """Return a JSON-safe dict for one pipeline result."""

    return {
        "vacancy_id": result.vacancy_id,
        "letter": result.letter,
        "universal_mode": result.universal_mode,
        "confidence": result.confidence,
        "skipped": result.skipped,
        "skipped_reason": result.skipped_reason,
        "passed_validation": result.passed_validation,
        "metadata": result.metadata,
        "stages": [
            {
                "name": s.name,
                "duration_sec": round(s.duration_sec, 3),
                "success": s.success,
                "error": s.error,
            }
            for s in result.stages
        ],
        "validation_passes": [
            {
                "passed": v.passed,
                "violations": [
                    {
                        "rule": viol.rule,
                        "severity": viol.severity,
                        "evidence": viol.evidence,
                        "fix_hint": viol.fix_hint,
                    }
                    for viol in v.violations
                ],
                "word_count": v.word_count,
            }
            for v in result.validation_passes
        ],
    }


def results_to_jsonl(results: Sequence[PipelineResult]) -> str:
    """Serialise a batch as JSONL (one result per line)."""
    return "\n".join(json.dumps(result_to_json(r), ensure_ascii=False) for r in results)
