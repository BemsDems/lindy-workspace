# Vacancy Agent

CLI-инструмент для сбора вакансий, сохранения результатов и подготовки сопроводительных писем.

## Возможности MVP

- добавление источников вакансий;
- поиск вакансий по URL или по сохранённым источникам;
- сбор ссылок через Playwright;
- извлечение данных через BeautifulSoup;
- хранение в JSON;
- удаление дублей;
- просмотр вакансий;
- экспорт в JSON/CSV/XLSX;
- генерация сопроводительного письма через Jinja2-шаблон;
- интерактивный apply-flow без автоматической отправки отклика.

## Установка

```bash
pip install -r requirements.txt

# Если вы запускаете в CDP-режиме (подключение к уже запущенному Edge/Chrome),
# устанавливать браузеры Playwright НЕ нужно.
#
# playwright install chromium
```

## Запуск

```bash
python -m vacancy_agent.cli init-profile
python -m vacancy_agent.cli add-source --name example --url "https://example.com/jobs"
python -m vacancy_agent.cli search-url "https://example.com/jobs"
python -m vacancy_agent.cli list
python -m vacancy_agent.cli show <vacancy_id_prefix>
python -m vacancy_agent.cli apply <vacancy_id_prefix>
python -m vacancy_agent.cli export --format xlsx
```

После установки через `pyproject.toml` можно использовать:

```bash
vacancy-cli list
```

## Важно

MVP не отправляет отклики автоматически. Команда `apply` создаёт и сохраняет черновик, а также может открыть ссылку на вакансию для ручной отправки.

## Как он ищет вакансии

MVP работает так:

1. Открывает страницу-источник через Playwright.
2. Ищет на странице ссылки на вакансии.
3. По умолчанию используются ссылки вида:
   - `a[href*='vacancy']`
   - `a[href*='job']`
   - `a[href*='career']`
   - `a[href*='position']`
4. Открывает каждую найденную ссылку.
5. Извлекает данные через CSS-селекторы:
   - `title`
   - `company`
   - `salary`
   - `location`
   - `description`
6. Сохраняет результат в `data/vacancies.json`.

### HH.ru: как выполняется поиск (CDP, read-only)

Для HH мы опираемся на браузерный DOM через CDP (т.е. уже запущенный Edge/Chrome). Поиск выполняется так:

1) Открыть главную (региональную) страницу, например:
- `https://nalchik.hh.ru/`

2) Найти поле ввода запроса по стабильному селектору:
- `input[data-qa="search-input"]` (также имеет `name="text"`)

3) Ввести строку запроса **(любой текст)** — например `flutter`, но может быть что угодно.

4) Нажать `Enter`.

5) Браузер перейдёт на страницу выдачи:
- `/search/vacancy?text=<query>&...`

Дальше агент собирает только ссылки вакансий вида `/vacancy/<digits>` и пропускает `/employer/*`, `/applicant/*` и любые URL с `vacancy_response`.

Для конкретного сайта лучше указывать селекторы при добавлении источника:

```bash
python -m vacancy_agent.cli add-source \
  --name example \
  --url "https://example.com/jobs" \
  --vacancy-link-selector "a.job-card" \
  --title-selector "h1.job-title" \
  --company-selector ".company-name" \
  --description-selector ".job-description"
```

Если сайт поддерживает поиск через query-параметры, можно указать их:

```bash
python -m vacancy_agent.cli add-source --name example --url "https://example.com/jobs" --query-param "q" --city-param "city"
```

После этого:

```bash
python -m vacancy_agent.cli search --source example --query "Python developer" --city "Москва"
```

## Подключение к браузеру через SSH/CDP

Инструмент не подключается к браузеру “по SSH” напрямую. Правильная схема такая:

1. На удалённой машине запускается Chrome/Edge/Chromium с открытым CDP-портом.
2. Через SSH создаётся локальный туннель к этому порту.
3. CLI подключается к браузеру через Playwright `connect_over_cdp`.

### 1. Запуск браузера на удалённой машине

Linux пример:

```bash
google-chrome --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir=/tmp/vacancy-agent-chrome
```

Если используется Chromium:

```bash
chromium --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir=/tmp/vacancy-agent-chromium
```

### 2. SSH-туннель с локальной машины

```bash
ssh -L 9222:127.0.0.1:9222 user@server
```

После этого локально CDP будет доступен здесь:

```text
http://127.0.0.1:9222
```

### 3. Запуск поиска через удалённый браузер

По прямой ссылке:

```bash
python -m vacancy_agent.cli search-url "https://example.com/jobs" --cdp http://127.0.0.1:9222
```

По сохранённому источнику:

```bash
python -m vacancy_agent.cli search --source example --cdp http://127.0.0.1:9222
```

Такой режим удобен, если браузер уже авторизован или работает на отдельной машине. Инструмент не обходит капчи и ограничения сайтов, а только использует доступный пользователю браузерный сеанс.

## Apply adapters: отправка откликов через платформенный слой

В проект добавлен отдельный слой `vacancy_agent/apply_adapters/` для отправки откликов. Это повторяет идею `query_adapters`: агент не должен знать DOM конкретного сайта, он вызывает единый сервис, а сервис сам выбирает адаптер по URL вакансии.

Сейчас реализован адаптер:

- `HHApplyAdapter` — `hh.ru` / `hh.kz`.

Что делает HH-адаптер:

1. открывает страницу вакансии;
2. нажимает `Откликнуться`;
3. если HH показывает предупреждение `Вы откликаетесь на вакансию в другой стране`, нажимает `Все равно откликнуться`;
4. останавливается, если появились анкета работодателя, тест, логин или капча;
5. вставляет сопроводительное письмо в `textarea[name="text"]`;
6. в `dry_run` не отправляет финальную форму;
7. при `--send` нажимает финальную кнопку `Отправить` и ждёт подтверждение `Резюме доставлено`.

Безопасный тест:

```bash
python -m vacancy_agent.cli submit <vacancy_id> --dry-run --cdp http://127.0.0.1:9222
```

Реальная отправка после ручного согласования письма:

```bash
python -m vacancy_agent.cli submit <vacancy_id> --send --cdp http://127.0.0.1:9222
```

Можно передать утверждённое письмо из файла:

```bash
python -m vacancy_agent.cli submit <vacancy_id> --letter-file approved_letter.txt --dry-run
```

Если для вакансии появляется анкета/тест/капча/логин, адаптер не пытается обходить это автоматически и возвращает статус для ручной обработки.

Подробнее см. `references/apply-adapters.md`.


### Генерация сопроводительных писем

Продакшен-генерация писем вынесена в отдельный skill `cover-letter-gen`.
`vacancy-agent` вызывает его через `vacancy_agent.letter_adapters.CoverLetterGenAdapter`, а сам отвечает за поиск вакансий, approval/edit и отправку через платформенные apply-adapters.

По умолчанию `COVER_LETTER_PROVIDER=auto`: агент ищет sibling-папку `cover-letter-gen`. Если путь другой, укажи:

```bash
COVER_LETTER_GEN_PATH=/absolute/path/to/cover-letter-gen
```

Локальный шаблонный генератор остался только как fallback/offline-режим: `COVER_LETTER_PROVIDER=simple`.
