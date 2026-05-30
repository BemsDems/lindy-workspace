# Vacancy Agent Skill — функционал и запуск

Документ описывает, что умеет `vacancy-agent`, как он связан с `cover-letter-gen`, как запускать поиск вакансий, генерацию сопроводительного письма, ручное согласование и отправку отклика через Playwright.

---

## 1. Общая архитектура

`vacancy-agent` отвечает за весь flow работы с вакансиями:

```text
поиск вакансий
 ↓
сохранение вакансий
 ↓
генерация сопроводительного письма через cover-letter-gen
 ↓
ручное согласование письма
 ↓
редактирование письма при необходимости
 ↓
отправка отклика через apply-adapter
```

Сам `vacancy-agent` не должен сам генерировать финальный текст письма через простую заглушку. Для этого используется отдельный skill:

**cover-letter-gen**

---

## 2. Основные компоненты

### 2.1. Поиск вакансий

Поиск вакансий выполняется через CLI-команду:

```bash
python3 -m vacancy_agent.cli search-url "<search_url>" --max-pages 1 --max-vacancies 5 --cdp http://127.0.0.1:9222
```

Пример для HH:

```bash
python3 -m vacancy_agent.cli search-url \
 "https://hh.ru/search/vacancy?text=Flutter&area=1" \
 --max-pages 1 \
 --max-vacancies 5 \
 --cdp http://127.0.0.1:9222
```

После поиска вакансии сохраняются в:

- `data/vacancies.json`
- `data/vacancies_by_source/`

---

### 2.2. Генерация сопроводительного письма

Генерация выполняется через внешний skill:

**cover-letter-gen**

Он отвечает за:

- Analyze → Write → Validate → Rewrite

В `vacancy-agent` генерация вызывается через слой:

`vacancy_agent/letter_adapters/`

Основной адаптер:

`CoverLetterGenAdapter`

Команда только для генерации письма:

```bash
python3 -m vacancy_agent.cli apply <vacancy_id>
```

Пример:

```bash
python3 -m vacancy_agent.cli apply bf155f3882bb48de
```

Результат сохраняется в:

`data/applications.json`

---

### 2.3. Ручное согласование письма

После генерации письмо не отправляется автоматически.

Flow сначала показывает письмо пользователю и ждёт решение:

```text
[1] Одобрить и отправить
[2] Открыть письмо в редакторе
[3] Сгенерировать заново
[4] Отклонить/пропустить
[5] Сохранить как черновик
```

Согласование реализовано через:

`vacancy_agent/approval_adapters/`

Для CLI используется:

`ConsoleApprovalAdapter`

Для OpenClaw/Telegram используется:

`OpenClawApprovalAdapter`

---

### 2.4. Сохранение письма как черновика

Если пользователь выбирает пункт **[5] Сохранить как черновик**:

- Письмо сохраняется с статусом `draft`.
- Flow **прерывается до отправки** (`apply_service.apply(...)` не вызывается).
- Не происходит ни `dry-run`, ни `send`, ни открытия страницы вакансии.
- Вакансия помечается как `DRAFT` в базе.

Это позволяет сохранить письмо для дальнейшего редактирования или отправки без необходимости одобрять или отклонять его сразу.

---

### 2.4. Редактирование письма

Если выбрать пункт:

```text
[2] Открыть письмо в редакторе
```

агент создаёт временный `.md` файл и открывает его в редакторе.

После сохранения и закрытия редактора агент:

1. читает изменённый текст;
2. валидирует его;
3. если всё хорошо — продолжает отправку;
4. если есть ошибки — просит исправить.

Для macOS удобно использовать TextEdit.

В `.env`:

```bash
VACANCY_AGENT_EDITOR=open -W -a TextEdit
```

Или временно в терминале:

```bash
export VACANCY_AGENT_EDITOR="open -W -a TextEdit"
```

---

### 2.5. Отправка отклика

