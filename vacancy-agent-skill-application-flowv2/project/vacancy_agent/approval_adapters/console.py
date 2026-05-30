from __future__ import annotations

import asyncio

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from vacancy_agent.approval_adapters.base import (
    ApprovalAdapter,
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
)
from vacancy_agent.approval_adapters.editor import CoverLetterEditor, EditorOpenError


class ConsoleApprovalAdapter(ApprovalAdapter):
    channel = "console"

    def __init__(self) -> None:
        self.console = Console()
        self.editor = CoverLetterEditor()

    async def notify(self, text: str) -> None:
        self.console.print(f"[yellow]{text}[/yellow]")

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        vacancy = request.vacancy

        self.console.print(
            Panel(
                f"[bold]{vacancy.title}[/bold]\n"
                f"Компания: {vacancy.company}\n"
                f"URL: {vacancy.url}\n"
                f"Попытка: {request.attempt}",
                title="Human approval",
                border_style="blue",
            )
        )

        self.console.print(
            Panel(
                request.draft_text,
                title="Черновик письма",
                border_style="green",
            )
        )

        self._print_generation_notes(request)

        self.console.print()
        self.console.print("[1] Одобрить и отправить")
        self.console.print("[2] Открыть письмо в редакторе")
        self.console.print("[3] Сгенерировать заново")
        self.console.print("[4] Отклонить/пропустить")
        self.console.print("[5] Сохранить как черновик")

        choice = Prompt.ask("Решение", choices=["1", "2", "3", "4", "5"], default="1")

        if choice == "1":
            return ApprovalResult(
                status=ApprovalStatus.APPROVED,
                text=request.draft_text,
            )

        if choice == "2":
            try:
                edited = await asyncio.to_thread(self.editor.edit, request)
            except EditorOpenError as error:
                return ApprovalResult(
                    status=ApprovalStatus.REJECTED,
                    message=str(error),
                )

            return ApprovalResult(
                status=ApprovalStatus.EDITED,
                text=edited,
            )

        if choice == "3":
            return ApprovalResult(
                status=ApprovalStatus.REGENERATE,
                message="Пользователь запросил регенерацию",
            )

        if choice == "5":
            return ApprovalResult(
                status=ApprovalStatus.DRAFT,
                text=request.draft_text,
                message="Письмо сохранено как черновик. Отклик отменён.",
            )

        return ApprovalResult(
            status=ApprovalStatus.REJECTED,
            message="Пользователь отклонил отклик",
        )

    def _print_generation_notes(self, request: ApprovalRequest) -> None:
        generation = request.generation

        if not generation:
            return

        if generation.matched_requirements:
            self.console.print(
                "[green]Совпадения:[/green] "
                + ", ".join(generation.matched_requirements)
            )

        if generation.missing_requirements:
            self.console.print(
                "[yellow]Не найдено в профиле:[/yellow] "
                + ", ".join(generation.missing_requirements)
            )

        if generation.risk_notes:
            for note in generation.risk_notes:
                self.console.print(f"[yellow]⚠ {note}[/yellow]")