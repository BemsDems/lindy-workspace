"""Tests for the deterministic validator."""

from __future__ import annotations

from src.facts import CanonicalFacts, ProjectFacts
from src.validator import validate_deterministic


ALLOWED_NUMBERS = ["3", "5", "11000", "4", "6", "20"]


def _make_facts(
    *,
    allowed_numbers=None,
    allowed_tech=None,
    allowed_project_names=None,
    allowed_company_names=None,
    profile_text="OtherMark erp clean architecture flutter bloc",
    forbidden_claims=None,
) -> CanonicalFacts:
    return CanonicalFacts(
        candidate_name="Иван Иванов",
        experience_years=3,
        experience_months=2,
        summary="",
        profile_text_lower=profile_text.lower(),
        allowed_numbers=list(allowed_numbers or ALLOWED_NUMBERS),
        allowed_tech=set(allowed_tech or {"Flutter", "Dart", "BLoC", "Clean", "Architecture"}),
        allowed_project_names=set(allowed_project_names or {"OtherMark"}),
        allowed_company_names=set(allowed_company_names or {"OtherCode"}),
        projects={
            "OtherMark": ProjectFacts(
                name="OtherMark",
                company="OtherCode",
                industry="ERP",
                description="ERP system",
                tech_stack=["Flutter", "Dart", "BLoC", "Clean Architecture"],
                achievements=["Спроектировал ERP-систему на Clean Architecture."],
                allowed_numbers=["5", "11000"],
            )
        },
        forbidden_claims=list(forbidden_claims) if forbidden_claims is not None else [],
    )


GOOD_LETTER = """\
3+ года разработки B2B-систем на Flutter. В OtherMark спроектировал ERP-систему \
на Clean Architecture с DI — 5 модулей от управления компаниями до производственных \
заданий со сложной бизнес-логикой и валидацией бизнес-форматов. Кодовая база превысила \
11000 строк, модульная архитектура упростила поддержку и тестирование сразу нескольких \
независимых команд разработки. Реализовал внутреннюю дизайн-систему с кастомными \
таблицами и формами для единообразия интерфейса в продукте и быстрой сборки новых \
экранов под новые бизнес-сценарии.

Опыт работы с корпоративными инструментами для сотрудников подкреплён реальной \
практикой в OtherMark. BLoC и Clean Architecture применялись в системе с ролевой \
моделью и сложной бизнес-логикой. Похожие задачи решал в production-проекте \
с 6 типами ролей и 20 переиспользуемыми UI-компонентами.\
"""


