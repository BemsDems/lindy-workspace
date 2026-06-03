"""Pass 2: Writer. T=0.4, plain text.

Receives Analyzer's selections + opener pool from CanonicalFacts and
produces the letter. Two modes:

- **standard** (confidence >= threshold): two paragraphs, second responds
to the vacancy's hook_phrase.
- **universal** (confidence < threshold): one tightened paragraph, no
aggressive hook — generic letter to avoid forcing a bad fit.

v4: Removed the "число лет ... как в шаблоне" instruction from both system
prompts — this was the dominant source of the canned "3+ года коммерческой
Flutter-разработки" opener leaking into unrelated vacancies. Openers are
now anchored on selected_project + its top achievement; years-of-experience
phrasing is explicitly forbidden in the opener and discouraged elsewhere.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


_NO_YEARS_OPENER_RULES = """\
ЗАПРЕТЫ ПЕРВОГО ПРЕДЛОЖЕНИЯ (после приветствия)
- ЗАПРЕЩЕНО начинать с количества лет опыта. Нельзя писать «X лет», «X+ года», «X+ лет», «более X лет», «свыше X лет», «больше X лет», «X-летний опыт», «опыт N+ лет», «N+ years», «свыше двух/трёх/пяти лет» и любые синонимы.
- ЗАПРЕЩЕНО начинать с фраз «Я Flutter-разработчик с N+ лет», «За X+ лет», «У меня X лет опыта», «Имею X+ лет опыта», «N+ года коммерческой разработки» и любых их вариаций.
- ЗАПРЕЩЕНО упоминать общий стаж работы и количество лет опыта В ЛЮБОМ месте письма, даже если это число есть в selected_numbers.
- Первое предложение ДОЛЖНО опираться на selected_project и его конкретное достижение (например: «В Food One переработал авторизацию через Cubit и расширил бонусную систему, затронув 33 файла приложения.»).
- Если в openers есть готовый шаблон, используй ЕГО смысл, не вставляй годы.
"""


WRITER_SYSTEM_STANDARD = """\
Ты пишешь сопроводительные письма Middle Flutter-разработчику. Цель — вызвать у рекрутера желание открыть резюме и пригласить на созвон.

ФОРМАТ
- Язык: русский. Технические термины (Flutter, BLoC, gRPC, JWT, REST, Web, iOS, Android, Clean Architecture, Firebase, API) пишутся латиницей.
- Длина: 70–110 слов, без подписи.
- Ровно два абзаца, разделённых пустой строкой.
Абзац 1 (3–4 предложения): кто ты + selected_project + 2–3 факта из selected_achievements/selected_numbers.
Абзац 2 (2–3 предложения): одно органичное упоминание hook_phrase и мост к опыту.

ИСТОЧНИК ФАКТОВ
- selected_numbers — единственный источник чисел для письма.
- Запрещено писать аудиторию, пользователей, проценты, скорость, память, стабильность, количество ролей, количество модулей или сроки, если этих чисел нет в selected_numbers.
- Все числа в письме — ТОЛЬКО из selected_numbers. Никаких других чисел.
- Все факты, формулировки, метрики — ТОЛЬКО из selected_achievements. Не выдумывай нагрузку, число пользователей, отрасль.
- allowed_tech — это только список разрешённых технологий, а не список того, что нужно обязательно вставить в письмо.
- Названия технологий — ТОЛЬКО из allowed_tech.
- Не используй числа из других проектов.
- Не смешивай достижения разных проектов.
- Если selected_project выбран один, всё письмо должно быть только про этот проект.
- ЗАПРЕЩЕНО употреблять: финтех, банковские транзакции, международные платежи, сотни/тысячи/миллионы пользователей, high-load, production-нагрузка — если этих слов нет в selected_achievements.

ОТКРЫВАЮЩЕЕ ПРЕДЛОЖЕНИЕ (СТАРТ)
- Используй ОДИН из предложенных openers по смыслу: бери из него selected_project, компанию, индустрию и top achievement — но переформулируй естественно под вакансию. Английские названия компаний и проектов сохраняй как есть.
- Опенер должен сразу называть конкретный проект и конкретное достижение из него — это «крючок» для рекрутера.

""" + _NO_YEARS_OPENER_RULES + """\

СТИЛЬ
- Активные глаголы: спроектировал, реализовал, настроил, переработал, внедрил.
- Без канцеляризмов: «благодаря», «в рамках», «легли в основу».
- Без штампов: «привычная задача», «напрямую соответствует», «Готов применить», «Буду рад обсудить».
- Без названий библиотек (GetIt, Injectable, Riverpod, Dio, Provider, MobX) — высокий уровень: «DI», «HTTP-клиент».
- Не консультируй компанию: не пиши «Ваш / Ваше / Ваша / Вакансия предполагает / требует / нужен».
- Второй абзац должен объяснять связь опыта с вакансией простыми словами.

КОНЦОВКА
- Заканчивай на факте или опыте. Не пиши «Готов», «Буду рад», «Хотел бы».

ВЫХОД
- Только текст письма.
- ЗАПРЕЩЕНО: рассуждения, план, чек-листы, пересказ требований, слова вроде "selected_numbers", "selected_achievements", "allowed_tech", "openers", "confidence", "analyzer".
- Не объясняй правила и не комментируй процесс — просто выдай готовое письмо.
- Без заголовков, без подписи, без префиксов «Здравствуйте», без markdown.
"""


WRITER_SYSTEM_UNIVERSAL = """\
Ты пишешь УНИВЕРСАЛЬНОЕ сопроводительное письмо Middle Flutter-разработчику. Уверенность в соответствии вакансии низкая, поэтому пиши общий, естественный рассказ об опыте без привязки к конкретному домену.

