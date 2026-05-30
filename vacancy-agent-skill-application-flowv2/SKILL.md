---
name: vacancy-agent-skill
description: Run and maintain the Vacancy Agent Python CLI that scrapes job vacancies via Playwright, deduplicates and stores results, and generates cover-letter drafts. Use when you need to copy this project into a skill, apply reliability/security fixes (async race conditions, resource cleanup, UTC timestamps), add URL/path validation, refactor JSON storage utilities, or package the skill for reuse.
---

# Vacancy Agent Skill

This skill bundles the `vacancy-agent` project (Python/Typer/Playwright) under `project/` and provides a safe workflow for running it and applying fixes.

## Quick start

```bash
cd skills/vacancy-agent-skill/project
python3 -m pip install -r requirements.txt
playwright install chromium
python3 -m vacancy_agent.cli version
```

## What’s inside

- `project/` — the full source tree of Vacancy Agent
- (optional) add `references/` for extra docs/config later

## Maintenance workflow

1. Make changes under `project/vacancy_agent/...`
2. Run formatting/lint (if configured)
3. Run a minimal smoke test:

```bash
python3 -m vacancy_agent.cli init-profile
python3 -m vacancy_agent.cli sources
python3 -m vacancy_agent.cli status
```

## Changes applied in this version

- Fix `BrowserManager.start()` race condition with an `asyncio.Lock`.
- Make timestamps UTC (`datetime.now(timezone.utc)`) for Pydantic models.
- Add basic validation for URLs in CLI and safe output path enforcement for exports.
- Refactor JSON storage load helpers to reduce duplication.
- Add `published_at` field to `Vacancy` model and implement `_extract_hh_published_at` in `VacancyExtractor` — parses Russian month names ("8 апреля 2026") and numeric format ("20.04.2026").
- Add `_check_hh_authenticated()` in `GenericPlaywrightSource` — before scanning HH, checks if user is logged in by looking for a login link (`a[href*="account/login"]`). If not authenticated, logs a warning and stops the scan.


## Apply-adapter layer

This version adds a platform-neutral application layer:

- `project/vacancy_agent/apply_adapters/base.py` — common `ApplyAdapter`, `ApplyStatus`, `ApplyResult` contract.
- `project/vacancy_agent/apply_adapters/hh.py` — HH.ru/HH.kz Playwright apply flow.
- `project/vacancy_agent/apply_adapters/registry.py` — platform adapter registry.
- `project/vacancy_agent/apply_service.py` — platform-neutral service used by the CLI/agent.
- CLI command: `python -m vacancy_agent.cli submit <vacancy_id> --dry-run` for safe testing and `--send` for real submission after human approval.

Use this layer for future job boards instead of hard-coding platform logic in `ApplyManager` or agent prompts.


### Генерация сопроводительных писем

Продакшен-генерация писем вынесена в отдельный skill `cover-letter-gen`.
`vacancy-agent` вызывает его через `vacancy_agent.letter_adapters.CoverLetterGenAdapter`, а сам отвечает за поиск вакансий, approval/edit и отправку через платформенные apply-adapters.

По умолчанию `COVER_LETTER_PROVIDER=auto`: агент ищет sibling-папку `cover-letter-gen`. Если путь другой, укажи:

```bash
COVER_LETTER_GEN_PATH=/absolute/path/to/cover-letter-gen
```

Локальный шаблонный генератор остался только как fallback/offline-режим: `COVER_LETTER_PROVIDER=simple`.