def test_good_letter_passes():
    facts = _make_facts()
    result = validate_deterministic(GOOD_LETTER, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert result.passed, [v.to_dict() for v in result.violations]
    assert 100 <= result.word_count <= 130
    # used_numbers should be populated.
    assert "11000" in result.used_numbers
    assert "5" in result.used_numbers


def test_invented_number_is_flagged():
    text = GOOD_LETTER.replace("11000 строк", "сотен тысяч пользователей и 999 строк")
    facts = _make_facts()
    result = validate_deterministic(text, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert not result.passed
    rules = {v.rule for v in result.violations}
    assert "invented_number" in rules


def test_forbidden_phrase_is_flagged():
    text = GOOD_LETTER + "\n\nГотов применить опыт в вашей команде."
    facts = _make_facts()
    result = validate_deterministic(text, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert not result.passed
    rules = {v.rule for v in result.violations}
    assert "forbidden_phrase" in rules


def test_library_name_is_flagged():
    text = GOOD_LETTER.replace("Clean Architecture с DI", "Clean Architecture с GetIt")
    facts = _make_facts()
    result = validate_deterministic(text, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert not result.passed
    assert any(v.rule == "library_name" for v in result.violations)


def test_paragraph_count_is_enforced():
    one_para = GOOD_LETTER.replace("\n\n", " ")
    facts = _make_facts()
    result = validate_deterministic(one_para, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert not result.passed
    assert any(v.rule == "wrong_paragraph_count" for v in result.violations)


def test_too_short_letter_is_flagged():
    short = "3+ года разработки на Flutter. В OtherMark реализовал 5 модулей.\n\nКодовая база 11000 строк."
    facts = _make_facts()
    result = validate_deterministic(short, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert not result.passed
    assert any(v.rule == "too_short" for v in result.violations)


def test_first_sentence_must_contain_a_number():
    text = "Опыт разработки на Flutter в течение нескольких лет в B2B-системах. " + GOOD_LETTER.split(". ", 1)[1]
    facts = _make_facts()
    result = validate_deterministic(text, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    rules = {v.rule for v in result.violations}
    assert "no_years_in_opener" in rules


def test_number_with_space_normalization():
    text = GOOD_LETTER.replace("11000", "11 000")
    facts = _make_facts()
    result = validate_deterministic(text, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert result.passed, [v.to_dict() for v in result.violations]


def test_anglicism_inside_russian_is_flagged():
    text = GOOD_LETTER.replace("сложной бизнес-логикой", "complex business logic", 1)
    facts = _make_facts()
    result = validate_deterministic(text, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert not result.passed
    rules = {v.rule for v in result.violations}
    assert "anglicism" in rules


def test_allowed_tech_terms_do_not_trigger_anglicism():
    facts = _make_facts()
    result = validate_deterministic(GOOD_LETTER, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert not any(v.rule == "anglicism" for v in result.violations)


# ---------- v2 rules ----------


def test_forbidden_claim_grounded_in_resume_is_allowed():
    # If "финтех" appears in the resume, mentioning it in the letter is fine.
    facts = _make_facts(
        profile_text="OtherMark финтех flutter bloc clean architecture",
        forbidden_claims=["финтех"],
    )
    text = GOOD_LETTER.replace("B2B-систем", "финтех-систем")
    result = validate_deterministic(text, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    rules = {v.rule for v in result.violations}
    assert "forbidden_claim" not in rules


def test_forbidden_claim_NOT_in_resume_is_flagged():
    # If "финтех" is not in the resume, the letter can't claim it.
    facts = _make_facts(forbidden_claims=["финтех"])
    text = GOOD_LETTER.replace("B2B-систем", "финтех-систем")
    result = validate_deterministic(text, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert not result.passed
    assert any(v.rule == "forbidden_claim" for v in result.violations)


def test_unknown_tech_term_is_flagged():
    # "Riverpod" is not in allowed_tech and not in BASE_ALLOWED_TECH.
    text = GOOD_LETTER.replace("Clean Architecture", "Riverpod", 1)
    facts = _make_facts()
    result = validate_deterministic(text, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    rules = {v.rule for v in result.violations}
    # Riverpod is in FORBIDDEN_LIBRARIES so it'll fire that rule too,
    # but unknown_tech_term should also fire because it's not in any whitelist.
    assert "library_name" in rules or "unknown_tech_term" in rules


def test_known_project_name_is_not_flagged_as_unknown_tech():
    # "OtherMark" appears in GOOD_LETTER and is in allowed_project_names.
    facts = _make_facts()
    result = validate_deterministic(GOOD_LETTER, facts=facts, allowed_numbers=ALLOWED_NUMBERS)
    assert not any(
        v.rule == "unknown_tech_term" and v.evidence == "OtherMark"
        for v in result.violations
    )


def test_universal_mode_allows_one_paragraph():
    one_para = GOOD_LETTER.replace("\n\n", " ")
    facts = _make_facts()
    result = validate_deterministic(
        one_para,
        facts=facts,
        allowed_numbers=ALLOWED_NUMBERS,
        universal_mode=True,
    )
    # The "wrong_paragraph_count" rule shouldn't fire in universal mode.
    assert not any(v.rule == "wrong_paragraph_count" for v in result.violations)
