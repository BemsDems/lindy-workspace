from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from vacancy_agent.schemas import CoverLetterResponse, Vacancy


class ApprovalStatus(str, Enum):
    """Platform-neutral human approval status."""

    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"
    REGENERATE = "regenerate"
    TIMEOUT = "timeout"
    DRAFT = "draft"


@dataclass(slots=True)
class ApprovalRequest:
    """Draft that must be approved by a human before submit."""

    vacancy: Vacancy
    draft_text: str
    generation: CoverLetterResponse | None = None
    attempt: int = 1


@dataclass(slots=True)
class ApprovalResult:
    """Result returned by any human-approval adapter."""

    status: ApprovalStatus
    text: str | None = None
    message: str | None = None

    @property
    def approved_text(self) -> str | None:
        if self.status in {ApprovalStatus.APPROVED, ApprovalStatus.EDITED}:
            return self.text
        return None


class ApprovalAdapter(Protocol):
    """Interface for a human-in-the-loop channel.

    Implementations can be CLI prompts, OpenClaw Telegram context, Slack, Web UI,
    etc. The application flow does not care where approval comes from.
    """

    channel: str

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResult:
        """Ask a human to approve/edit/reject/regenerate a cover letter."""

        ...
