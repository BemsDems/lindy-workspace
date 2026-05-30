"""End-to-end pipeline tests with a mocked LLM client (v2 schema).

Verifies that:
- Analyzer JSON (new v2 schema) is consumed correctly.
- Writer signature is stripped if the model adds it.
- Validator failures trigger a retry with feedback.
- Successful generation records the opener in `used_starts`.
- Analyzer's hallucinated project name / numbers are FILTERED OUT
  (anti-hallucination grounding).
- Low confidence routes to universal mode; very low confidence skips.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from src.llm_client import LLMConfig
from src.models import Language, Position, Profile, Project, Vacancy
from src.pipeline import CoverLetterPipeline, PipelineConfig
from src.prompts.analyzer import ANALYZER_SYSTEM
from src.prompts.validator import VALIDATOR_SYSTEM
from src.prompts.writer import WRITER_SYSTEM_STANDARD, WRITER_SYSTEM_UNIVERSAL


def _make_profile() -> Profile:
    return Profile(
        name="Тест Тестов",
        experience_years=3,
        experience_months=2,
        summary="Flutter-разработчик с 3+ годами опыта.",
        skills_primary=["Flutter", "Dart", "BLoC", "Clean Architecture"],
        positions=[
            Position(
                title="Flutter-разработчик",
                company="OtherCode",
                duration_months=9,
                projects=[
                    Project(
                        name="OtherMark",
                        description="B2B/ERP-система для маркировки товаров.",
                        tech_stack=["Flutter", "BLoC", "Clean Architecture", "gRPC"],
                        achievements=[
                            "Спроектировал архитектуру на Clean Architecture с DI.",
                            "Реализовал 5 ключевых B2B-модулей со сложной бизнес-логикой.",
                            "Кодовая база превысила 11 000 строк.",
                        ],
                    ),
                ],
            ),
        ],
        languages=[Language(name="Русский", level_code="C2")],
    )


def _make_vacancy() -> Vacancy:
    return Vacancy(
        id="vac-1",
        title="Flutter-разработчик",
        company="DNS",
        description=(
            "Корпоративное приложение для сотрудников магазинов. "
            "Clean Architecture, BLoC, сложная бизнес-логика, ролевая модель."
        ),
        requirements=["Flutter", "BLoC", "Clean Architecture"],
        work_format="remote",
    )


_ANALYZER_RESPONSE: Dict[str, Any] = {
    "vacancy_type": "b2b_erp",
    "top_requirements": ["Flutter", "BLoC", "Clean Architecture"],
    "selected_project": "OtherMark",
    "confidence": 0.85,
    "confidence_reason": "ERP-система с ролями и сложной бизнес-логикой как у DNS.",
    "selected_numbers": ["3", "5", "11000"],
    "selected_achievements": [
        "Спроектировал архитектуру на Clean Architecture с DI.",
        "Реализовал 5 ключевых B2B-модулей со сложной бизнес-логикой.",
    ],
    "hook_phrase": "корпоративное приложение для сотрудников",
    "honest_gaps": [],
}


_GOOD_LETTER = (
    "3+ года разработки B2B-систем на Flutter. В OtherMark спроектировал "
    "ERP-систему на Clean Architecture с DI — 5 модулей от управления "
    "компаниями до производственных заданий со сложной бизнес-логикой и "
    "валидацией бизнес-форматов. Кодовая база превысила 11000 строк, "
    "модульная архитектура упростила поддержку и тестирование сразу "
    "нескольких независимых команд разработки. Реализовал внутреннюю "
    "дизайн-систему с кастомными таблицами и формами для единообразия "
    "интерфейса в продукте и быстрой сборки новых экранов под новые сценарии.\n\n"
    "Опыт работы с корпоративными инструментами для сотрудников подкреплён "
    "реальной практикой в OtherMark. BLoC и Clean Architecture применялись "
    "в системе с ролевой моделью и сложной бизнес-логикой. Похожие задачи "
    "решал в production-проекте с 5 модулями и 3 годами развития."
)


_BAD_LETTER = (
    "Опыт разработки B2B-систем на Flutter. В OtherMark спроектировал ERP "
    "с complex business logic — 5 модулей. Готов применить этот опыт.\n\n"
    "Ваш продукт требует похожего подхода."
)


_UNIVERSAL_LETTER = (
    "3+ года разработки на Flutter в продуктовых командах с собственной "
    "дизайн-системой и серверной интеграцией через gRPC и REST. В "
    "OtherMark спроектировал ERP-систему на Clean Architecture с DI — 5 "
    "модулей со сложной бизнес-логикой и ролевой моделью на нескольких "
    "уровнях вложенности и валидацией ввода. Кодовая база превысила 11000 "
    "строк, модульная архитектура упростила поддержку и тестирование "
    "сразу несколькими независимыми командами разработки внутри одного "
    "репозитория. Реализовал внутреннюю дизайн-систему с кастомными "
    "таблицами и формами для единообразия интерфейса и быстрой сборки "
    "новых экранов под новые бизнес-сценарии. Подход переносим на разные "
    "продуктовые задачи с типовой бизнес-логикой и работой с данными в "
    "команде."
)


class FakeLLMClient:
    """Deterministic mock that routes by system prompt."""

    def __init__(self, responses: Dict[str, List[str]]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: List[Dict[str, Any]] = []
        self.config = LLMConfig(api_key="test", endpoint="test", model="test")
        self.stats = type("S", (), {"attempts": 0, "last_error": None, "json_mode_supported": True})()

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 800,
        json_mode: bool = False,
    ) -> str:
        self.calls.append({
            "system": system_prompt,
            "user": user_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_mode": json_mode,
        })
        queue = self.responses.get(system_prompt)
        if not queue:
            raise AssertionError(
                f"No mock response queued for system_prompt={system_prompt[:60]!r}"
            )
        return queue.pop(0)

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_happy_path():
    llm = FakeLLMClient({
        ANALYZER_SYSTEM: [json.dumps(_ANALYZER_RESPONSE, ensure_ascii=False)],
        WRITER_SYSTEM_STANDARD: [_GOOD_LETTER],
        VALIDATOR_SYSTEM: [json.dumps({"passed": True, "violations": []})],
    })
    profile = _make_profile()
    pipeline = CoverLetterPipeline(llm, profile, config=PipelineConfig())
    result = await pipeline.generate(_make_vacancy())

    assert result.passed, result.violations
    assert result.letter is not None
    assert result.attempts == 1
    assert 100 <= result.word_count <= 130
    assert result.selected_project == "OtherMark"
    assert result.confidence == 0.85
    assert not result.universal_mode
    assert "11000" in result.used_numbers
    assert "5" in result.used_numbers
    assert pipeline.used_starts and "3+ года" in pipeline.used_starts[0]


@pytest.mark.asyncio
async def test_retry_on_deterministic_failure():
    llm = FakeLLMClient({
        ANALYZER_SYSTEM: [json.dumps(_ANALYZER_RESPONSE, ensure_ascii=False)],
        WRITER_SYSTEM_STANDARD: [_BAD_LETTER, _GOOD_LETTER],
        VALIDATOR_SYSTEM: [json.dumps({"passed": True, "violations": []})],
    })
    profile = _make_profile()
    pipeline = CoverLetterPipeline(llm, profile, config=PipelineConfig())
    result = await pipeline.generate(_make_vacancy())

    assert result.passed
    assert result.attempts == 2
    writer_calls = [c for c in llm.calls if c["system"] == WRITER_SYSTEM_STANDARD]
    assert len(writer_calls) == 2
    assert "Нарушения" in writer_calls[1]["user"]


@pytest.mark.asyncio
async def test_gives_up_after_max_retries():
    profile = _make_profile()
    cfg = PipelineConfig(max_writer_retries=1)  # => 2 attempts total
    llm = FakeLLMClient({
        ANALYZER_SYSTEM: [json.dumps(_ANALYZER_RESPONSE, ensure_ascii=False)],
        WRITER_SYSTEM_STANDARD: [_BAD_LETTER, _BAD_LETTER],
        VALIDATOR_SYSTEM: [],
    })
    pipeline = CoverLetterPipeline(llm, profile, config=cfg)
    result = await pipeline.generate(_make_vacancy())

    assert not result.passed
    assert result.error == "validation_failed_after_retries"
    assert result.attempts == 2
    assert result.violations


@pytest.mark.asyncio
async def test_writer_signature_is_stripped():
    profile = _make_profile()
    letter_with_sig = _GOOD_LETTER + "\n\nС уважением,\nТест Тестов"
    llm = FakeLLMClient({
        ANALYZER_SYSTEM: [json.dumps(_ANALYZER_RESPONSE, ensure_ascii=False)],
        WRITER_SYSTEM_STANDARD: [letter_with_sig],
        VALIDATOR_SYSTEM: [json.dumps({"passed": True, "violations": []})],
    })
    pipeline = CoverLetterPipeline(llm, profile, config=PipelineConfig())
    result = await pipeline.generate(_make_vacancy())
    assert result.passed
    assert "С уважением" not in (result.letter or "")


@pytest.mark.asyncio
async def test_analyzer_filters_hallucinated_project():
    """Analyzer chose a project that doesn't exist → grounded to a real one with confidence=0."""
    bad_response = dict(_ANALYZER_RESPONSE)
    bad_response["selected_project"] = "PhantomProject"
    bad_response["confidence"] = 0.9  # the LLM was confident in its hallucination

    llm = FakeLLMClient({
        ANALYZER_SYSTEM: [json.dumps(bad_response, ensure_ascii=False)],
        # No writer call expected because the grounded confidence is 0.0,
        # which is below skip_below_confidence (0.2).
        WRITER_SYSTEM_STANDARD: [],
        WRITER_SYSTEM_UNIVERSAL: [],
        VALIDATOR_SYSTEM: [],
    })
    profile = _make_profile()
    pipeline = CoverLetterPipeline(llm, profile, config=PipelineConfig())
    result = await pipeline.generate(_make_vacancy())

    # Hallucination filtered: project fell back to a real one, confidence zeroed.
    assert result.selected_project == "OtherMark"
    assert result.confidence == 0.0
    assert not result.passed
    assert result.error == "skipped_low_confidence"
    # Writer was NEVER called.
    assert not any(c["system"] in (WRITER_SYSTEM_STANDARD, WRITER_SYSTEM_UNIVERSAL) for c in llm.calls)


