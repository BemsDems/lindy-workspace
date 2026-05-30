"""Pass 2: Analyzer JSON + CanonicalFacts -> letter text."""

from __future__ import annotations
import re

import json

from typing import Any, Dict, List, Optional

from .facts import CanonicalFacts
from .llm_client import LLMClient
from .prompts.opener_pool import select_openers
from .prompts.writer import build_writer_user, select_writer_system


def _extend_selected_numbers_with_allowed_flutter_versions(
 selected_numbers: list,
 selected_achievements: list,
) -> list:
 """Allow real Flutter migration versions when they are present in selected achievements."""
 result = [str(item) for item in (selected_numbers or [])]
 achievements_text = "
".join(str(item) for item in (selected_achievements or []))

 for version in ("3.0.2", "3.29.0"):
     if version in achievements_text and version not in result:
         result.append(version)

 return result


def _restrict_allowed_tech_for_letter(
 *,
 global_allowed_tech: list,
 selected_project_tech: list,
 selected_achievements: list,
) -> list:
 """Use only evidence-level tech for this letter, not the whole resume stack."""
 achievements_text = "
".join(str(item).lower() for item in (selected_achievements or []))
 allowed: list[str] = []

 def add(value: object) -> None:
     item = str(value).strip()
     if item and item not in allowed:
         allowed.append(item)

 # Always safe for this generator.
 for tech in ("Flutter", "Dart"):
     add(tech)

 # Add technology only if it appears in selected achievements.
 for tech in list(selected_project_tech or []) + list(global_allowed_tech or []):
     tech_text = str(tech).strip()
     if not tech_text:
         continue

     tech_lower = tech_text.lower()

     if tech_lower in achievements_text:
         add(tech_text)

 return sorted(allowed, key=lambda item: item.lower())

def build_canonical_facts_brief(
 facts: CanonicalFacts, selected_project: str
) -> Dict[str, Any]:
 """Compact dict of facts handed to the Writer - only what's needed."""
 proj = facts.project(selected_project)
 return {
     "candidate_name": facts.candidate_name,
     "selected_project_name": proj.name if proj else selected_project,
     "selected_project_company": proj.company if proj else "",
     "selected_project_industry": proj.industry if proj else "",
     "selected_project_description": proj.description if proj else "",
     "selected_project_tech": list(proj.tech_stack) if proj else [],
     "allowed_tech": sorted(facts.allowed_tech),
 }


def _normalize_metric(value: object) -> str:
 text = str(value).lower().replace(",", ".")
 text = re.sub(r"\s+", "", text)
 return text


def _allowed_metric_tokens(analyzer_json: Dict[str, Any]) -> set[str]:
 selected_numbers = analyzer_json.get("selected_numbers") or []
 tokens: set[str] = set()

 for item in selected_numbers:
     item_text = str(item)

     for number in re.findall(r"\d+(?:[.,]\d+)?\s*(?:%|\+|млн|тыс|тысяч)?", item_text.lower()):
         tokens.add(_normalize_metric(number))

 tokens.add("3")
 tokens.add("3+")

 return tokens


