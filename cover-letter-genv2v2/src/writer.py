from __future__ import annotations
import re

import json

from typing import Any, Dict, List, Optional

from .facts import CanonicalFacts
from .llm_client import LLMClient
from .prompts.opener_pool import select_openers
from .prompts.writer import build_writer_user, select_writer_system


# All Flutter versions that may legitimately appear in audited achievements.
# Sourced from the project audit reports — keep in sync with config/resume.yaml.
_ALLOWED_FLUTTER_VERSIONS = (
	"3.0.2",
	"3.22.2",
	"3.29.0",
	"3.8.0",
	"3.16.7",
	"2.14.0",
)


def _extend_selected_numbers_with_allowed_flutter_versions(
	selected_numbers: list,
	selected_achievements: list,
) -> list:
	"""Allow real Flutter migration versions when they are present in selected achievements."""
	result = [str(item) for item in (selected_numbers or [])]
	achievements_text = "\n".join(str(item) for item in (selected_achievements or []))

	for version in _ALLOWED_FLUTTER_VERSIONS:
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
	achievements_text = "\n".join(str(item).lower() for item in (selected_achievements or []))
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


# Match a numeric token, allowing internal whitespace within the integer part
# (e.g. "2 682", "11 381", "32 840") as well as decimals with . or , and
# optional suffix (%, +, млн, тыс, тысяч).
_METRIC_RE = re.compile(
	r"\d{1,3}(?:[\s\u00a0]\d{3})*(?:[.,]\d+)*\s*(?:%|\+|млн|тыс|тысяч)?",
	re.IGNORECASE,
)


def _normalize_metric(value: object) -> str:
	"""Canonicalize a numeric token: lowercase, drop internal whitespace, comma→dot."""
	text = str(value).lower().replace(",", ".")
	# Drop ALL whitespace (regular and NBSP) so "2 682" → "2682", "3 670" → "3670".
	text = re.sub(r"[\s\u00a0]+", "", text)
	return text


def _extract_numbers(text: str) -> list[str]:
	"""Pull all numeric tokens from a string, preserving the original form."""
	if not text:
		return []
	return _METRIC_RE.findall(str(text).lower())


def _allowed_metric_tokens(analyzer_json: Dict[str, Any]) -> set[str]:
	"""Build the whitelist of metric tokens the letter is allowed to contain.

	Whitelist sources (any of these legitimizes a number in the final letter):
	1. selected_numbers — what the analyzer explicitly chose to highlight.
	2. selected_achievements — every number the LLM physically sees in its
	   prompt context. If a number is in achievements the LLM is entitled
	   to use it, even if analyzer didn't pick it as a hero number.

	v2 history:
	- Previously we unconditionally added "3" / "3+" for the years opener;
	  removed in v2 after achievement-based openers.
	- Previously we built tokens only from selected_numbers; this caused
	  legitimate numbers from achievements to be cut by the post-filter.
	"""
	tokens: set[str] = set()

	for source_key in ("selected_numbers", "selected_achievements"):
		for item in analyzer_json.get(source_key) or []:
			for number in _extract_numbers(str(item)):
				tokens.add(_normalize_metric(number))

	return tokens


def _remove_unapproved_metric_sentences(text: str, analyzer_json: Dict[str, Any]) -> str:
	allowed = _allowed_metric_tokens(analyzer_json)
	paragraphs = text.split("\n\n")
	cleaned_paragraphs: list[str] = []

	for paragraph in paragraphs:
		sentences = re.split(r"(?<=[.!?])\s+", paragraph.strip())
		cleaned_sentences: list[str] = []

		for sentence in sentences:
			numbers = _extract_numbers(sentence)

			if not numbers:
				cleaned_sentences.append(sentence)
				continue

			# v2: removed the special "3+ года" / "3 года" whitelist. Years-in-text
			# must now pass the normal allowed-tokens check like every other number.

			normalized_numbers = {_normalize_metric(number) for number in numbers}

			if normalized_numbers.issubset(allowed):
				cleaned_sentences.append(sentence)

		cleaned_paragraph = " ".join(item for item in cleaned_sentences if item).strip()

		if cleaned_paragraph:
			cleaned_paragraphs.append(cleaned_paragraph)

	return "\n\n".join(cleaned_paragraphs).strip()


