"""
Persistent failure logging for the cover letter pipeline.

Writes every failed generation to logs/failed_letters.jsonl (one JSON object
per line). This is separate from Python's logging module — it's a structured,
machine-readable audit trail you can grep/jq/replay against.

Each record contains:
- timestamp (ISO 8601, UTC)
- vacancy_id, company, title
- error (the short error tag from GenerationResult.error)
- violations (deterministic/semantic validator violations, if any)
- word_count, attempts, universal_mode, confidence
- last_letter (the last draft we tried, truncated to 4000 chars)
- selected_project, selected_numbers, selected_tech

The file is opened in append mode, so concurrent writes from
generate_batch() are safe enough for single-process use. For
multi-process safety, switch to a queue or a real logging handler.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = Path("logs")
_DEFAULT_LOG_FILE = "failed_letters.jsonl"
_MAX_LETTER_CHARS = 4000


def _resolve_log_path() -> Path:
  """Resolve the log file path. Honors FAIL_LOG_PATH env var if set."""
  env_path = os.environ.get("FAIL_LOG_PATH")
  if env_path:
      return Path(env_path)
  return _DEFAULT_LOG_DIR / _DEFAULT_LOG_FILE


def log_failure(result: Any) -> None:
  """
  Append a failure record for a GenerationResult.

  Safe to call on any result — if result.passed is True, it's a no-op.
  Never raises: a logging failure must not break the pipeline.
  """
  try:
      if getattr(result, "passed", False):
          return

      letter = getattr(result, "letter", None) or ""
      if len(letter) > _MAX_LETTER_CHARS:
          letter = letter[:_MAX_LETTER_CHARS] + "...[truncated]"

      record: Dict[str, Any] = {
          "timestamp": datetime.now(timezone.utc).isoformat(),
          "vacancy_id": getattr(result, "vacancy_id", None),
          "company": getattr(result, "company", None),
          "title": getattr(result, "title", None),
          "error": getattr(result, "error", None),
          "violations": list(getattr(result, "violations", []) or []),
          "word_count": getattr(result, "word_count", 0),
          "attempts": getattr(result, "attempts", 0),
          "universal_mode": getattr(result, "universal_mode", False),
          "semantic_validator_used": getattr(
              result, "semantic_validator_used", False
          ),
          "confidence": getattr(result, "confidence", 0.0),
          "confidence_reason": getattr(result, "confidence_reason", ""),
          "selected_project": getattr(result, "selected_project", None),
          "used_numbers": list(getattr(result, "used_numbers", []) or []),
          "used_tech": list(getattr(result, "used_tech", []) or []),
          "last_letter": letter,
      }

      path = _resolve_log_path()
      path.parent.mkdir(parents=True, exist_ok=True)
      with path.open("a", encoding="utf-8") as fh:
          fh.write(json.dumps(record, ensure_ascii=False) + "\n")
  except Exception as exc:  # noqa: BLE001
      # Never crash the pipeline because of a logging failure.
      logger.warning("fail_logger could not write failure record: %s", exc)


def log_error_result(
  vacancy_id: str,
  company: str,
  title: str,
  *,
  error: str,
  attempts: int = 0,
  confidence: float = 0.0,
  extra: Optional[Dict[str, Any]] = None,
) -> None:
  """
  Lightweight alternative for callers that don't have a full GenerationResult
  handy (e.g. exception at analyzer stage before result construction).
  """
  try:
      record: Dict[str, Any] = {
          "timestamp": datetime.now(timezone.utc).isoformat(),
          "vacancy_id": vacancy_id,
          "company": company,
          "title": title,
          "error": error,
          "attempts": attempts,
          "confidence": confidence,
          "passed": False,
      }
      if extra:
          record.update(extra)
      path = _resolve_log_path()
      path.parent.mkdir(parents=True, exist_ok=True)
      with path.open("a", encoding="utf-8") as fh:
          fh.write(json.dumps(record, ensure_ascii=False) + "\n")
  except Exception as exc:  # noqa: BLE001
      logger.warning("fail_logger could not write error record: %s", exc)