Отправка выполняется через слой адаптеров:

`vacancy_agent/apply_adapters/`

Сейчас основной адаптер:

`HHApplyAdapter`

Он поддерживает HH.ru и региональные домены HH, например:

- hh.ru
- spb.hh.ru
- nalchik.hh.ru
- hh.kz

**Flow HH:**

1. открыть страницу вакансии;
2. проверить, не в архиве ли вакансия;
3. нажать «Откликнуться»;
4. если вакансия не в РФ — нажать «Все равно откликнуться»;
5. проверить блокеры:
   - логин;
   - капча;
   - анкета работодателя;
   - тестовое задание;
   - архив;
6. дождаться поля сопроводительного письма;
7. вставить утверждённый текст;
8. если `dry-run` — остановиться;
9. если `send` — нажать «Отправить»;
10. дождаться подтверждения «Резюме доставлено».

---

## 3. Переменные окружения

Рекомендуется создать файл:

`project/.env`

Пример:

```bash
COVER_LETTER_PROVIDER=cover_letter_gen
COVER_LETTER_GEN_PATH=/Users/pipyao/fp/create_order_app/cover-letter-gen
VACANCY_AGENT_EDITOR=open -W -a TextEdit
```

Если `cover-letter-gen` лежит рядом с `vacancy-agent`, можно использовать:

```bash
COVER_LETTER_PROVIDER=auto
```

Если путь нестандартный, лучше явно указать:

```bash
COVER_LETTER_PROVIDER=cover_letter_gen
COVER_LETTER_GEN_PATH=/Users/pipyao/fp/create_order_app/cover-letter-gen
```

---

## 4. Подготовка окружения

Перейти в проект:

```bash
cd /Users/pipyao/.openclaw/workspace-coding/skills/vacancy-agent-skill-application-flow/project
```

Активировать виртуальное окружение:

```bash
source ../.venv/bin/activate
```

Если `.venv` находится внутри `project`:

```bash
source .venv/bin/activate
```

Установить пакет:

```bash
python3 -m pip install -e .
```

Установить Playwright:

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

Проверить CLI:

```bash
python3 -m vacancy_agent.cli version
python3 -m vacancy_agent.cli status
python3 -m vacancy_agent.cli --help
```

---

## 5. Запуск Edge через CDP

Агент подключается к уже открытому браузеру через CDP.

Запуск Microsoft Edge на macOS:

```bash
/Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge \
 --remote-debugging-port=9222 \
 --user-data-dir="$HOME/edge-hh-agent"
```

После запуска открой в этом Edge:

```text
https://hh.ru
```

И авторизуйся вручную.

Проверить, что CDP работает:

```text
http://127.0.0.1:9222
```

В командах CLI передавать:

```bash
--cdp http://127.0.0.1:9222
```

---

## 6. Основные команды

### 6.1. Поиск вакансий

```bash
python3 -m vacancy_agent.cli search-url \
 "https://hh.ru/search/vacancy?text=Flutter&area=1" \
 --max-pages 1 \
 --max-vacancies 5 \
 --cdp http://127.0.0.1:9222
```

---

### 6.2. Посмотреть список вакансий

```bash
python3 -m vacancy_agent.cli list --all --limit 20
```

---

### 6.3. Посмотреть конкретную вакансию

```bash
python3 -m vacancy_agent.cli show <vacancy_id>
```

Пример:

```bash
python3 -m vacancy_agent.cli show bf155f3882bb48de
```

---

### 6.4. Только сгенерировать письмо

```bash
python3 -m vacancy_agent.cli apply <vacancy_id>
```

Пример:

```bash
python3 -m vacancy_agent.cli apply bf155f3882bb48de
```

---

### 6.5. Полный flow без реальной отправки

```bash
python3 -m vacancy_agent.cli approve-submit \
 <vacancy_id> \
 --dry-run \
 --cdp http://127.0.0.1:9222
```