def _enforce_paragraph_split(text: str, *, universal_mode: bool) -> str:
	"""Force at least 2 paragraphs for STANDARD mode if the model returned a single block.

	Heuristic: if STANDARD mode and the letter is one block of 3+ sentences,
	split at the boundary closest to the middle (between sentences).
	UNIVERSAL mode keeps a single paragraph.
	"""
	cleaned = (text or "").strip()
	if not cleaned or universal_mode:
		return cleaned

	# Already has paragraph breaks - trust the model.
	if "\n\n" in cleaned:
		return cleaned

	# Split into sentences (keep delimiters).
	sentences = re.split(r"(?<=[.!?])\s+", cleaned)
	sentences = [s.strip() for s in sentences if s.strip()]

	if len(sentences) < 3:
		return cleaned

	# Find split point closest to the middle.
	mid = len(sentences) // 2
	first = " ".join(sentences[:mid]).strip()
	second = " ".join(sentences[mid:]).strip()

	if not first or not second:
		return cleaned

	return f"{first}\n\n{second}"


def _build_greeting(vacancy_company: str) -> str:
	"""Build the exact greeting line the model must use."""
	company = (vacancy_company or "").strip()
	if company:
		return f"Здравствуйте, {company}!"
	return "Здравствуйте!"


def _inject_greeting(text: str, greeting: str) -> str:
	"""Ensure the letter starts with exactly the greeting line.

	If the model already produced the correct greeting as the first line,
	leave the text untouched. Otherwise prepend the greeting followed by
	a blank line, stripping any wrong greeting the model may have written.
	"""
	cleaned = (text or "").strip()
	if not cleaned:
		return greeting

	lines = cleaned.splitlines()

	# Check if first non-empty line is already the correct greeting.
	first_line = lines[0].strip() if lines else ""
	if first_line == greeting:
		return cleaned

	# Strip a wrong greeting line if the model wrote one (starts with "Здравствуйте").
	if first_line.startswith("Здравствуйте"):
		# Remove the first line and any immediately following blank line.
		rest_lines = lines[1:]
		while rest_lines and not rest_lines[0].strip():
			rest_lines.pop(0)
		cleaned = "\n".join(rest_lines).strip()

	return f"{greeting}\n\n{cleaned}"


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
	vacancy_title: str = "",
	vacancy_company: str = "",
	vacancy_description: str = "",
	vacancy_requirements: Optional[List[str]] = None,
) -> str:
	system_prompt = select_writer_system(universal_mode=universal_mode)
	selected_project = str(analyzer_json.get("selected_project") or "")
	brief = build_canonical_facts_brief(facts, selected_project)
	# v2: opener pool now consumes CanonicalFacts + selected_project and
	# returns achievement-based hooks instead of "{years}+ years" templates.
	opener_pool = select_openers(
		facts,
		selected_project,
		used_starts or [],
		n=2,
	)

	# Generate the final letter FROM FACTS ONLY.
	final_text = await _final_letter_from_facts(
		llm,
		analyzer_json=analyzer_json,
		canonical_facts_brief=brief,
		opener_pool=opener_pool,
		universal_mode=universal_mode,
		feedback=feedback,
		max_tokens=max_tokens,
		vacancy_title=vacancy_title,
		vacancy_company=vacancy_company,
		vacancy_description=vacancy_description,
		vacancy_requirements=vacancy_requirements or [],
	)
	stripped = _strip_signature_lines(final_text)
	split = _enforce_paragraph_split(stripped, universal_mode=universal_mode)

	# Python-level guarantee: greeting is always the first line.
	greeting = _build_greeting(vacancy_company)
	return _inject_greeting(split, greeting)


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
- любые проценты улучшения/ускорения/снижения, которых НЕТ в исходном черновике (например, "повысил производительность на 30%", "ускорил на 25%").
- любые цифры аудитории и масштаба, которых НЕТ в исходном черновике (например, "1,1 млн пользователей", "500 тыс. установок", "обслуживает миллион клиентов").
- конкретные времена встреч и длительности созвонов, которых НЕТ в исходном черновике (например, "созвон на 20 минут", "после 17:00", "в будни вечером").

