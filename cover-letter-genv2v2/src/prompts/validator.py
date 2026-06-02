"""Pass 3: Validator (semantic). T=0, JSON.

Hard rules (length, forbidden phrases, numeric whitelist) are enforced
deterministically in `src/validator.py`. This LLM pass only catches the
soft, semantic violations a regex can't see:

- whether the hook_phrase is actually addressed,
- whether the letter implicitly invents domain expertise,
- whether the second paragraph reads as advice to the company,
- whether the vacancy's domain is reflected in the letter.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


VALIDATOR_SYSTEM = """\
Ты — строгий редактор. Проверь сопроводительное письмо ТОЛЬКО на семантические нарушения и верни ОДИН JSON-объект.

Схема:
{
"passed": boolean,
"violations": [
  { "rule": string, "severity": "hard" | "soft", "evidence": string, "fix_hint": string }
]
}

Каждое нарушение ОБЯЗАНО иметь поле severity. Если не уверен — ставь "hard" для правил 1–3 и 11, "soft" для остальных.

Семантические правила:

1. invented_facts [severity=hard] — есть ли в письме факты или домены, которых НЕТ ни в analyzer_json, ни в резюме (например "платёжные сервисы", "сотни пользователей", "production-нагрузка", если их нет в данных).

2. advice_to_company [severity=hard] — второй абзац звучит как совет компании ("Вам нужен", "Ваш продукт требует", "Вакансия предполагает"), а не как факт о кандидате.

3. hook_not_addressed [severity=hard] — hook_phrase из analyzer_json должен быть РАСКРЫТ в письме: либо явно отражён в первом абзаце через формулировку роли/опыта кандидата, либо во втором абзаце через релевантное достижение, подтверждающее именно ту способность, на которую указывает hook. Простое случайное упоминание ключевого слова без раскрытия — это нарушение. Перефразирование разрешено, но смысл hook должен явно прозвучать.

4. weak_ending [severity=soft] — последнее предложение слабое ("Готов применить", "Буду рад обсудить", "Хотел бы"). Soft, потому что детерминированный постпроцессор уже срезает такие финалы.

5. tone_consulting [severity=hard] — кандидат пишет так, будто консультирует компанию, а не претендует на роль. Особенно опасно в hook closure: фраза должна звучать как роль кандидата, не как комплимент компании.

6. years_in_opener_allowed [НЕ нарушение] — упоминание 3+ года опыта в первых двух предложениях разрешено и НЕ должно попадать в violations.

7. generic_opener [severity=soft] — первые два предложения содержат шаблонные фразы ("Я узнал о вакансии на сайте компании", "Меня заинтересовала ваша вакансия").

8. cliches [severity=soft] — в письме используются клише: "ответственный", "командный игрок", "результат-ориентированный", "быстрая обучаемость".

9. tech_list_overload [severity=soft] — перечислено более 3 технологий подряд или в одном предложении.

10. no_concrete_facts [severity=hard] — в первом абзаце отсутствуют конкретные факты (достижения, проекты, метрики).

11. vacancy_domain_not_addressed [severity=hard] — домен вакансии (analyzer_json.vacancy_domain) НЕ упомянут лексически в письме (через прямое название домена, синонимы или явные доменно-специфичные термины). Достаточно одного явного упоминания. Не считаются упоминанием: общее слово "продукт", "сервис", "приложение" без указания индустрии. Исключение: если vacancy_domain == "общий продукт" — это правило НЕ применяется. Примеры приемлемых упоминаний:
 - vacancy_domain = "EdTech" → "образование", "обучение", "ученики", "курс", "студенты", "EdTech"
 - vacancy_domain = "финтех" → "финтех", "финансы", "платежи", "банк", "финансовые сервисы"
 - vacancy_domain = "видео и DRM" → "видео", "стриминг", "DRM", "контент"
 - vacancy_domain = "еда и доставка" → "доставка", "еда", "заказ", "ресторан"
 - vacancy_domain = "медицина" → "медицина", "здоровье", "клиника", "пациент", "MedTech"

Не проверяй длину, числа, англицизмы и список запрещённых слов — это делает отдельный детерминированный валидатор.

passed = true только если в violations нет ни одного нарушения с severity = "hard".
Ответ — ОДИН JSON-объект, без markdown, без префиксов.
"""


def build_validator_user(
  letter_text: str,
  analyzer_json: Dict[str, Any],
  allowed_numbers: List[str],
) -> str:
  return (
      f"allowed_numbers: {json.dumps(allowed_numbers, ensure_ascii=False)}\n\n"
      f"analyzer_json: {json.dumps(analyzer_json, ensure_ascii=False)}\n\n"
      "ПИСЬМО:\n"
      "---\n"
      f"{letter_text.strip()}\n"
      "---\n\n"
      "Верни JSON."
  )
