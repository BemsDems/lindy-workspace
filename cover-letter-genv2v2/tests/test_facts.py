"""Tests for the canonical-facts extractor."""

from __future__ import annotations

from src.facts import extract_canonical_facts
from src.models import Position, Profile, Project


def _profile() -> Profile:
    return Profile(
        name="Иван Иванов",
        experience_years=3,
        experience_months=2,
        summary="Flutter-разработчик с опытом B2B-систем.",
        skills_primary=["Flutter", "Dart", "BLoC", "Clean Architecture"],
        skills_secondary=["Firebase", "JWT"],
        positions=[
            Position(
                title="Flutter-разработчик",
                company="OtherCode",
                industry="ERP",
                projects=[
                    Project(
                        name="OtherMark",
                        description="B2B/ERP-система для маркировки.",
                        tech_stack=["Flutter", "BLoC", "Clean Architecture", "gRPC"],
                        achievements=[
                            "Спроектировал архитектуру с DI.",
                            "Реализовал 5 B2B-модулей.",
                            "Кодовая база превысила 11 000 строк.",
                        ],
                    ),
                    Project(
                        name="DIOM",
                        description="Финтех-приложение.",
                        tech_stack=["Flutter", "JWT", "Secure Storage"],
                        achievements=[
                            "Реализовал 4 способа входа и систему прав для 6 ролей.",
                            "20 переиспользуемых UI-компонентов.",
                        ],
                    ),
                ],
            ),
        ],
    )


def test_extract_canonical_facts_collects_numbers_globally():
    facts = extract_canonical_facts(_profile())
    nums = set(facts.allowed_numbers)
    # 3 (years), 5, 11000, 4, 6, 20
    assert nums.issuperset({"3", "5", "11000", "4", "6", "20"})


def test_extract_canonical_facts_collects_tech_globally():
    facts = extract_canonical_facts(_profile())
    assert "Flutter" in facts.allowed_tech
    assert "JWT" in facts.allowed_tech
    assert "gRPC" in facts.allowed_tech
    assert "Firebase" in facts.allowed_tech  # from secondary skills


def test_extract_canonical_facts_per_project():
    facts = extract_canonical_facts(_profile())
    om = facts.project("OtherMark")
    diom = facts.project("DIOM")
    assert om is not None and diom is not None
    assert set(om.allowed_numbers) == {"5", "11000"}
    assert set(diom.allowed_numbers) == {"4", "6", "20"}


def test_forbidden_claims_grounded_filters_out_resume_words():
    p = _profile()
    facts = extract_canonical_facts(
        p,
        forbidden_claims=["финтех", "high-load", "сотни пользователей"],
    )
    grounded = facts.forbidden_claims_grounded()
    # "финтех" appears in DIOM.description → it's NOT forbidden.
    assert "финтех" not in {g.lower() for g in grounded}
    # "high-load" and "сотни пользователей" don't appear in profile → forbidden.
    assert "high-load" in grounded
    assert "сотни пользователей" in grounded


def test_default_forbidden_claims_used_when_none_passed():
    facts = extract_canonical_facts(_profile())
    # Default list is non-empty and contains "финтех" — but profile has "финтех",
    # so it's filtered from grounded.
    assert len(facts.forbidden_claims) > 0
    grounded = facts.forbidden_claims_grounded()
    assert "финтех" not in {g.lower() for g in grounded}
    assert "high-load" in grounded