СТИЛЬ
- Русский язык, тех. термины латиницей.
- Письмо должно читаться естественно.
"""


def _cleaner_user(draft: str, *, universal_mode: bool) -> str:
	mode = "1-2 плотных абзаца" if universal_mode else "2-3 абзаца, разделённых пустой строкой"
	return (
		f"Режим: {mode}.\n"
		"Перепиши черновик в чистое сопроводительное письмо по режиму выше.\n\n"
		"ЧЕРНОВИК:\n"
		f"{draft.strip()}\n"
	)


FINALIZER_SYSTEM = """Ты пишешь сопроводительное письмо от имени Flutter-разработчика для российского рынка (HH.ru, корпоративная почта, Telegram).

=== АДРЕСАТ ===
Российский HR или тимлид. Читает 50+ откликов в день. Шаблон отличает за 5 секунд.

=== ГЛАВНЫЙ ПРИНЦИП ===
Письмо должно быть НАПИСАНО ПОД КОНКРЕТНУЮ ВАКАНСИЮ, а не быть переписанным резюме.
- Читай блок VACANCY_CONTEXT внимательно. Привязывай факты из ACHIEVEMENTS к тому, что нужно ЭТОЙ компании.
- Если в названии вакансии или описании есть нестандартная роль (спикер, преподаватель, ментор, тимлид, архитектор) - явно ответь на эту роль, а не пиши как обычный dev.
- Если у компании специфический домен (инфобез, финтех, медтех, EdTech, стриминг, AdTech) - покажи понимание домена в 1 предложении.

=== АБСОЛЮТНЫЕ ПРАВИЛА (НАРУШЕНИЕ = БРАК) ===

A. ПЕРВАЯ СТРОКА ПИСЬМА = ровно та строка, которая передана в блоке GREETING. Слово в слово. Затем пустая строка. Затем основной текст.

B. ЗАПРЕТ НА «ГОДЫ ОПЫТА» В ОПЕНЕРЕ. Первое содержательное предложение после GREETING НЕ должно начинаться с конструкций про годы опыта. Конкретно запрещено в первом предложении:
- «X+ лет», «X+ года», «X лет», «X года» (опыта/разработки/коммерческой/работы).
- «Имею X+ лет опыта», «Опыт работы X+ лет», «Более X лет», «Около X лет».
- «Занимаюсь Flutter-разработкой X+ лет/года».
Это ЛЮБАЯ формулировка про длительность опыта в первом предложении — запрещена.

ВМЕСТО этого: первое предложение = конкретный факт-кейс из ACHIEVEMENTS (что сделал, в каком продукте/домене), либо прямой ответ на HOOK / специальную роль. Подсказку по содержанию первого предложения смотри в блоке OPENERS — там УЖЕ собран опенер из реального проекта кандидата.

C. ЗАПРЕЩЁННЫЕ ФИНАЛЫ - ни одна из этих фраз не должна появиться в конце письма:
- «Готов обсудить задачи и подробнее рассказать о релевантном опыте на собеседовании.»
- «Готов обсудить задачи на собеседовании.»
- «Буду рад обсудить.»
- «Буду рад обсудить детали.»
- «Хотел бы обсудить...»
- «Этот опыт поможет...»
- «Этот опыт может быть полезен...»
- «Смогу быстро включиться...»
- «Буду полезен...»
ВМЕСТО них - конкретный, живой финал (примеры в секции ЖИВЫЕ ФИНАЛЫ ниже).

D. ВТОРОЙ АБЗАЦ ОБЯЗАТЕЛЕН (только для STANDARD режима). Между первым и вторым абзацем - ровно одна пустая строка (\n\n). Не 1 абзац, не 3.

E. ПОДПИСЬ НЕ ДОБАВЛЯЙ - её добавит постпроцесс.

F. ЗАПРЕТ НА ВЫДУМАННЫЕ МЕТРИКИ И ФАКТЫ.
Эти классы утверждений ЗАПРЕЩЕНО писать в ЛЮБОЙ части письма, даже если они «звучат правдоподобно». Если ниже описанного факта нет ДОСЛОВНО в ACHIEVEMENTS — НЕ ПИШИ:

F.1. Проценты эффективности / ускорения / снижения:
- «улучшил производительность на 30%», «ускорил на 25%», «сократил время отклика на X%», «снизил crash rate на X%», «повысил конверсию на X%».
- Любое «X%» рядом со словами «быстрее», «улучшение», «снижение», «рост», «оптимизация», «производительность».
Эти числа можно использовать ТОЛЬКО если они дословно лежат в ACHIEVEMENTS.