Пример:

```bash
python3 -m vacancy_agent.cli approve-submit \
 bf155f3882bb48de \
 --dry-run \
 --cdp http://127.0.0.1:9222
```

`--dry-run` означает:

- письмо будет сгенерировано;
- письмо будет показано на согласование;
- можно будет отредактировать письмо;
- браузер откроет вакансию;
- письмо будет вставлено в поле;
- финальная кнопка «Отправить» нажата **НЕ будет**.

---

### 6.6. Реальная отправка отклика

Только после успешного `dry-run`:

```bash
python3 -m vacancy_agent.cli approve-submit \
 <vacancy_id> \
 --send \
 --cdp http://127.0.0.1:9222
```

Пример:

```bash
python3 -m vacancy_agent.cli approve-submit \
 bf155f3882bb48de \
 --send \
 --cdp http://127.0.0.1:9222
```

---

## 7. Рекомендуемый полный сценарий проверки

```bash
cd /Users/pipyao/.openclaw/workspace-coding/skills/vacancy-agent-skill-application-flow/project

source ../.venv/bin/activate

python3 -m vacancy_agent.cli status

python3 -m vacancy_agent.cli search-url \
 "https://hh.ru/search/vacancy?text=Flutter&area=1" \
 --max-pages 1 \
 --max-vacancies 5 \
 --cdp http://127.0.0.1:9222

python3 -m vacancy_agent.cli list --all --limit 20

python3 -m vacancy_agent.cli approve-submit \
 <vacancy_id> \
 --dry-run \
 --cdp http://127.0.0.1:9222
```

Если `dry-run` прошёл успешно:

```bash
python3 -m vacancy_agent.cli approve-submit \
 <vacancy_id> \
 --send \
 --cdp http://127.0.0.1:9222
```

---

## 8. Статусы отклика

### Успешные статусы

- `success`
- `dry_run_success`
- `already_applied`

### Статусы ручной обработки

- `login_required`
- `captcha_required`
- `questionnaire_required`
- `test_required`

### Терминальные пропуски

- `archived`
- `unsupported_platform`

### Ошибки

- `apply_button_not_found`
- `cover_letter_field_not_found`
- `submit_button_not_found`
- `validation_failed`
- `submit_failed`
- `timeout`
- `unknown_error`

---

## 9. Архивные вакансии

Если HH показывает блок:

```text
Вакансия в архиве
```

или элемент:

```html
[data-qa="vacancy-archive-description"]
```

агент должен вернуть статус:

`archived`

И вакансия должна быть помечена как:

`skipped`

Это нормальная ситуация, не ошибка.

---

## 10. Анкеты и тесты работодателя

Если при отклике появляется обязательная анкета работодателя или тестовое задание, агент не должен пытаться отправить отклик автоматически.

Ожидаемые статусы:

- `questionnaire_required`
- `test_required`

Такие вакансии нужно обрабатывать вручную.

---

## 11. Капча и логин

Если HH просит авторизоваться:

`login_required`

Если HH показывает капчу или антибот-проверку:

`captcha_required`

В этих случаях агент останавливается.

---

## 12. Очистка вакансий

Из папки `project`:

```bash
cp -r data "data_backup_$(date +%Y%m%d_%H%M%S)"

rm -f data/vacancies.json
rm -rf data/vacancies_by_source
mkdir -p data/vacancies_by_source
```

Очистить ещё и письма/отклики:

```bash
rm -f data/applications.json
```

Полная очистка:

```bash
cp -r data "data_backup_$(date +%Y%m%d_%H%M%S)"

rm -f data/vacancies.json
rm -f data/applications.json
rm -rf data/vacancies_by_source
mkdir -p data/vacancies_by_source
```

---

## 13. Диагностика

Проверить синтаксис:

```bash
python3 -m compileall -q vacancy_agent
```

Проверить статус:

