# cover-letter-gen

3-pass пайплайн для генерации сопроводительных писем под Flutter-разработчика.

Это самостоятельный Python-инструмент, лежащий в подпапке этого Flutter-репо — он не зависит от Dart-кода и не используется приложением `create_order_app`.

## Зачем

Старый подход (один монолитный промпт ~700 строк + `T=0.85`) давал нестабильный результат:
- письма часто совпадали слово в слово между разными вакансиями;
- модель регулярно выдумывала факты вроде «production-проекты с сотнями пользователей», которых нет в резюме;
- внутренние «чек-листы» в том же вызове LLM игнорировались.

Новый пайплайн разнесёт это на три специализированных вызова + детерминированный слой фактов.

## Архитектура (v2)

```
                    [Profile (resume.yaml)]
                            │
                            ▼  deterministic, no LLM
                  ┌─────────────────────┐
                  │  CanonicalFacts     │
                  │  (src/facts.py)     │
                  │  • allowed_numbers  │  ← числа из резюме
                  │  • allowed_tech     │  ← стек из проектов
                  │  • allowed_projects │
                  │  • projects[name]:  │
                  │    achievements[]   │  ← дословно из резюме
                  │  • forbidden_claims │  ← «финтех», «сотни пользователей» и т.п.
                  └────────┬────────────┘
                           │
              [Vacancy]    │   единый источник истины
                  │        │
                  ▼        ▼
            ┌──────────────────────────────┐
            │ Pass 1: Analyzer             │  T=0.0, JSON-only
            │  Вход: vacancy + CanonicalFacts
            │  Выход:                      │
            │   • selected_project (∈ allowed_project_names)
            │   • confidence (0.0-1.0)     │
            │   • selected_numbers (⊂ allowed_numbers)
            │   • selected_achievements (⊂ project.achievements, дословно)
            │   • hook_phrase              │
            │  + Python grounding отбрасывает
            │    всё, чего нет в CanonicalFacts
            └──────────┬───────────────────┘
                       │
                       ▼   confidence < 0.2?  → skip
                       │   confidence < 0.5?  → universal mode (1 абзац, без hook)
                       │
            ┌──────────────────────────────┐
            │ Pass 2: Writer               │  T=0.4
            │  Вход: analyzer JSON + opener pool из CanonicalFacts
            │  Системник: ~40 строк        │
            │  Выход: текст письма         │
            └──────────┬───────────────────┘
                       │
            ┌──────────────────────────────┐
            │ Pass 3: Validator            │
            │  Детерминистика (regex):     │
            │   • длина, абзацы            │
            │   • forbidden_phrase         │
            │   • invented_number          │
            │   • forbidden_claim ← grounded
            │   • unknown_tech_term ← grounded
            │   • library_name             │
            │   • anglicism (с whitelist)  │
            │  Семантика (LLM, T=0):       │
            │   • hook_not_addressed       │
            │   • advice_to_company        │
            │   • weak_ending              │
            │   • invented_facts           │
            └──────────┬───────────────────┘
                       │ если passed=false → Writer.rewrite(feedback)   max 2 ретрая
```

### Anti-hallucination: 3 уровня + grounding

| Уровень | Что делает | Где живёт |
|---|---|---|
| **0. CanonicalFacts** | Whitelist строится из `resume.yaml` детерминированно. Источник истины. | `src/facts.py:extract_canonical_facts` |
| 1. Analyzer grounding | LLM-выход фильтруется: проект → должен быть в `allowed_project_names`; числа → в `allowed_numbers`; достижения → дословно из `projects[*].achievements`. Всё лишнее ВЫРЕЗАЕТСЯ ещё до Writer'а. | `src/analyzer.py:_ground` |
| 2. Prompt constraints | Системник Writer'а: «числа — только из selected_numbers; факты — только из selected_achievements». | `src/prompts/writer.py` |
| 3. Deterministic validator | regex-проверка: числа в письме сверяются со списком; smell-фразы из `forbidden_claims` сравниваются с резюме (если фразы нет в резюме — нельзя её употреблять). | `src/validator.py:validate_deterministic` |
| 4. Semantic validator (опц.) | LLM проверяет 5 семантических нарушений, которые regex не ловит. | `src/validator.py:validate_semantic` |

Аналитик **больше не формирует** whitelist — он только выбирает из готового. Если первая LLM-стадия ошибётся, эта ошибка не доживёт до Writer'а.

### Confidence routing

Analyzer ставит `confidence ∈ [0,1]` — насколько выбранный проект релевантен этой конкретной вакансии.

| `confidence` | Действие | Системник Writer'а |
|---|---|---|
| ≥ 0.5 | standard | два абзаца, второй отвечает на `hook_phrase` |
| 0.2 – 0.5 | universal | один абзац, без агрессивной привязки к вакансии |
| < 0.2 | skip | письмо не генерируется, попадает в `_summary.json` с `error="skipped_low_confidence"` |

