# Apply adapters

`vacancy_agent.apply_adapters` is the platform layer for sending applications.

The rest of the agent should call only:

```python
from vacancy_agent.apply_service import apply_service

result = await apply_service.apply(vacancy, approved_cover_letter, dry_run=True)
```

The service chooses a platform adapter by `vacancy.url`.

## Current adapters

- `HHApplyAdapter` — supports `hh.ru` and `hh.kz`.

## Add a new platform

1. Create a new file, for example:

```text
vacancy_agent/apply_adapters/hirify.py
```

2. Implement the `ApplyAdapter` protocol:

```python
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Page

from vacancy_agent.apply_adapters.base import ApplyAdapter, ApplyResult, ApplyStatus
from vacancy_agent.schemas import Vacancy


@dataclass(slots=True)
class HirifyApplyAdapter(ApplyAdapter):
    platform: str = "hirify"

    def can_handle(self, vacancy: Vacancy) -> bool:
        return urlparse(vacancy.url).netloc.endswith("hirify.me")

    async def apply(self, page: Page, vacancy: Vacancy, cover_letter: str, *, dry_run: bool = True) -> ApplyResult:
        # TODO: implement selectors and flow for Hirify.
        return ApplyResult(
            status=ApplyStatus.UNSUPPORTED_PLATFORM,
            vacancy_url=vacancy.url,
            platform=self.platform,
            message="Hirify apply flow is not implemented yet.",
        )
```

3. Register it in `vacancy_agent/apply_adapters/registry.py`:

```python
from vacancy_agent.apply_adapters.hirify import HirifyApplyAdapter

_ADAPTERS = [
    HHApplyAdapter(),
    HirifyApplyAdapter(),
]
```

## Guardrails

Adapters must return a manual status instead of trying to bypass blockers:

- `LOGIN_REQUIRED`
- `CAPTCHA_REQUIRED`
- `QUESTIONNAIRE_REQUIRED`
- `TEST_REQUIRED`

Use `dry_run=True` as the default when testing any new adapter. In dry-run mode, the adapter may open the form and fill the letter, but must not press the final submit button.

## End-to-end flow with approval adapters

`apply_adapters` отвечают только за платформенную отправку: HH, Hirify, LinkedIn и т.д.
Логика генерации письма и ожидания решения человека вынесена выше, в `application_flow.py`.

Слои:

```text
build_cover_letter(vacancy, candidate)
  -> approval_adapter.request_approval(...)
  -> apply_service.apply(...)
  -> platform adapter: HHApplyAdapter / future adapters
```

Human-in-the-loop тоже сделан адаптером:

- `ConsoleApprovalAdapter` — локальный CLI: approve/edit/regenerate/reject.
- `OpenClawApprovalAdapter` — Telegram/чат через context OpenClaw. Все SDK-зависимые методы изолированы в одном файле.

Так можно позже заменить Telegram на Slack/Web UI, не меняя HH-адаптеры, и добавить другие job board adapters, не меняя approval-flow.

## Cover-letter generation adapter

`vacancy-agent` no longer owns the production cover-letter generation logic. The flow uses `vacancy_agent.letter_adapters`:

- `CoverLetterGenAdapter` calls the external `cover-letter-gen` skill and its 3-pass pipeline: Analyze -> Write -> Validate -> Rewrite.
- `SimpleTemplateLetterAdapter` remains only as an offline fallback.

Configuration:

```bash
COVER_LETTER_PROVIDER=auto
COVER_LETTER_GEN_PATH=/absolute/path/to/cover-letter-gen
# optional:
COVER_LETTER_GEN_RESUME=/absolute/path/to/cover-letter-gen/config/resume.yaml
COVER_LETTER_GEN_SETTINGS=/absolute/path/to/cover-letter-gen/config/settings.yaml
```

If both skills are siblings in an OpenClaw workspace, `auto` usually finds `../cover-letter-gen` automatically.
