"""Writer module: generates cover letter from CanonicalFacts.

NOTE: This file is intentionally written with TAB indentation.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .canonical_facts import CanonicalFacts, build_canonical_facts_brief
from .openers import select_openers
from .prompts import select_writer_system
from .llm import LLMClient


async def _final_letter_from_facts(
	llm: LLMClient,
	*,
	analyzer_json: dict,
	canonical_facts_brief: str,
	opener_pool: list[str],
	universal_mode: bool,
	feedback: Optional[str],
	max_tokens: int,
	vacancy_title: Optional[str],
	vacancy_company: Optional[str],
	vacancy_description: Optional[str],
	vacancy_requirements: list[str],
) -> str:
	"""Call the LLM to produce the final letter text from canonical facts."""
	# (Body intentionally omitted in this patch context — preserved as-is in repo.)
	raise NotImplementedError


def _strip_signature_lines(text: str) -> str:
	raise NotImplementedError


def _enforce_paragraph_split(text: str, *, universal_mode: bool) -> str:
	raise NotImplementedError


def _build_greeting(company: Optional[str]) -> str:
	raise NotImplementedError


def _inject_greeting(text: str, greeting: str) -> str:
	raise NotImplementedError


async def write_letter(
	llm: LLMClient,
	*,
	facts: CanonicalFacts,
	analyzer_json: dict,
	universal_mode: bool = False,
	feedback: Optional[str] = None,
	max_tokens: int = 800,
	vacancy_title: Optional[str] = None,
	vacancy_company: Optional[str] = None,
	vacancy_description: Optional[str] = None,
	vacancy_requirements: Optional[list[str]] = None,
	used_starts: Optional[list[str]] = None,
) -> str:
	system_prompt = select_writer_system(universal_mode=universal_mode)
	selected_project = str(analyzer_json.get("selected_project") or "")
	brief = build_canonical_facts_brief(facts, selected_project)
	# v2: opener pool now consumes CanonicalFacts + selected_project and
	# returns achievement-based hooks instead of "{years}+ years" templates.
	opener_pool = select_openers(
		facts,
		selected_project,
		used_starts or [],
		n=2,
	)

	# Generate the final letter FROM FACTS ONLY.
	final_text = await _final_letter_from_facts(
		llm,
		analyzer_json=analyzer_json,
		canonical_facts_brief=brief,
		opener_pool=opener_pool,
		universal_mode=universal_mode,
		feedback=feedback,
		max_tokens=max_tokens,
		vacancy_title=vacancy_title,
		vacancy_company=vacancy_company,
		vacancy_description=vacancy_description,
		vacancy_requirements=vacancy_requirements or [],
	)

	# Empty-result guard. Previously an empty LLM response flowed silently
	# through postprocess to a 0-word letter, which surfaced in pipeline.py
	# as an empty `writer:` error string (no signal about the cause).
	# Now we raise a descriptive error so the pipeline's f"writer: {exc}"
	# log identifies the failure mode (e.g. Ustoz AI adapter returning empty).
	if not final_text or not final_text.strip():
		raise RuntimeError(
			"LLM returned empty text (model="
			+ str(getattr(llm.config, "model", "?"))
			+ ", endpoint="
			+ str(getattr(llm.config, "endpoint", "?"))
			+ ", vacancy_title="
			+ (vacancy_title or "?")
			+ ", universal_mode="
			+ str(universal_mode)
			+ ")"
		)

	stripped = _strip_signature_lines(final_text)
	split = _enforce_paragraph_split(stripped, universal_mode=universal_mode)

	# Python-level guarantee: greeting is always the first line.
	greeting = _build_greeting(vacancy_company)
	return _inject_greeting(split, greeting)
