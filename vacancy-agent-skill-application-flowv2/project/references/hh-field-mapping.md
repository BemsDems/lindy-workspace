# HH.ru vacancy field mapping (CDP/Playwright DOM)

This document explains **where each structured Vacancy field comes from** for HH.ru pages and which **stable selectors** we use.

> Important: these selectors are reliably present in the **browser-rendered DOM** (via Playwright + CDP). They may be missing in raw HTML fetched via `requests`.

## Source page types

### Search results page
- Example: `https://nalchik.hh.ru/search/vacancy?...`
- Goal: collect only vacancy URLs.
- Guardrails:
  - **Allow** only paths matching: `/vacancy/<digits>`
  - **Deny** any URLs under `/employer/` and `/applicant/` (including `vacancy_response`)

### Vacancy page
- Example: `https://nalchik.hh.ru/vacancy/<id>`
- Goal: extract structured fields + description.

## Field mapping

### `Vacancy.title`
- Primary selector(s):
  - `h1`
  - `[data-qa='vacancy-title']`

### `Vacancy.company`
- Primary selector(s):
  - `[data-qa='vacancy-company-name']`

### `Vacancy.salary`
- Primary selector(s):
  - `[data-qa='vacancy-salary']`

### `Vacancy.location`
- Primary selector(s):
  - `[data-qa='vacancy-view-location']`
  - `[data-qa='vacancy-location']`

### `Vacancy.description`
- Primary selector(s):
  - `[data-qa='vacancy-description']`
- Fallbacks:
  - `main` (with script/style stripping)

### `Vacancy.employment_type`
- Prefer stable `data-qa`:
  - `[data-qa='common-employment-text']`
- Typical values:
  - `Полная занятость`
  - `Частичная занятость`
  - `Проектная работа`
  - `Стажировка`

### `Vacancy.experience`
- Prefer stable `data-qa`:
  - `[data-qa='vacancy-experience']`
  - `[data-qa='work-experience-text']`
- Typical values:
  - `Без опыта`
  - `1–3 года` / `1-3 года`
  - `3–6 лет` / `3-6 лет`
  - `более 6 лет`

### `Vacancy.work_schedule`
- Prefer stable `data-qa`:
  - `[data-qa='work-schedule-by-days-text']`
- Typical values:
  - `График 5/2`, `График 2/2`, `График 6/1`, ...

### `Vacancy.working_hours`
- Prefer stable `data-qa`:
  - `[data-qa='working-hours-text']`
- Typical values:
  - `Рабочие часы 8`

### `Vacancy.work_format`
- Current implementation: heuristic detection from `(description + location)` text.
- Notes:
  - HH also has labels like `Удалённый формат` (remote) which appear in the DOM.
  - If you want a selector-based mapping, we observed:
    - `[data-qa='vacancy-label-work-schedule-remote']` (remote label)

## Notes / gotchas

- HH DOM may split some labels into separate nodes (e.g. `Опыт` and `1–3 года`), so we normalize dash variants.
- For CDP runs we recommend using an already running, logged-in Edge session.
- The scraper is designed to be **read-only** (no clicks on apply).