```bash
python3 -m vacancy_agent.cli status
```

Посмотреть ошибки:

```bash
python3 -m vacancy_agent.cli list --status error --limit 30
```

Посмотреть отправленные:

```bash
python3 -m vacancy_agent.cli list --status applied --limit 30
```

Посмотреть пропущенные:

```bash
python3 -m vacancy_agent.cli list --status skipped --limit 30
```

Проверить файлы:

```bash
cat data/vacancies.json
cat data/applications.json
```

---

## 14. Как добавить новую платформу

Для новой платформы не нужно менять генерацию писем и approval-flow.

Нужно добавить новый apply-adapter.

Пример:

`vacancy_agent/apply_adapters/hirify.py`

Он должен реализовать общий интерфейс:

`ApplyAdapter`

Потом зарегистрировать его в:

`vacancy_agent/apply_adapters/registry.py`

После этого общий flow останется тем же:

```text
generate letter
 ↓
approve/edit
 ↓
apply via selected platform adapter
```

---

## 15. Роль OpenClaw / Telegram

В CLI используется консольное согласование:

`ConsoleApprovalAdapter`

В OpenClaw/Telegram должен использоваться:

`OpenClawApprovalAdapter`

Логика та же:

```text
сгенерировать письмо
 ↓
отправить пользователю в Telegram
 ↓
ждать ответ:
 - Да / Ок — одобрить;
 - новый текст — заменить письмо;
 - заново — регенерировать;
 - отмена — пропустить;
 ↓
после одобрения отправить через apply-adapter
```

Агент не должен использовать `apply_to_hh` или другой инструмент отправки без предварительного approval.

---

## 16. Главное правило безопасности

Реальный отклик отправляется только при команде:

```bash
--send
```

Безопасный режим:

```bash
--dry-run
```

Для первого теста всегда использовать:

```bash
python3 -m vacancy_agent.cli approve-submit \
 <vacancy_id> \
 --dry-run \
 --cdp http://127.0.0.1:9222
```

Только после успешного `dry-run` можно запускать:

```bash
python3 -m vacancy_agent.cli approve-submit \
 <vacancy_id> \
 --send \
 --cdp http://127.0.0.1:9222
```

---

## 17. Отклик на несколько вакансий

Агент умеет обрабатывать несколько вакансий последовательно.

**Правильный batch-flow:**

```text
вакансия 1
 ↓
генерация письма
 ↓
approval/edit
 ↓
dry-run/send
 ↓
следующая вакансия
```

Даже при batch-режиме агент не отправляет отклики молча. Для каждой вакансии отдельно появляется approval-меню:

```text
[1] Одобрить и отправить
[2] Открыть письмо в редакторе
[3] Сгенерировать заново
[4] Отклонить/пропустить
```

---

### 17.1. Отклик на несколько конкретных вакансий

Можно передать ID прямо в команде:

```bash
python3 -m vacancy_agent.cli approve-submit-many \
 bf155f3882bb48de 132166970 a12f38d9 \
 --dry-run \
 --cdp http://127.0.0.1:9222
```

Реальная отправка:

```bash
python3 -m vacancy_agent.cli approve-submit-many \
 bf155f3882bb48de 132166970 a12f38d9 \
 --send \
 --cdp http://127.0.0.1:9222
```

---

### 17.2. Отклик на вакансии из файла

Если ID много, удобнее хранить их в отдельном файле.

Создать файл:

```bash
touch selected_vacancies.txt
open -a TextEdit selected_vacancies.txt
```

Пример содержимого:

```text
# Flutter вакансии на сегодня
bf155f3882bb48de
132166970
https://hh.ru/vacancy/133241293

# Можно оставлять комментарии и пустые строки
```

Поддерживается:

- внутренний `vacancy_id`
- короткий prefix `vacancy_id`
- числовой HH id
- полный URL вакансии
- пустые строки
- комментарии через `#`

Запуск dry-run:

