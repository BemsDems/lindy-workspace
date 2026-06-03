"""Tests for the deterministic post-processor.

These guarantee the form-level contract holds regardless of model output:
- the reference letter passes through untouched,
- stack dumps / signatures / weak endings / meta lines are removed,
- vплетённые (inline) technologies are preserved (not mistaken for a dump),
- decimals and version numbers survive sentence splitting.
"""

from src.postprocess import (
    postprocess_letter,
    strip_stack_dump,
    strip_weak_ending,
    strip_meta_and_signature,
    normalize_version_numbers,
    _word_count,
)


REFERENCE = (
    "Меня заинтересовала вакансия Flutter-разработчика в ORB IT, так как она "
    "требует опыта работы с корпоративными системами и интеграциями. В OtherMark "
    "я проектировал архитектуру ERP-системы на Clean Architecture и реализовывал "
    "5 ключевых модулей для управления производственными процессами. Для обмена "
    "данными использовал gRPC-интеграцию, что упростило взаимодействие с backend.\n\n"
    "Этот опыт близок к задачам вашего проекта, где важны стабильность и работа с "
    "данными.\n\n"
    "В работе я использую BLoC и Clean Architecture, чтобы бизнес-логика оставалась "
    "понятной, а приложение было проще масштабировать и поддерживать."
)


def test_reference_passes_untouched():
    """The hand-written reference must survive byte-for-byte (modulo whitespace)."""
    out = postprocess_letter(REFERENCE)
    assert out == REFERENCE.strip()
    assert _word_count(out) == _word_count(REFERENCE)


def test_inline_tech_is_preserved():
    """Technologies woven into a sentence are NOT a stack dump — keep them."""
    text = "Использовал gRPC-интеграцию на Clean Architecture, что упростило backend."
    out = strip_stack_dump(text)
    assert "gRPC" in out and "Clean Architecture" in out


def test_stack_dump_lead_is_removed():
    text = (
        "Переработал auth модуль. "
        "Стек: Flutter, Dart, Clean Architecture, Dio, REST API, gRPC, Protobuf, JWT."
    )
    out = strip_stack_dump(text)
    assert "Стек" not in out
    assert "Protobuf" not in out
    assert "Переработал auth модуль" in out


def test_stack_dump_without_lead_is_removed():
    text = (
        "Работал с Flutter, Dart, Clean Architecture, Auto Route, Dio, REST API, gRPC. "
        "Переработал auth модуль."
    )
    out = strip_stack_dump(text)
    assert "Auto Route" not in out
    assert "Переработал auth модуль" in out


def test_weak_ending_is_removed():
    """The boilerplate closing is dropped — but only when enough real content
    remains (the rollback guard intentionally keeps stub letters intact)."""
    text = (
        "Решал задачи стабильности в платёжном приложении. "
        "Декомпозировал крупные экраны на переиспользуемые компоненты. "
        "Готов применить этот опыт для выпуска карт."
    )
    out = strip_weak_ending(text)
    assert "Готов применить" not in out
    assert "Решал задачи стабильности" in out


def test_signature_is_removed():
    text = "Текст письма здесь.\n\nС уважением,\nДуков Тамерлан"
    out = strip_meta_and_signature(text)
    assert "уважением" not in out.lower()
    assert "Текст письма здесь" in out


def test_meta_prefix_is_removed():
    text = "Вот письмо для вас:\n\n3+ года Flutter-разработки. Переработал auth модуль."
    out = postprocess_letter(text)
    assert not out.lower().startswith("вот")
    assert "письмо для вас" not in out.lower()
    assert "3+ года" in out


def test_decimal_survives_split():
    """A decimal such as 99.5% must not be split into a new sentence."""
    text = (
        "Поднял стабильность с 92% до 99.5% в проде. "
        "Декомпозировал крупные экраны на компоненты. "
        "Готов применить опыт."
    )
    out = postprocess_letter(text)
    assert "99.5%" in out
    assert "Готов" not in out