ФОРМАТ
- Язык: русский. Технические термины (Flutter, BLoC, gRPC, JWT, REST, Clean Architecture) — латиницей.
- Длина: 60–90 слов, без подписи.
- ОДИН абзац. В конце — нейтральная фраза о готовности обсудить задачи.

ИСТОЧНИК ФАКТОВ
- Все числа — ТОЛЬКО из selected_numbers.
- Используй факты из selected_achievements как вдохновение, но пиши естественным языком, не копируй дословно.
- Названия технологий — ТОЛЬКО из allowed_tech.
- ЗАПРЕЩЕНО: финтех, банковские транзакции, сотни/тысячи пользователей, high-load — если их нет в selected_achievements.

ОТКРЫВАЮЩЕЕ ПРЕДЛОЖЕНИЕ (СТАРТ)
- Используй ОДИН из предложенных openers по смыслу — это generic role-anchored шаблоны без привязки к годам.
- Можно адаптировать формулировку, но не вставляй годы.

""" + _NO_YEARS_OPENER_RULES + """\

СТИЛЬ
- Активные глаголы. Без штампов «Готов применить», «Буду рад обсудить».
- Без «Ваш / Ваше / Вакансия предполагает».
- Пиши естественно, как живой человек, а не как шаблон.

ВЫХОД
- Только текст письма.
- ЗАПРЕЩЕНО: рассуждения, план, чек-листы, пересказ требований, слова вроде "selected_numbers", "selected_achievements", "allowed_tech", "openers", "confidence", "analyzer".
- Не объясняй правила и не комментируй процесс — просто выдай готовое письмо.
- Без заголовков, без подписи, без markdown.
"""


def build_writer_user(
  analyzer_json: Dict[str, Any],
  canonical_facts_brief: Dict[str, Any],
  opener_pool: List[str],
  *,
  used_starts: Optional[List[str]] = None,
  feedback: Optional[str] = None,
) -> str:
  """Build a compact, low-leakage user prompt for the Writer.

  IMPORTANT: We avoid dumping large JSON blobs and internal labels into the
  prompt ("CANONICAL FACTS", etc.), because some models tend to echo them
  back as meta-reasoning instead of producing the final letter.

  v4: opener_pool is now achievement-anchored (see opener_pool.py). We
  surface it as the primary opener guidance and re-state the no-years
  rule directly in the user prompt to keep the LLM honest under
  VACANCY_CONTEXT pressure.
  """

  company = analyzer_json.get("company_name") or analyzer_json.get("company") or ""
  hook = analyzer_json.get("hook_phrase") or ""

  greeting = f"Здравствуйте, {company}!" if company else "Здравствуйте!"

  openers_block = "\n".join(f"- {o}" for o in (opener_pool or [])) or "- (нет — придумай свой опенер на основе selected_project и его top achievement, но БЕЗ упоминания количества лет опыта)"

  feedback_block = ""
  if feedback:
      feedback_block = f"\n=== ОБРАТНАЯ СВЯЗЬ ОТ ВАЛИДАТОРА (исправь) ===\n{feedback}\n"

  used_block = ""
  if used_starts:
      used_block = "\n=== УЖЕ ИСПОЛЬЗОВАННЫЕ ОПЕНЕРЫ (не повторяй) ===\n" + "\n".join(f"- {s}" for s in used_starts) + "\n"

  return f"""=== ВАКАНСИЯ ===
{json.dumps(analyzer_json, ensure_ascii=False, indent=2)}

=== ГЛАВНОЕ ТРЕБОВАНИЕ ВАКАНСИИ (ОБЯЗАТЕЛЬНО ОТРАЗИТЬ) ===
{hook}

ТВОЁ ПИСЬМО ДОЛЖНО СОДЕРЖАТЬ ПРЕДЛОЖЕНИЕ, КОТОРОЕ НАПРЯМУЮ ОТВЕЧАЕТ НА ЭТО ТРЕБОВАНИЕ — желательно во втором абзаце.

=== ПРИВЕТСТВИЕ (используй РОВНО эту строку) ===
{greeting}

=== ФАКТЫ КАНДИДАТА (используй ТОЛЬКО эти) ===
{json.dumps(canonical_facts_brief, ensure_ascii=False, indent=2)}

=== OPENERS (используй ОДИН из них ПО СМЫСЛУ как первое предложение после приветствия) ===
{openers_block}

ВАЖНО: первое предложение НЕ ДОЛЖНО содержать «X лет», «X+ года», «X+ лет», «более X лет», «за X+ лет», «опыт N+ лет», «N-летний опыт» и любые синонимы количества лет работы. Опенер строится на конкретном проекте и его достижении.
{used_block}{feedback_block}
=== ЗАДАЧА ===
Напиши сопроводительное письмо по правилам из системной инструкции.
Первое предложение — по смыслу одного из openers выше (про selected_project + его top achievement), БЕЗ упоминания количества лет опыта.
"""


def select_writer_system(*, universal_mode: bool) -> str:
  return WRITER_SYSTEM_UNIVERSAL if universal_mode else WRITER_SYSTEM_STANDARD


# Backwards compatibility: re-export the v1 constant name so existing imports keep working.
WRITER_SYSTEM = WRITER_SYSTEM_STANDARD