Пороги конфигурируются в `settings.yaml` (`low_confidence_threshold`, `skip_below_confidence`).

### Curated opener pool

Вместо «не используй эти заходы» (негативный constraint) Writer получает 2 канонических шаблона первой фразы из `src/prompts/opener_pool.py` и должен выбрать/адаптировать один. Использованные openers копятся в `pipeline.used_starts` по батчу.

### LLM retry vs Writer retry — разные уровни

Не путать:
- **LLMClient retry** (`src/llm_client.py`): сетевые ошибки, 5xx, таймауты, пустой `choices: []` → экспоненциальный backoff (0.5s → 1s → 2s → 4s → 8s), `max_retries=3` по умолчанию.
- **Writer retry** (`src/pipeline.py`): письмо не прошло валидацию → Writer переписывает с конкретным списком нарушений, `max_writer_retries=2` по умолчанию.

Первое — про инфраструктуру, второе — про качество. Их счётчики независимы и хорошо видны в логах.

## Структура

```
cover-letter-gen/
├── config/
│   ├── settings.example.yaml    # шаблон настроек
│   └── resume.example.yaml      # анонимизированный шаблон резюме
├── data/                        # vacancies.json кладёшь сам (git-ignored)
├── scripts/
│   └── generate.py              # CLI
├── src/
│   ├── prompts/                 # три промпта + opener_pool
│   ├── models.py                # Profile, Vacancy, Project и пр.
│   ├── facts.py                 # CanonicalFacts — единый whitelist
│   ├── profile_loader.py
│   ├── vacancy_loader.py
│   ├── llm_client.py            # async httpx + retries + JSON-mode fallback
│   ├── analyzer.py              # Pass 1 (с grounding)
│   ├── writer.py                # Pass 2
│   ├── validator.py             # Pass 3 (детерминированный + семантический)
│   └── pipeline.py              # оркестратор
├── tests/
│   ├── test_facts.py            # извлечение CanonicalFacts
│   ├── test_validator.py        # юнит-тесты детерминированных правил
│   └── test_pipeline.py         # e2e-тесты с замоканным LLM
├── .env.example
├── .gitignore                   # настоящие .env / resume.yaml / vacancies.json не коммитятся
└── requirements.txt
```

## Установка

```bash
cd cover-letter-gen
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Настройка

```bash
cp .env.example .env                       # затем подставь свой LLM_API_KEY
cp config/settings.example.yaml config/settings.yaml
cp config/resume.example.yaml config/resume.yaml   # заполни своими данными
```

`.env`, `config/settings.yaml`, `config/resume.yaml` — все игнорируются git'ом.

`vacancies.json` (выход вашего `vacancy-agent-skill`) положите в `data/vacancies.json`. Опциональный фильтр — список ID одной строкой в `data/selected_vacancy_ids.txt`.

## Запуск

```bash
python scripts/generate.py \
  --resume config/resume.yaml \
  --settings config/settings.yaml \
  --vacancies data/vacancies.json \
  --selected data/selected_vacancy_ids.txt \
  --out letters/
```

Письма пишутся в `letters/<company>_<id8>.txt`. Детальный JSON по каждому письму — в `letters/_summary.json`, включая:

```json
{
  "vacancy_id": "...",
  "company": "...",
  "selected_project": "OtherMark",
  "confidence": 0.85,
  "confidence_reason": "...",
  "used_numbers": ["3", "5", "11000"],
  "used_tech": ["Flutter", "BLoC", "Clean", "Architecture"],
  "universal_mode": false,
  "semantic_validator_used": true,
  "word_count": 124,
  "passed": true,
  "attempts": 1,
  "violations": [],
  "error": null
}
```

Флаги:
- `--limit N` — обработать только первые N вакансий.
- `--no-semantic` — пропустить семантический валидатор (быстрее, дешевле).
- `--log-level DEBUG` — больше подробностей.

## Тесты

```bash
cd cover-letter-gen
PYTHONPATH=. pytest -q
```

Тесты не делают реальных HTTP-вызовов — `FakeLLMClient` подменяет LLM по системному промпту и проигрывает заранее заданные ответы. Покрывают: извлечение `CanonicalFacts`, все правила детерминированного валидатора, retry-поведение пайплайна, anti-hallucination grounding, low-confidence routing.

## Что НЕ делает

- Не парсит вакансии — это работа отдельного `vacancy-agent-skill`.
- Не отправляет письма автоматически — только генерирует текст в файлы.
- Не использует `getattr`/`Any`-лазейки — все данные типизированы dataclass'ами.
- Не выдумывает факты: всё, что попадает в письмо, проходит через `CanonicalFacts`.
