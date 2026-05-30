"""Tests for the deterministic post-processor.

These guarantee the form-level contract holds regardless of model output:
- the reference letter passes through untouched,
- stack dumps / signatures / weak endings / meta lines are removed,
- vplетённые (inline) technologies are preserved (not mistaken for a dump),
- decimals survive sentence splitting.
"""

from src.postprocess import (
    postprocess_letter,
    strip_stack_dump,
    strip_weak_ending,
    strip_meta_and_signature,
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
    text = "Решал задачи стабильности. Готов применить этот опыт для выпуска карт."
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
    text = "Поднял стабильность с 92% до 99.5%. Готов применить опыт."
    out = postprocess_letter(text)
    assert "99.5%" in out
    assert "Готов" not in out


def test_verb_led_dump_with_connectors_is_removed():
    """Real case: 'Работал с A, B/C, D, REST API, gRPC, … и Secure Storage' —
    a dump glued by commas/slashes/'и' that the comma regex alone missed."""
    text = (
        "3+ года Flutter-разработки. "
        "Работал с Clean Architecture, BLoC/Cubit, Dio, REST API, gRPC, Protobuf, "
        "Drift и Secure Storage — строил приложения от навигации до аналитики. "
        "Переработал auth модуль."
    )
    out = strip_stack_dump(text)
    assert "Protobuf" not in out
    assert "Secure Storage" not in out
    assert "Переработал auth модуль" in out


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