F.2. Цифры аудитории и пользовательской базы:
- «X млн пользователей», «X тыс. установок», «обслуживает X пользователей в месяц», «MAU/DAU = X», «X+ компаний используют».
- Любые слова «миллион», «млн», «тыс.», «тысяч» рядом со словами «пользоват», «клиент», «компани», «установ», «аудитори». Эти цифры можно использовать ТОЛЬКО если они дословно лежат в ACHIEVEMENTS.

F.3. Конкретные времена и длительности созвонов (small-talk факты):
- «20-минутный созвон», «созвон на 30 минут», «свободен после 17:00», «после 18:00», «в пятницу вечером», «в будни», «в выходные».
- Эти small-talk детали запрещены — они выглядят как имитация живости, но являются выдумкой. Финал должен быть живым БЕЗ выдумывания конкретных таймслотов.

F.4. Точные количественные обещания будущего:
- «выйду на full performance за 2 недели», «закрою задачу за X дней», «снижу bug rate на X%».

=== СТРУКТУРА ===

STANDARD режим (2 абзаца, 70-110 слов суммарно):

Абзац 1 (3-4 предложения):
- Начинается СРАЗУ с конкретного факта-кейса (что сделал в продукте/проекте) или прямого ответа на HOOK_PHRASE/роль из VACANCY_TITLE.
- НЕ начинать с «X+ лет опыта», «Меня заинтересовала ваша вакансия», «Я узнал о вакансии», «Хочу предложить свою кандидатуру».
- 1-2 числовых факта из SELECTED_NUMBERS.
- Привязка к ALLOWED_TECH (не более 3 технологий подряд).

Абзац 2 (2-3 предложения):
- 1 предложение про то, что заинтересовало в продукте / домене / задачах компании (используй VACANCY_DESCRIPTION).
- Живой, конкретный финал.

UNIVERSAL режим: 1 плотный абзац 60-90 слов, без агрессивного hook.

=== ОБРАБОТКА СПЕЦИАЛЬНЫХ РОЛЕЙ ===

Если в VACANCY_TITLE или VACANCY_DESCRIPTION есть слова «спикер», «преподаватель», «ментор», «тренер», «evangelist», «advocate», «лектор», «курс», «обучение»:
- ОБЯЗАТЕЛЬНО первый абзац должен быть про опыт публичных выступлений, менторинга, проведения митапов, code review или обучения команды (если такой опыт есть в ACHIEVEMENTS).
- Если такого опыта в ACHIEVEMENTS нет - сделай акцент на структурном мышлении, документировании решений, готовности делиться знаниями.
- НЕ пиши про продуктовые фичи и метрики DAU/MAU - это не про эту вакансию.

Если в VACANCY_TITLE есть слова «тимлид», «teamlead», «архитектор», «senior+», «principal», «head»:
- Покажи опыт принятия архитектурных решений, ревью кода, наставничества, постановки процессов.

=== ЗАПРЕЩЁННЫЕ КЛИШЕ ===
«ответственный», «командный игрок», «быстрая обучаемость», «результат-ориентированный», «стрессоустойчивый», «коммуникабельный».

=== ЖИВЫЕ ФИНАЛЫ (варьируй, не копируй один и тот же; БЕЗ конкретных таймслотов и длительностей) ===
- «Если интересно — давайте созвонимся, расскажу детали по архитектуре.»
- «Готов показать архитектуру решения на коротком созвоне.»
- «Если резюме откликается — давайте сразу созвонимся, без длинной переписки.»
- «На созвоне покажу, как это работает изнутри.»
- «Удобнее обсудить голосом — напишите, в какое время позвонить.»
- «Если попадаю в стек — давайте обсудим в формате созвона.»

=== ЧТО ОБЯЗАТЕЛЬНО ===
- Первая строка = ровно GREETING.
- Конкретные числа из SELECTED_NUMBERS (минимум 1, максимум 3).
- Привязка к VACANCY_CONTEXT: минимум одно предложение, где явно видно, что письмо написано под ЭТУ вакансию (упомянут продукт / домен / специфическое требование).
- Ответ на HOOK_PHRASE.
- Живой финал, не из списка запрещённых и БЕЗ выдуманных времён/длительностей.

