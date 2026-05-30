from vacancy_agent.approval_adapters.base import (
    ApprovalAdapter,
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
)
from vacancy_agent.approval_adapters.console import ConsoleApprovalAdapter
from vacancy_agent.approval_adapters.editor import CoverLetterEditor, EditorOpenError
from vacancy_agent.approval_adapters.openclaw import OpenClawApprovalAdapter

__all__ = [
    "ApprovalAdapter",
    "ApprovalRequest",
    "ApprovalResult",
    "ApprovalStatus",
    "ConsoleApprovalAdapter",
    "CoverLetterEditor",
    "EditorOpenError",
    "OpenClawApprovalAdapter",
]