def test_verb_led_pure_list_is_removed():
    """A pure trailing tech list (no narrative) IS a dump and gets removed.

    NOTE: a list *followed by real narrative/metrics* is intentionally
    preserved — see test_list_with_narrative_is_preserved — because stripping
    it would destroy genuine achievement sentences."""
    text = (
        "3+ года Flutter-разработки. "
        "Работал с Dio, REST API, gRPC, Protobuf, Drift, Secure Storage. "
        "Переработал auth модуль."
    )
    out = strip_stack_dump(text)
    assert "Protobuf" not in out
    assert "Secure Storage" not in out
    assert "Переработал auth модуль" in out


def test_list_with_narrative_is_preserved():
    """A tech list that is part of a sentence carrying real narrative/metrics
    must be preserved (offline-verified against a real generated letter)."""
    text = (
        "Работал с Firebase Auth, FCM и Dio в приложении доставки еды — "
        "интегрировал push-уведомления и подготовил 13+ релизов в сторах."
    )
    out = strip_stack_dump(text)
    assert "Firebase Auth" in out
    assert "13+ релизов" in out


def test_inline_multi_tech_prose_is_preserved():
    """Real case: a normal achievement sentence that names a few technologies
    inline must NOT be treated as a dump."""
    text = (
        "Переработал profile-модуль с поддержкой VK ID и Yandex ID, "
        "интегрировал Deep Links и Branch SDK для кросс-платформенной навигации."
    )
    out = strip_stack_dump(text)
    assert "profile-модуль" in out
    assert "Deep Links" in out


def test_reference_third_paragraph_survives():
    """The 'use X to Y' closing from the reference must never be cut."""
    text = (
        "В работе я использую BLoC и Clean Architecture, чтобы бизнес-логика "
        "оставалась понятной, а приложение было проще масштабировать."
    )
    assert strip_stack_dump(text).strip() == text.strip()


def test_punctuation_artifacts_are_cleaned():
    """Double dashes / orphaned separators left by fragment removal are fixed."""
    from src.postprocess import normalize_punctuation
    assert normalize_punctuation("авторизации —  — включая") == "авторизации — включая"
    assert normalize_punctuation("текст , запятая .") == "текст, запятая."
    assert normalize_punctuation("двойная,, запятая") == "двойная, запятая"
    assert normalize_punctuation("тире перед точкой —.") == "тире перед точкой."
    assert normalize_punctuation("пустые скобки () тут") == "пустые скобки тут"


def test_empty_input():
    assert postprocess_letter("") == ""


# --- Version-number normalization (3. 22. 2 -> 3.22.2) ---------------------


def test_spaced_version_is_normalized():
    assert normalize_version_numbers("Flutter 3. 22. 2 обновил") == "Flutter 3.22.2 обновил"
    assert normalize_version_numbers("3. 0. 2") == "3.0.2"


def test_spaced_version_in_migration_arrow():
    text = "3.0.2 → 3. 22. 2 → 3.29.0"
    assert normalize_version_numbers(text) == "3.0.2 → 3.22.2 → 3.29.0"


def test_clean_version_is_unchanged():
    text = "Миграция Flutter 3.22.2 → 3.29.0 прошла гладко."
    assert normalize_version_numbers(text) == text


def test_two_part_decimal_not_touched():
    assert normalize_version_numbers("рост до 99.5%") == "рост до 99.5%"


def test_sentence_boundary_not_merged():
    s = "Завершил 5 модулей. 3 из них переписал заново."
    assert normalize_version_numbers(s) == s


def test_version_fix_via_postprocess():
    text = "Провёл миграцию Flutter 3.0.2 → 3. 22. 2 → 3.29.0 без проблем."
    out = postprocess_letter(text)
    assert "3. 22. 2" not in out
    assert "3.22.2" in out
    assert "3.0.2" in out and "3.29.0" in out