=== ЧЕГО НЕ ДЕЛАТЬ ===
- Не перечисляй больше 3 технологий подряд.
- Не упоминай зарплату.
- Не выдумывай факты вне SELECTED_NUMBERS / ALLOWED_TECH / ACHIEVEMENTS.
- Не используй англицизмы без необходимости («pipeline», «hook», «scope»).
- Не присваивай кандидату технологии из вакансии, если их нет в ACHIEVEMENTS или ALLOWED_TECH.
- Не добавляй подпись «С уважением, ...» - её добавит постпроцесс.
- Не выдумывай проценты эффективности, цифры аудитории, конкретные времена/длительности встреч (см. блок F).
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
	vacancy_title: str = "",
	vacancy_company: str = "",
	vacancy_description: str = "",
	vacancy_requirements: Optional[List[str]] = None,
) -> str:
	mode = (
		"UNIVERSAL: 1 плотный абзац, 60-90 слов"
		if universal_mode
		else "STANDARD: РОВНО 2 абзаца через \n\n, 70-110 слов суммарно"
	)

	greeting = _build_greeting(vacancy_company)

	parts: List[str] = [
		f"MODE: {mode}",
		"",
		"=== GREETING (ПЕРВАЯ СТРОКА ПИСЬМА = РОВНО ЭТА СТРОКА, ПОТОМ ПУСТАЯ СТРОКА) ===",
		greeting,
		"",
		"=== VACANCY_CONTEXT (КУДА ПИШЕМ) ===",
		f"VACANCY_TITLE: {vacancy_title or '(не передан)'}",
		f"VACANCY_COMPANY: {vacancy_company or '(не передан)'}",
	]

	if vacancy_description:
		desc_trimmed = vacancy_description.strip()
		if len(desc_trimmed) > 1200:
			desc_trimmed = desc_trimmed[:1200].rstrip() + "..."
		parts.append(f"VACANCY_DESCRIPTION: {desc_trimmed}")

	if vacancy_requirements:
		parts.append("VACANCY_REQUIREMENTS:")
		for req in list(vacancy_requirements)[:15]:
			req_text = str(req).strip()
			if req_text:
				parts.append(f"- {req_text}")

	parts.extend([
		"",
		"=== HOOK (ГЛАВНОЕ ТРЕБОВАНИЕ, на которое надо ответить) ===",
		f"HOOK: {hook_phrase or '(не передан - привяжи к VACANCY_TITLE и описанию)'}",
		"",
		"=== ВЫБРАННЫЙ ПРОЕКТ КАНДИДАТА ===",
		"PROJECT: выбранный проект из резюме, название проекта не использовать в письме",
		"PROJECT_FACTS:",
		"- company: прошлую компанию не указывать в письме",
		f"- industry: {project_industry}",
		f"- description: {project_description}",
		"",
		f"SELECTED_NUMBERS: {json.dumps(selected_numbers, ensure_ascii=False)}",
		"ACHIEVEMENTS (разрешённые факты):",
	])
	for a in selected_achievements:
		parts.append(f"- {a}")

	parts.append("")
	parts.append("ALLOWED_TECH (используй только эти термины):")
	for t in allowed_tech[:80]:
		parts.append(f"- {t}")

	parts.append("")
	parts.append("OPENERS (используй СМЫСЛ одного из них как первую содержательную фразу ПОСЛЕ GREETING — это уже опенер на основе реального проекта кандидата; адаптируй формулировку под вакансию, но НЕ выноси отдельной строкой и НЕ начинай с «X+ лет»):")
	for o in openers:
		parts.append(f"- {o}")

	if feedback:
		parts.append("")
		parts.append("FEEDBACK ОТ ВАЛИДАТОРА:")
		parts.append(feedback)
		parts.append("Исправь это, но не упоминай feedback в письме.")

	parts.append("")
	parts.append("=== НАПОМИНАНИЕ ПО ВЫДУМАННЫМ ФАКТАМ ===")
	parts.append("Запрещено писать в письме (если этого НЕТ дословно в ACHIEVEMENTS):")
	parts.append("- любые «X%» рядом со словами «быстрее», «улучшение», «снижение», «рост», «производительность», «оптимизация»;")
	parts.append("- любые «X млн / X тыс. пользователей / клиентов / установок / компаний»;")
	parts.append("- любые «20-минутный созвон», «созвон на 30 минут», «после 17:00», «после 18:00», «в будни», «в пятницу вечером».")
	parts.append("Финал должен быть живым, но БЕЗ конкретного времени и длительности.")

	parts.append("")
	parts.append("=== ЗАДАЧА ===")
	if not universal_mode:
		parts.append(
			"Напиши письмо по MODE.\n"
			"СТРУКТУРА: первая строка = GREETING слово в слово. Затем пустая строка. Затем абзац 1 (3-4 предложения, привязка к VACANCY_CONTEXT + 1-2 факта из ACHIEVEMENTS с числами). Затем пустая строка. Затем абзац 2 (2-3 предложения: связка с компанией/доменом из VACANCY_CONTEXT + живой финал).\n"
			"ПЕРВОЕ ПРЕДЛОЖЕНИЕ АБЗАЦА 1: конкретный факт-кейс из ACHIEVEMENTS / OPENERS. ЗАПРЕЩЕНО начинать с «X+ лет», «X лет», «X+ года», «X года», «Имею X+ лет опыта», «Опыт работы», «Более X лет», «Около X лет».\n"
			"ФИНАЛ: НЕ используй фразы из секции ЗАПРЕЩЁННЫЕ ФИНАЛЫ. Сформулируй живой финал сам - предложение созвона ИЛИ готовность показать архитектуру. БЕЗ конкретного времени и длительности.\n"
			"Верни только письмо, без комментариев."
		)
	else:
		parts.append(
			"Напиши письмо по MODE.\n"
			"СТРУКТУРА: первая строка = GREETING слово в слово. Затем пустая строка. Затем 1 плотный абзац.\n"
			"ПЕРВОЕ ПРЕДЛОЖЕНИЕ ПОСЛЕ GREETING: конкретный факт-кейс из ACHIEVEMENTS / OPENERS. ЗАПРЕЩЕНО начинать с «X+ лет», «X лет», «X+ года», «X года», «Имею X+ лет опыта», «Опыт работы», «Более X лет», «Около X лет».\n"
			"ФИНАЛ: НЕ используй фразы из секции ЗАПРЕЩЁННЫЕ ФИНАЛЫ. БЕЗ конкретного времени и длительности.\n"
			"Верни только письмо, без комментариев."
		)
	return "\n".join(parts)


