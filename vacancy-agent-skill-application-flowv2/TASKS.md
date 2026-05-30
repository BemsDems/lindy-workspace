Финальный план изменений

Файл 1: cover-letter-genv2v2/src/project_selector.py (НОВЫЙ)

Создать с нуля. Содержит:

класс ProjectSelector (выше)

module-level _DEFAULT_SELECTOR + get_default_selector()

импорт _score_project_for_vacancy из .analyzer

Файл 2: cover-letter-genv2v2/src/analyzer.py (ИЗМЕНЕНИЯ)

A. Расширить _score_project_for_vacancy новыми бакетами:

# desktop / kiosk / embedded
desktop_vacancy = any(x in vacancy_text for x in
    ["desktop", "windows", "macos", "linux", "kiosk", "терминал", "embedded", "windows ce"])
desktop_project = any(x in project_text for x in
    ["desktop", "windows", "kiosk", "терминал", "embedded"])
if desktop_vacancy and desktop_project:
    score += 12

# realestate / proptech
realestate_vacancy = any(x in vacancy_text for x in
    ["недвижим", "девелопер", "квартир", "жил", "ипотек", "застройщик", "proptech"])
realestate_project = any(x in project_text for x in
    ["недвижим", "квартир", "жил", "ипотек", "застройщик"])
if realestate_vacancy and realestate_project:
    score += 12

# gRPC / protobuf / specific tech distinguishers
grpc_vacancy = any(x in vacancy_text for x in ["grpc", "protobuf", "protoc"])
grpc_project = any(x in project_text for x in ["grpc", "protobuf", "protoc"])
if grpc_vacancy and grpc_project:
    score += 10

B. Изменить _sanitize_analyzer_result — использовать ProjectSelector:

from .project_selector import get_default_selector

def _sanitize_analyzer_result(result, vacancy, facts):
    selector = get_default_selector()
    llm_selected = str(result.get("selected_project") or "") or None
    
    chosen, reason = selector.select(vacancy, facts, llm_selected)
    result["selected_project"] = chosen
    if reason:
        result["confidence_reason"] = reason
    
    # ... остальная очистка selected_numbers, selected_achievements без изменений

C. Добавить требование hook_phrase в инструкции analyzer'а — но это правка prompts/analyzer.py, не analyzer.py. См. файл 4.

D. Добавить fallback для hook_phrase в _sanitize_analyzer_result:

if not result.get("hook_phrase"):
    if vacancy.requirements:
        result["hook_phrase"] = vacancy.requirements[0][:120]
    else:
        result["hook_phrase"] = vacancy.title

Файл 3: cover-letter-genv2v2/src/writer.py (ИЗМЕНЕНИЯ)

A. Поднять температуру с 0.2 до 0.35 в _final_letter_from_facts:

temperature=0.35,  # было 0.2

B. В _finalizer_user — убедиться, что секция HOOK использует analyzer_json["hook_phrase"].

Если уже использует — оставить. Если берёт из другого места — заменить на чтение из analyzer_json.

(Точное место правки уточню, когда буду писать diff — нужно увидеть текущую сборку HOOK в _finalizer_user. Это легко.)

Файл 4: cover-letter-genv2v2/src/prompts/analyzer.py (ИЗМЕНЕНИЯ)

В ANALYZER_SYSTEM добавить требование вернуть hook_phrase:

hook_phrase: одна короткая фраза (5–12 слов) на русском, которая
формулирует САМОЕ СИЛЬНОЕ требование вакансии — то, на что
кандидат должен прямо ответить в первом абзаце письма. Это не
цитата из вакансии, а её парафраз в форме «нужен X с опытом Y».

В build_analyzer_user (если он есть отдельно) — ничего не меняем.

Файл 5: application_flow.py (ОТКАТ)

Убрать self._recent_picks и self._recent_picks_window из __init__ — они там бесполезны (ApplicationFlow создаётся на каждую вакансию). Тебе их добавляли ранее «впрок» — теперь они переехали в ProjectSelector.