```bash
python3 -m vacancy_agent.cli approve-submit-many \
 --ids-file selected_vacancies.txt \
 --dry-run \
 --cdp http://127.0.0.1:9222
```

Реальная отправка:

```bash
python3 -m vacancy_agent.cli approve-submit-many \
 --ids-file selected_vacancies.txt \
 --send \
 --cdp http://127.0.0.1:9222
```

---

### 17.3. Отклик на вакансии по статусу

Например, взять первые 5 новых вакансий:

```bash
python3 -m vacancy_agent.cli approve-submit-many \
 --status new \
 --limit 5 \
 --dry-run \
 --cdp http://127.0.0.1:9222
```

Реальная отправка:

```bash
python3 -m vacancy_agent.cli approve-submit-many \
 --status new \
 --limit 5 \
 --send \
 --cdp http://127.0.0.1:9222
```

Если `--limit 0`, лимит не применяется:

```bash
python3 -m vacancy_agent.cli approve-submit-many \
 --status new \
 --limit 0 \
 --dry-run \
 --cdp http://127.0.0.1:9222
```

---

## 18. Массовая генерация сопроводительных писем

Если нужно только подготовить черновики писем без браузера и без отправки, используется команда:

```bash
python3 -m vacancy_agent.cli apply-many
```

---

### 18.1. Генерация писем для конкретных ID

```bash
python3 -m vacancy_agent.cli apply-many \
 bf155f3882bb48de 132166970 a12f38d9
```

---

### 18.2. Генерация писем из файла

```bash
python3 -m vacancy_agent.cli apply-many \
 --ids-file selected_vacancies.txt
```

---

### 18.3. Генерация писем по статусу

```bash
python3 -m vacancy_agent.cli apply-many \
 --status new \
 --limit 10
```

---

## 19. Формат файла selected_vacancies.txt

Файл может лежать в корне `project`:

`project/selected_vacancies.txt`

Пример:

```text
# Можно указывать внутренние ID из команды list
bf155f3882bb48de

# Можно указывать HH ID
132166970

# Можно указывать полный URL
https://hh.ru/vacancy/133241293

# Комментарии и пустые строки игнорируются
```

Запуск:

```bash
python3 -m vacancy_agent.cli approve-submit-many \
 --ids-file selected_vacancies.txt \
 --dry-run \
 --cdp http://127.0.0.1:9222
```

---

## 20. Рекомендуемый batch-сценарий

```bash
# 1. Найти вакансии
python3 -m vacancy_agent.cli search-url \
 "https://hh.ru/search/vacancy?text=Flutter&area=1" \
 --max-pages 1 \
 --max-vacancies 20 \
 --cdp http://127.0.0.1:9222

# 2. Посмотреть список
python3 -m vacancy_agent.cli list --all --limit 30

# 3. Создать файл с выбранными вакансиями
touch selected_vacancies.txt
open -a TextEdit selected_vacancies.txt

# 4. Прогнать выбранные вакансии без реальной отправки
python3 -m vacancy_agent.cli approve-submit-many \
 --ids-file selected_vacancies.txt \
 --dry-run \
 --cdp http://127.0.0.1:9222

# 5. После успешной проверки отправить реально
python3 -m vacancy_agent.cli approve-submit-many \
 --ids-file selected_vacancies.txt \
 --send \
 --cdp http://127.0.0.1:9222
```

---

## 21. Batch-статусы

Для каждой вакансии batch-flow сохраняет отдельный результат.

Ожидаемые статусы:

- `success`
- `dry_run_success`
- `already_applied`
- `archived`
- `questionnaire_required`
- `test_required`
- `captcha_required`
- `login_required`
- `unsupported_platform`
- `validation_failed`
- `submit_failed`
- `timeout`
- `unknown_error`

Если одна вакансия завершилась ошибкой, остальные вакансии из batch должны продолжать обрабатываться дальше.