async def _final_letter_from_facts(
	llm: LLMClient,
	*,
	analyzer_json: Dict[str, Any],
	canonical_facts_brief: Dict[str, Any],
	opener_pool: List[str],
	universal_mode: bool,
	feedback: Optional[str] = None,
	max_tokens: int = 400,
	vacancy_title: str = "",
	vacancy_company: str = "",
	vacancy_description: str = "",
	vacancy_requirements: Optional[List[str]] = None,
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
			vacancy_title=vacancy_title,
			vacancy_company=vacancy_company,
			vacancy_description=vacancy_description,
			vacancy_requirements=vacancy_requirements or [],
		),
		temperature=0.25,
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
	min_words: int = 70,
	max_words: int = 110,
) -> str:
	selected_project = str(analyzer_json.get("selected_project") or "")
	selected_achievements = list(analyzer_json.get("selected_achievements") or [])
	selected_numbers = list(analyzer_json.get("selected_numbers") or [])
	allowed_tech = list(canonical_facts_brief.get("allowed_tech") or [])

	system_prompt = """\
Ты редактор сопроводительных писем.

Твоя задача - исправить готовое письмо по замечаниям валидатора.

ВАЖНО
- Не переписывай письмо полностью без необходимости.
- Сохрани стиль и структуру исходного письма.
- Исправь только проблемы из validation_feedback.
- Не добавляй новые факты.
- Не добавляй новые числа.
- Не добавляй новые технологии.
- Не меняй выбранный проект.
- Не увеличивай письмо.
- Верни только исправленный текст письма.
- Не заканчивай письмо шаблонными фразами: "Этот опыт поможет...", "Этот опыт может быть полезен...", "Смогу быстро включиться...", "Буду полезен...", "Готов обсудить задачи и подробнее рассказать о релевантном опыте на собеседовании.", "Буду рад обсудить.", "Хотел бы...".
- Финальное предложение должно быть живым и конкретным: предложение созвона или готовность показать архитектуру проекта. БЕЗ конкретного времени («после 17:00») и БЕЗ длительности («20-минутный созвон»).
- Первое предложение письма (сразу после "Здравствуйте...") НЕ должно начинаться с конструкций про годы опыта ("X+ лет", "X лет", "X+ года", "X года", "Имею X+ лет опыта", "Опыт работы", "Более X лет", "Около X лет"). Если в исходном письме такая конструкция уже есть — замени её на конкретный факт-кейс из selected_achievements (что сделал, в каком продукте/домене).
- Не присваивай кандидату технологии из вакансии, если их нет в selected_achievements, allowed_tech или evidence.
- Запрещено добавлять как опыт кандидата: video player, DRM, ExoPlayer, HLS, DASH, offline cache, WebSocket, Firestore, Amplitude, AppsFlyer, FFI, Kotlin, Swift, platform channels, MethodChannel, EventChannel.
- Если validation_feedback просит добавить технологию из вакансии, но её нет в evidence, игнорируй такой fix_hint.
- Запрещено добавлять как факт кандидата (если этого НЕТ дословно в selected_achievements):
* любые «X%» рядом со словами «быстрее», «улучшение», «снижение», «рост», «производительность», «оптимизация»;
* любые «X млн / X тыс. пользователей / клиентов / установок»;
* любые «20-минутный созвон», «созвон на 30 минут», «после 17:00», «после 18:00», «в будни», «в пятницу вечером».
"""

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
		user_prompt += f"- {a}\n"

	user_prompt += f"""\
ALLOWED_NUMBERS:
"""
	for n in selected_numbers:
		user_prompt += f"- {n}\n"

	user_prompt += "ALLOWED_TECH:\n"
	for tech in allowed_tech[:30]:
		user_prompt += f"- {tech}\n"

	user_prompt += (
		"\n"
		"ОБЯЗАТЕЛЬНЫЕ ОГРАНИЧЕНИЯ ПРИ ИСПРАВЛЕНИИ:\n"
		f"- ЦЕЛЕВОЙ ОБЪЁМ: письмо должно содержать от {min_words} до {max_words} слов суммарно. Если исходное письмо длиннее {max_words} слов — СОКРАТИ его до диапазона {min_words}-{max_words}, удаляя наименее ценные фрагменты: вводные обороты, повторы, дублирующие технические детали, общие фразы про мотивацию. Сохрани числовые факты и привязку к вакансии. Если короче {min_words} слов — НЕ ДОБАВЛЯЙ новый материал, только переформулируй компактнее в пределах допустимого.\n"
		"- Не заканчивай письмо шаблонными фразами: «Этот опыт поможет», «Этот опыт может быть полезен», «Смогу быстро включиться», «Буду полезен», «Готов обсудить задачи и подробнее рассказать о релевантном опыте на собеседовании», «Буду рад обсудить», «Хотел бы».\n"
		"- Первое предложение письма (после «Здравствуйте...») НЕ должно начинаться с «X+ лет», «X лет», «X+ года», «X года», «Имею X+ лет опыта», «Опыт работы», «Более X лет», «Около X лет». Если такая конструкция есть в исходном письме, замени её на конкретный факт-кейс из ALLOWED_ACHIEVEMENTS.\n"
		"- Финальное предложение должно быть живым и конкретным под контекст вакансии: предложение созвона или предложение показать архитектуру проекта. БЕЗ конкретного времени («после 17:00») и БЕЗ длительности («20-минутный созвон»).\n"
		"- Если в ИСХОДНОМ ПИСЬМЕ уже есть живой, не шаблонный финал без выдуманных времён/длительностей - сохрани его без изменений.\n"
		"- Не добавляй как опыт кандидата: video player, DRM, ExoPlayer, HLS, DASH, offline cache, WebSocket, Firestore, Amplitude, AppsFlyer, FFI, Kotlin, Swift, platform channels, MethodChannel, EventChannel, если этого нет в ALLOWED_ACHIEVEMENTS или ALLOWED_TECH.\n"
		"- Если VALIDATION_FEEDBACK просит добавить неподтверждённую технологию из вакансии, игнорируй эту часть feedback.\n"
		"- Не добавляй: «X% улучшения/ускорения/снижения», «X млн пользователей», «X тыс. установок», «20-минутный созвон», «после 17:00», «в будни», если этого нет в ALLOWED_ACHIEVEMENTS.\n"
		"\n"
		"Исправь письмо минимально. Верни только финальный текст.\n"
	)

	return await llm.generate(
		system_prompt=system_prompt,
		user_prompt=user_prompt,
		temperature=0.15,
		max_tokens=max_tokens,
		json_mode=False,
	)


def _strip_signature_lines(text: str) -> str:
	"""Remove a trailing 'С уважением, ...\n    <name>' block if the model added one."""
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

	return "\n".join(lines).strip()
