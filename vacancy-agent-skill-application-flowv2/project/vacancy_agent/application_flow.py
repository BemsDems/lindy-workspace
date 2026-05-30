from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from vacancy_agent.apply_adapters import ApplyResult, ApplyStatus
from vacancy_agent.apply_service import apply_service
from vacancy_agent.browser import browser_manager
from vacancy_agent.approval_adapters import (
    ApprovalAdapter,
    ApprovalRequest,
    ApprovalStatus,
    ConsoleApprovalAdapter,
)
from vacancy_agent.letter_generation import generate_cover_letter_for_vacancy
from vacancy_agent.schemas import ApplicationDraft, CandidateProfile, Vacancy, VacancyStatus
from vacancy_agent.storage import storage
from vacancy_agent.utils.ids import make_id


@dataclass(slots=True)
class ApplicationFlowResult:
    vacancy_id: str
    vacancy_url: str
    approved_text: str | None
    approval_status: ApprovalStatus
    apply_result: ApplyResult | None = None
    message: str | None = None

    @property
    def was_sent(self) -> bool:
        return bool(self.apply_result and self.apply_result.was_sent)


class ApplicationFlow:
    """End-to-end flow: generate -> human approval/edit -> platform apply.

    Platform submission is delegated to `apply_service`, which chooses an
    apply-adapter (HH today; other job boards later). Human approval is also an
    adapter, so CLI/OpenClaw Telegram/Slack/Web UI can reuse the same flow.
    """

    def __init__(self, approval_adapter: ApprovalAdapter | None = None) -> None:
        self.approval_adapter = approval_adapter or ConsoleApprovalAdapter()

    async def run(
        self,
        vacancy_id_or_prefix: str,
        *,
        cover_letter: str | None = None,
        letter_file: Path | None = None,
        dry_run: bool = True,
        max_regenerations: int = 2,
    ) -> ApplicationFlowResult:
        vacancy = storage.find_vacancy(vacancy_id_or_prefix)
        if not vacancy:
            raise ValueError(f"Вакансия не найдена: {vacancy_id_or_prefix}")

        candidate = storage.load_candidate_profile()
        if not candidate:
            raise ValueError("Профиль кандидата не найден. Выполни: init-profile")

        generation = None
        draft_text = self._resolve_initial_letter(vacancy.id, cover_letter=cover_letter, letter_file=letter_file)

        if not draft_text:
            generation = await generate_cover_letter_for_vacancy(vacancy, candidate)

            if not generation.cover_letter:
                return ApplicationFlowResult(
                    vacancy_id=vacancy.id,
                    vacancy_url=vacancy.url,
                    approved_text=None,
                    approval_status=ApprovalStatus.REJECTED,
                    message=f"Генератор не вернул текст письма: {generation.error or generation.risk_notes}",
                )

            valid, validation_error = self.validate_cover_letter(generation.cover_letter, candidate)

            if not valid:
                return ApplicationFlowResult(
                    vacancy_id=vacancy.id,
                    vacancy_url=vacancy.url,
                    approved_text=None,
                    approval_status=ApprovalStatus.REJECTED,
                    message=f"Сгенерированное письмо не прошло базовую проверку: {validation_error}",
                )

            draft_text = generation.cover_letter
            self._save_draft(vacancy, draft_text, status="draft")
            storage.update_vacancy_status(vacancy.id, VacancyStatus.DRAFT)

        attempt = 1
        while True:
            approval = await self.approval_adapter.request_approval(
                ApprovalRequest(
                    vacancy=vacancy,
                    draft_text=draft_text,
                    generation=generation,
                    attempt=attempt,
                )
            )

            if approval.status == ApprovalStatus.APPROVED:
                approved_text = approval.text or draft_text
                break

            if approval.status == ApprovalStatus.EDITED:
                valid, error = self.validate_cover_letter(approval.text or "", candidate)
                if valid:
                    approved_text = (approval.text or "").strip()
                    self._save_draft(vacancy, approved_text, status="approved_edited")
                    break

                # Invalid manual edit: ask again with the previous safe draft.
                await self._notify_invalid_edit(error)
                attempt += 1
                continue

            if approval.status == ApprovalStatus.DRAFT:
                draft_to_save = approval.text or draft_text

                self._save_draft(
                    vacancy,
                    draft_to_save,
                    status="draft",
                )

                storage.update_vacancy_status(vacancy.id, VacancyStatus.DRAFT)

                return ApplicationFlowResult(
                    vacancy_id=vacancy.id,
                    vacancy_url=vacancy.url,
                    approved_text=draft_to_save,
                    approval_status=ApprovalStatus.DRAFT,
                    apply_result=None,
                    message="Письмо сохранено как черновик. Отклик не выполнялся.",
                )

            if approval.status == ApprovalStatus.REGENERATE:
                if attempt > max_regenerations:
                    return ApplicationFlowResult(
                        vacancy_id=vacancy.id,
                        vacancy_url=vacancy.url,
                        approved_text=None,
                        approval_status=approval.status,
                        message="Достигнут лимит перегенераций",
                    )
                generation = await generate_cover_letter_for_vacancy(vacancy, candidate)

                if not generation.cover_letter:
                    return ApplicationFlowResult(
                        vacancy_id=vacancy.id,
                        vacancy_url=vacancy.url,
                        approved_text=None,
                        approval_status=ApprovalStatus.REJECTED,
                        message=f"Перегенерация не вернула текст письма: {generation.error or generation.risk_notes}",
                    )

                valid, validation_error = self.validate_cover_letter(generation.cover_letter, candidate)

                if not valid:
                    return ApplicationFlowResult(
                        vacancy_id=vacancy.id,
                        vacancy_url=vacancy.url,
                        approved_text=None,
                        approval_status=ApprovalStatus.REJECTED,
                        message=f"Перегенерированное письмо не прошло базовую проверку: {validation_error}",
                    )

                draft_text = generation.cover_letter
                self._save_draft(vacancy, draft_text, status="regenerated")
                attempt += 1
                continue

            self._save_draft(vacancy, draft_text, status=approval.status.value)
            if approval.status == ApprovalStatus.REJECTED:
                storage.update_vacancy_status(vacancy.id, VacancyStatus.SKIPPED)
            return ApplicationFlowResult(
                vacancy_id=vacancy.id,
                vacancy_url=vacancy.url,
                approved_text=None,
                approval_status=approval.status,
                message=approval.message,
            )

        self._save_draft(vacancy, approved_text, status="approved")

        result = await apply_service.apply(
            vacancy=vacancy,
            cover_letter=approved_text,
            dry_run=dry_run,
        )

        if result.status == ApplyStatus.DRY_RUN_SUCCESS:
            self._save_draft(vacancy, approved_text, status="dry_run")
            storage.update_vacancy_status(vacancy.id, VacancyStatus.DRAFT)
        elif result.was_sent:
            self._save_draft(vacancy, approved_text, status="sent")
            storage.update_vacancy_status(vacancy.id, VacancyStatus.APPLIED)
            storage.set_vacancy_applied_by_us(vacancy.id, True)
        elif result.status == ApplyStatus.ARCHIVED:
            self._save_draft(vacancy, approved_text, status=result.status.value)
            storage.update_vacancy_status(vacancy.id, VacancyStatus.SKIPPED)
        elif result.needs_manual_action:
            self._save_draft(vacancy, approved_text, status=result.status.value)
        else:
            self._save_draft(vacancy, approved_text, status=result.status.value)
            storage.update_vacancy_status(vacancy.id, VacancyStatus.ERROR)

        return ApplicationFlowResult(
            vacancy_id=vacancy.id,
            vacancy_url=vacancy.url,
            approved_text=approved_text,
            approval_status=ApprovalStatus.APPROVED,
            apply_result=result,
        )

    def _resolve_initial_letter(
        self,
        vacancy_id: str,
        *,
        cover_letter: str | None,
        letter_file: Path | None,
    ) -> str | None:
        if letter_file:
            return letter_file.read_text(encoding="utf-8").strip()
        if cover_letter:
            return cover_letter.strip()
        latest = self._latest_draft_for_vacancy(vacancy_id)
        return latest.cover_letter.strip() if latest else None

    def _latest_draft_for_vacancy(self, vacancy_id: str) -> ApplicationDraft | None:
        drafts = [item for item in storage.load_applications() if item.vacancy_id == vacancy_id]
        if not drafts:
            return None
        return sorted(drafts, key=lambda item: item.updated_at, reverse=True)[0]

    def _save_draft(self, vacancy: Vacancy, text: str, *, status: str) -> None:
        now = datetime.now()
        draft = ApplicationDraft(
            id=make_id(vacancy.id),
            vacancy_id=vacancy.id,
            vacancy_url=vacancy.url,
            cover_letter=text,
            status=status,
            updated_at=now,
        )
        storage.upsert_application(draft)

    async def _notify_invalid_edit(self, error: str) -> None:
        adapter = self.approval_adapter
        notify = getattr(adapter, "notify", None)
        if notify:
            result = notify(f"⚠️ Ручная правка не прошла валидацию: {error}")
            if hasattr(result, "__await__"):
                await result

    @staticmethod
    def validate_cover_letter(text: str, candidate: CandidateProfile) -> tuple[bool, str | None]:
        normalized = text.strip()
        if not normalized:
            return False, "письмо пустое"
        if len(normalized) < 80:
            return False, "письмо слишком короткое"
        if len(normalized) > 5000:
            return False, "письмо слишком длинное"

        forbidden_fragments = ["{{", "}}", "[company]", "[vacancy]", "TODO", "FIXME"]
        for fragment in forbidden_fragments:
            if fragment.lower() in normalized.lower():
                return False, f"остался шаблонный фрагмент: {fragment}"

        for restricted in candidate.restrictions:
            if restricted and restricted.lower() in normalized.lower():
                return False, f"запрещённое упоминание из профиля: {restricted}"

        return True, None


def run_application_flow(
    vacancy_id_or_prefix: str,
    *,
    cover_letter: str | None = None,
    letter_file: Path | None = None,
    dry_run: bool = True,
    approval_adapter: ApprovalAdapter | None = None,
) -> ApplicationFlowResult:
    async def _run_and_close() -> ApplicationFlowResult:
        try:
            return await ApplicationFlow(approval_adapter=approval_adapter).run(
                vacancy_id_or_prefix,
                cover_letter=cover_letter,
                letter_file=letter_file,
                dry_run=dry_run,
            )
        finally:
            await browser_manager.close()

    return asyncio.run(_run_and_close())
