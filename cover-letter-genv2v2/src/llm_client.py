"""Async LLM client for OpenAI-compatible chat-completions endpoints.

Features:
- Retries with exponential backoff on 5xx / network errors.
- Optional JSON mode (`response_format`) with graceful fallback if the
server rejects the field (some OpenAI-compatible proxies don't support it).
- Reads `reasoning_content` as a fallback when `content` is empty
(some proxies/models return their final answer there).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
  api_key: str
  endpoint: str = "http://localhost:20128/v1/chat/completions"
  model: str = "openrouter/owl-alpha"
  timeout_seconds: float = 120.0
  max_retries: int = 2

  @classmethod
  def from_dict(cls, data: Dict[str, Any]) -> "LLMConfig":
      # Expand ${ENV_VAR} placeholders for api_key.
      api_key = str(data.get("api_key", ""))
      if api_key.startswith("${") and api_key.endswith("}"):
          api_key = os.environ.get(api_key[2:-1], "")
      if not api_key:
          api_key = os.environ.get("LLM_API_KEY", "")
      return cls(
          api_key=api_key,
          endpoint=str(data.get("endpoint", os.environ.get("LLM_ENDPOINT", cls.endpoint))),
          model=str(data.get("model", os.environ.get("LLM_MODEL", cls.model))),
          timeout_seconds=float(data.get("timeout_seconds", cls.timeout_seconds)),
          max_retries=int(data.get("max_retries", cls.max_retries)),
      )


@dataclass
class LLMCallStats:
  attempts: int = 0
  last_error: Optional[str] = None
  json_mode_supported: Optional[bool] = None


class LLMClient:
  """Thin async wrapper over an OpenAI-compatible /v1/chat/completions API."""

  # Module-level memoization across instances to avoid re-discovering
  # JSON-mode support after the first failed attempt.
  _json_mode_cache: Dict[str, bool] = {}

  def __init__(self, config: LLMConfig, http_client: Optional[httpx.AsyncClient] = None):
      self.config = config
      self._client = http_client or httpx.AsyncClient(timeout=config.timeout_seconds)
      self.stats = LLMCallStats()
      # Отключаем json_mode для OmniRoute
      self._json_mode_cache = {config.endpoint: False}

  async def close(self) -> None:
      await self._client.aclose()

  async def generate(
      self,
      system_prompt: str,
      user_prompt: str,
      *,
      temperature: float = 0.4,
      max_tokens: int = 800,
      json_mode: bool = False,
  ) -> str:
      """Single completion call.

      Returns the assistant text (or the contents of `reasoning_content` if
      the proxy put its final answer there).
      """
      messages: List[Dict[str, str]] = []
      if system_prompt:
          messages.append({"role": "system", "content": system_prompt})
      messages.append({"role": "user", "content": user_prompt})

      return await self._post_with_retries(
          messages=messages,
          temperature=temperature,
          max_tokens=max_tokens,
          json_mode=json_mode,
      )

  async def complete_json(
      self,
      *,
      system: str,
      user: str,
      temperature: float = 0.0,
      max_tokens: int = 600,
  ) -> Dict[str, Any]:
      """Single completion call expecting a JSON object response.

      Wraps `generate(json_mode=True)` and parses the result as JSON.
      Returns a dict on success. On parse failure, attempts to extract the
      first JSON object substring (some models wrap JSON in prose or code
      fences). If extraction also fails, returns an empty dict — the
      semantic validator treats this as a soft-pass and won't block the
      letter.

      Kwargs `system`/`user` mirror the caller convention in validator.py;
      internally these map to `generate(system_prompt, user_prompt, ...)`.
      """
      raw_text = await self.generate(
          system_prompt=system,
          user_prompt=user,
          temperature=temperature,
          max_tokens=max_tokens,
          json_mode=True,
      )

      if not raw_text:
          return {}

      # 1) Direct parse.
      try:
          parsed = json.loads(raw_text)
      except json.JSONDecodeError:
          parsed = None

      # 2) Fallback: strip code fences and re-parse.
      if parsed is None:
          stripped = _strip_code_fences(raw_text)
          try:
              parsed = json.loads(stripped)
          except json.JSONDecodeError:
              parsed = None

      # 3) Fallback: extract first {...} substring.
      if parsed is None:
          extracted = _extract_first_json_object(raw_text)
          if extracted is not None:
              try:
                  parsed = json.loads(extracted)
              except json.JSONDecodeError:
                  parsed = None

      if isinstance(parsed, dict):
          return parsed

      logger.warning(
          "complete_json: could not parse response as JSON object; preview=%s",
          raw_text[:200],
      )
      return {}

  async def _post_with_retries(
      self,
      *,
      messages: List[Dict[str, str]],
      temperature: float,
      max_tokens: int,
      json_mode: bool,
  ) -> str:
      endpoint = self.config.endpoint
      json_mode_supported = self._json_mode_cache.get(endpoint, True)
      last_exc: Optional[Exception] = None

      for attempt in range(1, self.config.max_retries + 1):
          self.stats.attempts = attempt
          payload: Dict[str, Any] = {
              "model": self.config.model,
              "messages": messages,
              "temperature": temperature,
              "max_tokens": max_tokens,
              "stream": False,
          }
          if json_mode and json_mode_supported:
              payload["response_format"] = {"type": "json_object"}

          headers = {
              "Authorization": self.config.api_key,
              "Content-Type": "application/json",
          }

          try:
              resp = await self._client.post(endpoint, json=payload, headers=headers, timeout=self.config.timeout_seconds)
          except (httpx.TransportError, httpx.TimeoutException) as exc:
              last_exc = exc
              logger.warning("LLM transport error (attempt %d/%d): %s", attempt, self.config.max_retries, exc)
              await asyncio.sleep(_backoff(attempt))
              continue

          if resp.status_code == 400 and json_mode and json_mode_supported:
              # Some proxies reject `response_format`. Disable and retry once
              # within the same attempt budget.
              logger.info("Endpoint %s rejected json_mode; disabling and retrying.", endpoint)
              json_mode_supported = False
              self._json_mode_cache[endpoint] = False
              continue

          if 500 <= resp.status_code < 600:
              last_exc = httpx.HTTPStatusError(
                  f"server {resp.status_code}", request=resp.request, response=resp
              )
              body_preview = resp.text[:500]
              logger.warning(
                  "LLM 5xx (attempt %d/%d): status=%d body=%s",
                  attempt, self.config.max_retries, resp.status_code, body_preview[:200],
              )

              reset_delay = _extract_reset_delay(body_preview)
              sleep_for = max(_backoff(attempt), reset_delay + 0.75 if reset_delay else 0)

              await asyncio.sleep(sleep_for)
              continue

          if resp.status_code >= 400:
              # 4xx other than 400 — don't retry, surface error.
              resp.raise_for_status()

          self._json_mode_cache.setdefault(endpoint, json_mode_supported)
          self.stats.json_mode_supported = json_mode_supported
          return _extract_content(resp.json())

      # Exhausted retries.
      self.stats.last_error = str(last_exc) if last_exc else "unknown"
      raise RuntimeError(
          f"LLM call failed after {self.config.max_retries} attempts: {self.stats.last_error}"
      )


def _extract_content(data: Dict[str, Any]) -> str:
  choices = data.get("choices") or []
  if not choices:
      return ""
  msg = choices[0].get("message") or {}
  content = msg.get("content")
  if isinstance(content, str) and content.strip():
      return content.strip()
  reasoning = msg.get("reasoning_content")
  if isinstance(reasoning, str) and reasoning.strip():
      return reasoning.strip()
  return ""


def _backoff(attempt: int) -> float:
  """Exponential backoff: 0.5s, 1.0s, 2.0s, capped at 8s."""
  return min(0.5 * (2 ** (attempt - 1)), 8.0)


def _extract_reset_delay(value: str) -> float:
  match = re.search(r"reset after\s+(\d+(?:\.\d+)?)s", value, flags=re.IGNORECASE)
  if not match:
      return 0.0
  try:
      return float(match.group(1))
  except ValueError:
      return 0.0


def _strip_code_fences(text: str) -> str:
  """Strip ```json ... ``` or ``` ... ``` fences if present."""
  stripped = text.strip()
  if stripped.startswith("```"):
      # Drop opening fence line.
      lines = stripped.split("\n")
      if lines:
          lines = lines[1:]
      # Drop closing fence if present.
      if lines and lines[-1].strip().startswith("```"):
          lines = lines[:-1]
      stripped = "\n".join(lines).strip()
  return stripped


def _extract_first_json_object(text: str) -> Optional[str]:
  """Find the first balanced {...} substring in `text`. Returns None if not found."""
  start = text.find("{")
  if start == -1:
      return None
  depth = 0
  in_string = False
  escape = False
  for i in range(start, len(text)):
      ch = text[i]
      if in_string:
          if escape:
              escape = False
          elif ch == "\\":
              escape = True
          elif ch == '"':
              in_string = False
          continue
      if ch == '"':
          in_string = True
          continue
      if ch == "{":
          depth += 1
      elif ch == "}":
          depth -= 1
          if depth == 0:
              return text[start : i + 1]
  return None