@pytest.mark.asyncio
async def test_analyzer_filters_hallucinated_numbers():
    """Analyzer's selected_numbers outside CanonicalFacts.allowed_numbers are dropped."""
    bad_response = dict(_ANALYZER_RESPONSE)
    bad_response["selected_numbers"] = ["999", "9999"]  # Neither in resume.

    llm = FakeLLMClient({
        ANALYZER_SYSTEM: [json.dumps(bad_response, ensure_ascii=False)],
        WRITER_SYSTEM_STANDARD: [_GOOD_LETTER],
        VALIDATOR_SYSTEM: [json.dumps({"passed": True, "violations": []})],
    })
    profile = _make_profile()
    pipeline = CoverLetterPipeline(llm, profile, config=PipelineConfig())
    result = await pipeline.generate(_make_vacancy())

    # Grounding filtered "999"/"9999" out and replaced with real numbers.
    grounded_numbers = result.analyzer_json["selected_numbers"]
    assert "999" not in grounded_numbers
    assert "9999" not in grounded_numbers
    assert "3" in grounded_numbers  # years of experience
    # The letter uses only legit numbers, so validation passes.
    assert result.passed


@pytest.mark.asyncio
async def test_low_confidence_routes_to_universal_mode():
    response = dict(_ANALYZER_RESPONSE)
    response["confidence"] = 0.35  # < default low_confidence_threshold (0.5)

    llm = FakeLLMClient({
        ANALYZER_SYSTEM: [json.dumps(response, ensure_ascii=False)],
        WRITER_SYSTEM_UNIVERSAL: [_UNIVERSAL_LETTER],
        VALIDATOR_SYSTEM: [json.dumps({"passed": True, "violations": []})],
    })
    profile = _make_profile()
    pipeline = CoverLetterPipeline(llm, profile, config=PipelineConfig())
    result = await pipeline.generate(_make_vacancy())

    assert result.passed, result.violations
    assert result.universal_mode is True
    # Universal mode uses the universal system prompt, not the standard one.
    assert any(c["system"] == WRITER_SYSTEM_UNIVERSAL for c in llm.calls)
    assert not any(c["system"] == WRITER_SYSTEM_STANDARD for c in llm.calls)


@pytest.mark.asyncio
async def test_no_semantic_validator_means_no_validator_calls():
    cfg = PipelineConfig(use_semantic_validator=False)
    llm = FakeLLMClient({
        ANALYZER_SYSTEM: [json.dumps(_ANALYZER_RESPONSE, ensure_ascii=False)],
        WRITER_SYSTEM_STANDARD: [_GOOD_LETTER],
        VALIDATOR_SYSTEM: [],
    })
    profile = _make_profile()
    pipeline = CoverLetterPipeline(llm, profile, config=cfg)
    result = await pipeline.generate(_make_vacancy())

    assert result.passed
    assert result.semantic_validator_used is False
    assert not any(c["system"] == VALIDATOR_SYSTEM for c in llm.calls)
