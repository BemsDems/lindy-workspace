from __future__ import annotations

import asyncio
import webbrowser
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from vacancy_agent.apply_adapters import ApplyStatus, registered_platforms
from vacancy_agent.apply_service import apply_service
from vacancy_agent.letter_generation import generate_cover_letter_for_vacancy_sync
from vacancy_agent.schemas import ApplicationDraft, VacancyStatus
from vacancy_agent.storage import storage
from vacancy_agent.utils.ids import make_id


class ApplyManager:
    def __init__(self):
        self.console = Console()

    def apply_to_vacancy(self, vacancy_id_or_prefix: str) -> bool:
        """Prepare and save a cover-letter draft.

        This is the original safe/manual flow. It does not submit anything on a
        website. Use `submit_to_vacancy(..., dry_run=False)` for actual
        platform submission after human approval.
        """

        vacancy = storage.find_vacancy(vacancy_id_or_prefix)
        if not vacancy:
            self.console.print(f"[red]Вакансия не найдена: {vacancy_id_or_prefix}[/red]")
            return False

        candidate = storage.load_candidate_profile()
        if not candidate:
            self.console.print("[red]Профиль кандидата не найден. Выполни: init-profile[/red]")
            return False

        response = generate_cover_letter_for_vacancy_sync(vacancy, candidate).to_cover_letter_response()

        while True:
            self._display_vacancy(vacancy)
            self._display_cover_letter(response.cover_letter)

            if response.matched_requirements:
                self.console.print("[green]Совпадения:[/green] " + ", ".join(response.matched_requirements))
            if response.missing_requirements:
                self.console.print("[yellow]Не найдено в профиле:[/yellow] " + ", ".join(response.missing_requirements))
            if response.risk_notes:
                for note in response.risk_notes:
                    self.console.print(f"[yellow]⚠ {note}[/yellow]")

            self.console.print()
            self.console.print("[1] Сохранить черновик")
            self.console.print("[2] Отредактировать и сохранить")
            self.console.print("[3] Сгенерировать заново")
            self.console.print("[4] Открыть вакансию в браузере")
            self.console.print("[5] Отметить как отправленный")
            self.console.print("[6] Пропустить")

            choice = Prompt.ask("Выберите действие", choices=["1", "2", "3", "4", "5", "6"], default="1")

            if choice == "1":
                self._save_draft(vacancy.id, vacancy.url, response.cover_letter)
                storage.update_vacancy_status(vacancy.id, VacancyStatus.DRAFT)
                self.console.print("[green]Черновик сохранён[/green]")
                return True

            if choice == "2":
                edited = Prompt.ask("Вставьте отредактированный текст", default=response.cover_letter)
                self._save_draft(vacancy.id, vacancy.url, edited)
                storage.update_vacancy_status(vacancy.id, VacancyStatus.DRAFT)
                self.console.print("[green]Отредактированный черновик сохранён[/green]")
                return True

            if choice == "3":
                response = generate_cover_letter_for_vacancy_sync(vacancy, candidate).to_cover_letter_response()
                continue

            if choice == "4":
                webbrowser.open(vacancy.url)
                continue

            if choice == "5":
                if Confirm.ask("Отметить отклик как отправленный? Это не отправляет форму на сайте."):
                    self._save_draft(vacancy.id, vacancy.url, response.cover_letter, status="sent")
                    storage.update_vacancy_status(vacancy.id, VacancyStatus.APPLIED)
                    storage.set_vacancy_applied_by_us(vacancy.id, True)
                    self.console.print("[green]Отклик отмечен как отправленный[/green]")
                    return True

            if choice == "6":
                storage.update_vacancy_status(vacancy.id, VacancyStatus.SKIPPED)
                self.console.print("[yellow]Вакансия пропущена[/yellow]")
                return True

    def submit_to_vacancy(
        self,
        vacancy_id_or_prefix: str,
        *,
        cover_letter: str | None = None,
        letter_file: Path | None = None,
        dry_run: bool = True,
    ) -> bool:
        """Submit a vacancy application through a platform adapter.

        Platform routing is adapter-based. Today the registered apply adapter is
        HH (`hh.ru`/`hh.kz`). Future platforms only need a new adapter in
        `vacancy_agent/apply_adapters/` plus registry registration.
        """

        vacancy = storage.find_vacancy(vacancy_id_or_prefix)
        if not vacancy:
            self.console.print(f"[red]Вакансия не найдена: {vacancy_id_or_prefix}[/red]")
            return False

        letter_text = self._resolve_cover_letter(vacancy.id, cover_letter=cover_letter, letter_file=letter_file)
        if not letter_text:
            candidate = storage.load_candidate_profile()
            if not candidate:
                self.console.print("[red]Профиль кандидата не найден. Выполни: init-profile[/red]")
                return False
            response = generate_cover_letter_for_vacancy_sync(vacancy, candidate).to_cover_letter_response()
            letter_text = response.cover_letter
            self._save_draft(vacancy.id, vacancy.url, letter_text, status="draft")
            storage.update_vacancy_status(vacancy.id, VacancyStatus.DRAFT)

        self._display_vacancy(vacancy)
        self._display_cover_letter(letter_text)

        self.console.print(
            f"[cyan]Apply adapters:[/cyan] {', '.join(registered_platforms()) or 'none'}"
        )
        self.console.print(
            "[yellow]dry-run включён: финальная кнопка отправки не будет нажата.[/yellow]"
            if dry_run
            else "[red]dry-run выключен: адаптер может нажать финальную кнопку отправки.[/red]"
        )

        if not dry_run:
            confirmed = Confirm.ask(
                "Отправить отклик на сайте через Playwright?",
                default=False,
            )
            if not confirmed:
                self.console.print("[yellow]Отправка отменена пользователем[/yellow]")
                return False

        result = asyncio.run(
            apply_service.apply(
                vacancy=vacancy,
                cover_letter=letter_text,
                dry_run=dry_run,
            )
        )

        style = "green" if result.is_success else "yellow" if result.needs_manual_action else "red"
        self.console.print(
            Panel(
                f"Платформа: {result.platform}\n"
                f"Статус: {result.status.value}\n"
                f"URL: {result.vacancy_url}\n"
                f"Сообщение: {result.message or '—'}",
                title="Результат apply-adapter",
                border_style=style,
            )
        )

        if result.status == ApplyStatus.DRY_RUN_SUCCESS:
            self._save_draft(vacancy.id, vacancy.url, letter_text, status="dry_run")
            storage.update_vacancy_status(vacancy.id, VacancyStatus.DRAFT)
            return True

        if result.was_sent:
            self._save_draft(vacancy.id, vacancy.url, letter_text, status="sent")
            storage.update_vacancy_status(vacancy.id, VacancyStatus.APPLIED)
            storage.set_vacancy_applied_by_us(vacancy.id, True)
            return True

        if result.status == ApplyStatus.ARCHIVED:
            self._save_draft(vacancy.id, vacancy.url, letter_text, status=result.status.value)
            storage.update_vacancy_status(vacancy.id, VacancyStatus.SKIPPED)
            return False

        if result.needs_manual_action:
            self._save_draft(vacancy.id, vacancy.url, letter_text, status=result.status.value)
            return False

        storage.update_vacancy_status(vacancy.id, VacancyStatus.ERROR)
        self._save_draft(vacancy.id, vacancy.url, letter_text, status=result.status.value)
        return False

    def _resolve_cover_letter(
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
        if latest:
            return latest.cover_letter.strip()

        return None

    def _latest_draft_for_vacancy(self, vacancy_id: str) -> ApplicationDraft | None:
        drafts = [item for item in storage.load_applications() if item.vacancy_id == vacancy_id]
        if not drafts:
            return None
        return sorted(drafts, key=lambda item: item.updated_at, reverse=True)[0]

    def _display_vacancy(self, vacancy) -> None:
        body = (
            f"[bold]{vacancy.title}[/bold]\n"
            f"Компания: {vacancy.company}\n"
            f"Зарплата: {vacancy.salary or 'не указана'}\n"
            f"Локация: {vacancy.location or 'не указана'}\n"
            f"Ссылка: {vacancy.url}"
        )
        self.console.print(Panel(body, title=f"Вакансия {vacancy.id}", border_style="blue"))

    def _display_cover_letter(self, text: str) -> None:
        self.console.print(Panel(text, title="Сопроводительное письмо", border_style="green"))

    def _save_draft(self, vacancy_id: str, vacancy_url: str, text: str, status: str = "draft") -> None:
        now = datetime.now()
        draft = ApplicationDraft(
            id=make_id(vacancy_id),
            vacancy_id=vacancy_id,
            vacancy_url=vacancy_url,
            cover_letter=text,
            status=status,
            updated_at=now,
        )
        storage.upsert_application(draft)


apply_manager = ApplyManager()