def _remove_unapproved_metric_sentences(text: str, analyzer_json: Dict[str, Any]) -> str:
 allowed = _allowed_metric_tokens(analyzer_json)
 paragraphs = text.split("

")
 cleaned_paragraphs: list[str] = []

 for paragraph in paragraphs:
     sentences = re.split(r"(?<=[.!?])\s+", paragraph.strip())
     cleaned_sentences: list[str] = []

     for sentence in sentences:
         numbers = re.findall(r"\d+(?:[.,]\d+)?\s*(?:%|\+|млн|тыс|тысяч)?", sentence.lower())

         if not numbers:
             cleaned_sentences.append(sentence)
             continue

         if "3+ года" in sentence or "3 года" in sentence:
             cleaned_sentences.append(sentence)
             continue

         normalized_numbers = {_normalize_metric(number) for number in numbers}

         if normalized_numbers.issubset(allowed):
             cleaned_sentences.append(sentence)

     cleaned_paragraph = " ".join(item for item in cleaned_sentences if item).strip()

     if cleaned_paragraph:
         cleaned_paragraphs.append(cleaned_paragraph)

 return "

".join(cleaned_paragraphs).strip()


async def write_letter(
 llm: LLMClient,
 analyzer_json: Dict[str, Any],
 facts: CanonicalFacts,
 *,
 used_starts: Optional[List[str]] = None,
 feedback: Optional[str] = None,
 universal_mode: bool = False,
 temperature: float = 0.4,
 max_tokens: int = 400,
 two_pass_editing: bool = False,
) -> str:
 system_prompt = select_writer_system(universal_mode=universal_mode)
 selected_project = str(analyzer_json.get("selected_project") or "")
 brief = build_canonical_facts_brief(facts, selected_project)
 opener_pool = select_openers(facts.experience_years, used_starts or [], n=2)

 # Generate the final letter FROM FACTS ONLY.
 final_text = await _final_letter_from_facts(
     llm,
     analyzer_json=analyzer_json,
     canonical_facts_brief=brief,
     opener_pool=opener_pool,
     universal_mode=universal_mode,
     feedback=feedback,
     max_tokens=max_tokens,
 )
 return _strip_signature_lines(final_text)


CLEANER_SYSTEM = """\
Ты редактор сопроводительных писем. Твоя задача - превратить черновик в ГОТОВОЕ письмо.

ВХОД
Черновик может содержать:
- рассуждения ("нужно", "проверяю", "сначала", "план", "структура"),
- списки и пункты,
- служебные слова и внутренние термины пайплайна,
- обрывы текста.

ВЫХОД (КРИТИЧНО)
- Верни ТОЛЬКО финальный текст сопроводительного письма.
- Никаких пояснений, никаких вступлений про пользователя/задачу, никаких "собираю текст".
- Если в черновике нет готового письма (или оно обрывается) - НАПИШИ письмо заново, используя только факты, явно присутствующие в черновике.

ЗАПРЕЩЕНО (удаляй и не добавляй)
- любые мета-слова и рассуждения: "пользователь", "черновик", "формат", "ограничения", "проверяю", "анализирую", "план", "структура".
- любые внутренние термины: selected_numbers, selected_achievements, allowed_tech, openers, confidence, analyzer, canonical facts.
- любые списки (маркированные/нумерованные), заголовки, markdown.
- подпись и любые строки начиная с "С уважением".

СТИЛЬ
- Русский язык, тех. термины латиницей.
- Письмо должно читаться естественно.
"""


def _cleaner_user(draft: str, *, universal_mode: bool) -> str:
 mode = "1-2 плотных абзаца" if universal_mode else "2-3 абзаца, разделённых пустой строкой"
 return (
     f"Режим: {mode}.
"
     "Перепиши черновик в чистое сопроводительное письмо по режиму выше.

"
     "ЧЕРНОВИК:
"
     f"{draft.strip()}
"
 )


FINALIZER_SYSTEM = """Ты пишешь сопроводительное письмо от имени Flutter-разработчика для российского рынка (HH.ru, корпоративная почта, Telegram).

=== АДРЕСАТ ===
Российский HR или тимлид. Читает 50+ откликов в день. Шаблон отличает за 5 секунд.

=== ЖЁСТКИЕ ПРАВИЛА ===

1. ВСЕГДА начинай с приветствия. Используй ровно ту строку приветствия, которая передана в user-промпте в секции «ПРИВЕТСТВИЕ». Без приветствия = провал.

2. НИКОГДА не начинай первое содержательное предложение со слов:
- «X+ лет опыта…»
- «X+ года коммерческой разработки…»
- «Меня заинтересовала ваша вакансия…»
- «Я узнал о вакансии…»
- «Хочу предложить свою кандидатуру…»
Это маркеры автоматизации.

3. НИКОГДА не заканчивай шаблонными фразами:
- «Готов обсудить задачи и подробнее рассказать о релевантном опыте на собеседовании.»
- «Буду рад обсудить.»
- «Хотел бы…»
Используй живой финал (см. примеры ниже).

4. Подпись ВСЕГДА: «Тамерлан Дуков» (Имя Фамилия, не наоборот).

5. Структура: минимум 2 абзаца. Короче - выглядит как middle-отклик.

=== ЗАПРЕЩЁННЫЕ КЛИШЕ ===
«ответственный», «командный игрок», «быстрая обучаемость», «результат-ориентированный», «стрессоустойчивый», «коммуникабельный».

=== 4 ШАБЛОНА СТРУКТУРЫ (выбери ОДИН под тип вакансии) ===

ШАБЛОН A - «Прямой ответ на hook» (вакансии с чётким требованием):
[Приветствие]. [Прямое утверждение: «У меня есть опыт именно в X», с конкретным примером]. [2–3 факта из релевантного проекта с числами].
[Связка с компанией: что заинтересовало в продукте/домене/задачах].
[Открытый финал: предложение созвона].

ШАБЛОН B - «История-кейс» (senior/TL вакансии):
[Приветствие]. [Короткая история: «Недавно делал X - задача была Y, решил так Z»]. [Результат с числами].
[Почему это релевантно вакансии].
[Открытый финал].

ШАБЛОН C - «Проблема → решение» (вакансии с описанием боли):
[Приветствие]. [Парафраз боли из вакансии: «Понимаю, насколько важно X»]. [Конкретный случай, когда решал такую же проблему: что было, что сделал, результат].
[Финал: что могу принести команде].

ШАБЛОН D - «Технический пинч» (технологически сложные вакансии):
[Приветствие]. [Сразу к делу: одно сильное техническое утверждение + контекст]. [Развёрнутое описание архитектурного/инженерного решения, конкретика].
[Короткая связка с компанией + финал].

=== ЖИВЫЕ ФИНАЛЫ (варьируй, не копируй один и тот же) ===
- «Если интересно - давайте 20-минутный созвон, расскажу детали по [проекту].»
- «Свободен на этой неделе после 17:00 - напишите, когда удобно.»
- «Готов показать архитектуру [проекта] на коротком созвоне.»
- «Если резюме откликается - давайте сразу созвонимся, без длинной переписки.»

=== ЧТО ОБЯЗАТЕЛЬНО ===
- Конкретные числа из allowed_numbers (минимум 1, максимум 3).
- Связка с компанией: минимум одно предложение про их продукт/домен/задачу из вакансии.
- Ответ на hook_phrase (см. секцию «ГЛАВНОЕ ТРЕБОВАНИЕ» в user-промпте).

=== ЧЕГО НЕ ДЕЛАТЬ ===
- Не перечисляй больше 3 технологий подряд.
- Не упоминай зарплату.
- Не используй списки и буллиты - это сплошной текст.
- Не выдумывай факты вне allowed_numbers / allowed_tech / описаний проектов.
- Не используй англицизмы без необходимости («pipeline», «hook», «scope»).
"""


def _finalizer_user(
 *,
 universal_mode: bool,
 selected_project: str,
 project_company: str,
 project_industry: str,
 project_description: str,
 hook_phrase: str,
 selected_numbers: List[str],
 selected_achievements: List[str],
 allowed_tech: List[str],
 openers: List[str],
 feedback: Optional[str] = None,
) -> str:
 mode = (
     "UNIVERSAL: 1 короткий абзац, 60–90 слов" if universal_mode
     else "STANDARD: 2 коротких абзаца, 70–110 слов"
 )
 parts: List[str] = [
     f"MODE: {mode}",
     "PROJECT: выбранный проект из резюме, название проекта не использовать в письме",
     "PROJECT_FACTS:",
     "- company: прошлую компанию не указывать в письме",
     f"- industry: {project_industry}",
     f"- description: {project_description}",
     f"HOOK: {hook_phrase}",
     f"SELECTED_NUMBERS: {json.dumps(selected_numbers, ensure_ascii=False)}",
     "ACHIEVEMENTS (разрешённые факты):",
 ]
 for a in selected_achievements:
     parts.append(f"- {a}")

 parts.append("")
 parts.append("ALLOWED_TECH (используй только эти термины):")
 for t in allowed_tech[:80]:
     parts.append(f"- {t}")

 parts.append("")
 parts.append("OPENERS (используй как смысл для первой фразы, но НЕ выноси отдельной строкой):")
 for o in openers:
     parts.append(f"- {o}")

 if feedback:
     parts.append("")
     parts.append("FEEDBACK ОТ ВАЛИДАТОРА:")
     parts.append(feedback)
     parts.append("Исправь это, но не упоминай feedback в письме.")

 parts.append("Напиши письмо по MODE. Верни только письмо, без комментариев.")
 return "
".join(parts)


async def _final_letter_from_facts(
 llm: LLMClient,
 *,
 analyzer_json: Dict[str, Any],
 canonical_facts_brief: Dict[str, Any],
 opener_pool: List[str],
 universal_mode: bool,
 feedback: Optional[str] = None,
 max_tokens: int = 400,
) -> str:
 selected_project = str(analyzer_json.get("selected_project") or "")
 hook_phrase = str(analyzer_json.get("hook_phrase") or "")
 project_company = str(canonical_facts_brief.get("selected_project_company") or "")
 project_industry = str(canonical_facts_brief.get("selected_project_industry") or "")
 project_description = str(canonical_facts_brief.get("selected_project_description") or "")
 selected_achievements = list(analyzer_json.get("selected_achievements") or [])
 selected_numbers = _extend_selected_numbers_with_allowed_flutter_versions(
     list(analyzer_json.get("selected_numbers") or []),
     selected_achievements,
 )
 selected_project_tech = list((canonical_facts_brief or {}).get("selected_project_tech") or [])
 global_allowed_tech = list((canonical_facts_brief or {}).get("allowed_tech") or [])
 allowed_tech = _restrict_allowed_tech_for_letter(
     global_allowed_tech=global_allowed_tech,
     selected_project_tech=selected_project_tech,
     selected_achievements=selected_achievements,
 )

 return await llm.generate(
     system_prompt=FINALIZER_SYSTEM,
     user_prompt=_finalizer_user(
         universal_mode=universal_mode,
         selected_project=selected_project,
         project_company=project_company,
         project_industry=project_industry,
         project_description=project_description,
         hook_phrase=hook_phrase,
         selected_numbers=selected_numbers,
         selected_achievements=selected_achievements,
         allowed_tech=allowed_tech,
         openers=list(opener_pool),
         feedback=feedback,
     ),
     temperature=0.35,
     max_tokens=max_tokens,
     json_mode=False,
 )


async def repair_letter_after_validation(
 llm: LLMClient,
 *,
 letter: str,
 validation_feedback: str,
 analyzer_json: Dict[str, Any],
 canonical_facts_brief: Dict[str, Any],
 max_tokens: int = 700,
) -> str:
 selected_project = str(analyzer_json.get("selected_project") or "")
 selected_achievements = list(analyzer_json.get("selected_achievements") or [])
 selected_numbers = list(analyzer_json.get("selected_numbers") or [])
 allowed_tech = list(canonical_facts_brief.get("allowed_tech") or [])

 system_prompt = """#
#Ты редактор сопроводительных писем.
#
#Твоя задача - исправить готовое письмо по замечаниям валидатора.
#
#ВАЖНО
#- Не переписывай письмо полностью без необходимости.
#- Сохрани стиль и структуру исходного письма.
#- Исправь только проблемы из validation_feedback.
#- Не добавляй новые факты.
#- Не добавляй новые числа.
#- Не добавляй новые технологии.
#- Не меняй выбранный проект.
#- Не увеличивай письмо.
#- Верни только исправленный текст письма.
#- Не заканчивай письмо шаблонными фразами: "Этот опыт поможет...", "Этот опыт может быть полезен...", "Смогу быстро включиться...", "Буду полезен...", "Готов обсудить задачи и подробнее рассказать о релевантном опыте на собеседовании.", "Буду рад обсудить.", "Хотел бы...".
#- Финальное предложение должно быть живым и конкретным: предложение созвона, доступности по времени или показа архитектуры проекта. НЕ используй ни одну заранее заготовленную фразу - формулируй финал под контекст вакансии и проекта.
#- Не присваивай кандидату технологии из вакансии, если их нет в selected_achievements, allowed_tech или evidence.
#- Запрещено добавлять как опыт кандидата: video player, DRM, ExoPlayer, HLS, DASH, offline cache, WebSocket, Firestore, Amplitude, AppsFlyer.
#- Если validation_feedback просит добавить технологию из вакансии, но её нет в evidence, игнорируй такой fix_hint.
#"""

 user_prompt = f"""\
ИСХОДНОЕ ПИСЬМО:
{letter.strip()}

VALIDATION_FEEDBACK:
{validation_feedback.strip()}

SELECTED_PROJECT:
{selected_project}

ALLOWED_ACHIEVEMENTS:
"""
 for a in selected_achievements:
     user_prompt += f"- {a}
"

 user_prompt += f"""\
ALLOWED_NUMBERS:
"""
 for n in selected_numbers:
     user_prompt += f"- {n}
"

 user_prompt += "ALLOWED_TECH:\
"
 for tech in allowed_tech[:30]:
     user_prompt += f"- {tech}\
"

 user_prompt += (
     "\
"
     "ОБЯЗАТЕЛЬНЫЕ ОГРАНИЧЕНИЯ ПРИ ИСПРАВЛЕНИИ:\
"
     "- Не заканчивай письмо шаблонными фразами: «Этот опыт поможет», «Этот опыт может быть полезен», «Смогу быстро включиться», «Буду полезен», «Готов обсудить задачи и подробнее рассказать о релевантном опыте на собеседовании», «Буду рад обсудить», «Хотел бы».\
"
     "- Финальное предложение должно быть живым и конкретным под контекст вакансии: предложение короткого созвона, указание доступности по времени или предложение показать архитектуру проекта. Сформулируй его сам - не используй заготовленные фразы.\
"
     "- Если в ИСХОДНОМ ПИСЬМЕ уже есть живой, не шаблонный финал - сохрани его без изменений.\
"
     "- Не добавляй как опыт кандидата: video player, DRM, ExoPlayer, HLS, DASH, offline cache, WebSocket, Firestore, Amplitude, AppsFlyer, если этого нет в ALLOWED_ACHIEVEMENTS или ALLOWED_TECH.\
"
     "- Если VALIDATION_FEEDBACK просит добавить неподтверждённую технологию из вакансии, игнорируй эту часть feedback.\
"
     "\
"
     "Исправь письмо минимально. Верни только финальный текст.\
"
 )

 return await llm.generate(
     system_prompt=system_prompt,
     user_prompt=user_prompt,
     temperature=0.15,
     max_tokens=max_tokens,
     json_mode=False,
 )


def _strip_signature_lines(text: str) -> str:
 """Remove a trailing 'С уважением, ...
<name>' block if the model added one."""
 lines = text.splitlines()
 while lines and not lines[-1].strip():
     lines.pop()

 for idx in range(len(lines) - 1, -1, -1):
     stripped = lines[idx].strip().lower()
     if stripped.startswith("с уважением"):
         lines = lines[:idx]
         break

 while lines and not lines[-1].strip():
     lines.pop()

 return "
".join(lines).strip()
