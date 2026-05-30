from __future__ import annotations

import asyncio
from typing import Any

from vacancy_agent.approval_adapters.base import (
    ApprovalAdapter,
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
)


APPROVE_WORDS = {"да", "ок", "ok", "yes", "y", "+", "отправить", "отправляй", "approve"}
REJECT_WORDS = {"нет", "no", "n", "отмена", "cancel", "отклонить", "skip", "пропустить"}
REGENERATE_WORDS = {"заново", "перепиши", "перегенерируй", "regenerate", "rewrite"}


class OpenClawApprovalAdapter(ApprovalAdapter):
    """Human approval adapter that delegates messages to OpenClaw context.

    Keep all SDK-specific method names isolated here. If your OpenClaw SDK uses
    different names, update `_send_message` and `_wait_for_user_reply` only.
    """

    channel = "openclaw"

    def __init__(self, context: Any, *, timeout_seconds: float = 3600.0) -> None:
        self.context = context
        self.timeout_seconds = timeout_seconds

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        vacancy = request.vacancy
        text = (
            f"📝 Вакансия: {vacancy.title}\n"
            f"Компания: {vacancy.company}\n"
            f"ID: {vacancy.id}\n"
            f"URL: {vacancy.url}\n\n"
            f"Черновик:\n{request.draft_text}\n\n"
            "Ответь:\n"
            "• `Да` / `Ок` — одобрить и отправить;\n"
            "• пришли новый текст — заменить письмо и отправить после валидации;\n"
            "• `заново` — сгенерировать другой вариант;\n"
            "• `отмена` — пропустить вакансию."
        )
        await self._send_message(text)

        try:
            reply = await asyncio.wait_for(self._wait_for_user_reply(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return ApprovalResult(status=ApprovalStatus.TIMEOUT, message="Таймаут ожидания ответа пользователя")

        reply_text = str(reply).strip()
        normalized = reply_text.lower()

        if normalized in APPROVE_WORDS:
            return ApprovalResult(status=ApprovalStatus.APPROVED, text=request.draft_text)
        if normalized in REJECT_WORDS:
            return ApprovalResult(status=ApprovalStatus.REJECTED, message="Пользователь отменил отклик")
        if normalized in REGENERATE_WORDS:
            return ApprovalResult(status=ApprovalStatus.REGENERATE, message="Пользователь запросил новый вариант")

        return ApprovalResult(status=ApprovalStatus.EDITED, text=reply_text)

    async def notify(self, text: str) -> None:
        await self._send_message(text)

    async def _send_message(self, text: str) -> None:
        for method_name in ("send_message", "reply", "send", "prompt_user"):
            method = getattr(self.context, method_name, None)
            if method is None:
                continue
            result = method(text)
            if hasattr(result, "__await__"):
                await result
            return
        raise AttributeError("OpenClaw context has no known send method")

    async def _wait_for_user_reply(self) -> str:
        for method_name in ("wait_for_user_reply", "wait_for_reply", "receive", "input"):
            method = getattr(self.context, method_name, None)
            if method is None:
                continue
            result = method()
            if hasattr(result, "__await__"):
                result = await result
            return str(result)
        raise AttributeError("OpenClaw context has no known wait-for-reply method")
