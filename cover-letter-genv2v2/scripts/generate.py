#!/usr/bin/env python3
"""CLI entrypoint.

Usage:
    python -m scripts.generate \\
        --resume config/resume.yaml \\
        --settings config/settings.yaml \\
        --vacancies data/vacancies.json \\
        [--selected data/selected_vacancy_ids.txt] \\
        [--out letters/] \\
        [--limit 0]

Loads .env if present (LLM_API_KEY etc. take precedence over settings.yaml).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

import yaml

# Make `src.*` importable when running this script directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm_client import LLMClient, LLMConfig
from src.pipeline import CoverLetterPipeline, PipelineConfig
from src.profile_loader import load_profile
from src.vacancy_loader import filter_by_ids, load_selected_ids, load_vacancies


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate cover letters with the 3-pass pipeline.")
    p.add_argument("--resume", required=True, type=Path)
    p.add_argument("--settings", required=True, type=Path)
    p.add_argument("--vacancies", required=True, type=Path)
    p.add_argument("--selected", type=Path, default=None,
                   help="Optional: file with one vacancy id per line.")
    p.add_argument("--out", type=Path, default=Path("letters"))
    p.add_argument("--limit", type=int, default=0, help="Cap on number of vacancies (0 = no cap).")
    p.add_argument("--no-semantic", action="store_true",
                   help="Skip the LLM semantic validator (deterministic only).")
    p.add_argument("--no-deterministic", action="store_true",
                   help="Skip deterministic validation entirely (debug only; allows hallucinations).")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


async def amain() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    _load_dotenv(Path.cwd() / ".env")

    settings_data = yaml.safe_load(args.settings.read_text(encoding="utf-8")) or {}
    llm_cfg = LLMConfig.from_dict(settings_data.get("llm") or {})
    if not llm_cfg.api_key:
        print("ERROR: LLM_API_KEY is not set. Put it in .env or settings.yaml.", file=sys.stderr)
        return 2

    pipeline_cfg_data = settings_data.get("pipeline") or {}
    pipeline_cfg = PipelineConfig(
        min_words=int(pipeline_cfg_data.get("min_words", 100)),
        max_words=int(pipeline_cfg_data.get("max_words", 130)),
        max_writer_retries=int(pipeline_cfg_data.get("max_writer_retries", 2)),
        use_semantic_validator=not args.no_semantic,
        skip_deterministic_validation=bool(args.no_deterministic),
        low_confidence_threshold=float(
            pipeline_cfg_data.get("low_confidence_threshold", 0.5)
        ),
        skip_below_confidence=float(
            pipeline_cfg_data.get("skip_below_confidence", 0.2)
        ),
        stage_timeout=float(
            pipeline_cfg_data.get(
                "stage_timeout",
                # Default: give each stage the LLM timeout plus headroom for retries.
                float((settings_data.get("llm") or {}).get("timeout_seconds", 120)) + 60,
            )
        ),
    )
    max_concurrent = int(pipeline_cfg_data.get("max_concurrent", 5))
    forbidden_claims = settings_data.get("forbidden_claims")

    profile = load_profile(args.resume)
    vacancies = load_vacancies(args.vacancies)

    if args.selected:
        ids = load_selected_ids(args.selected)
        if ids:
            vacancies = filter_by_ids(vacancies, ids)

    if args.limit > 0:
        vacancies = vacancies[: args.limit]

    if not vacancies:
        print("No vacancies to process.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)

    llm = LLMClient(llm_cfg)
    pipeline = CoverLetterPipeline(
        llm,
        profile,
        config=pipeline_cfg,
        forbidden_claims=forbidden_claims,
    )
    try:
        results = await asyncio.wait_for(
            pipeline.generate_batch(vacancies, max_concurrent=max_concurrent),
            timeout=300.0,
        )
    finally:
        await llm.close()

    generated = 0
    failed = 0
    summary = []
    for r in results:
        safe_company = re.sub(r"[^\w\-]+", "_", r.company or "unknown").strip("_") or "unknown"
        if r.letter:
            signature = f"\n\nС уважением,\n{profile.name}" if profile.name else ""
            if r.passed:
                # Validated letter: write the clean, ready-to-send file.
                out_path = args.out / f"{safe_company}_{r.vacancy_id[:8]}.txt"
                # Do not add validation errors to the letter itself
                out_path.write_text(r.letter + signature, encoding="utf-8")
                generated += 1
                print(f"OK   {out_path.name}  ({r.word_count} words, attempts={r.attempts})")
            else:
                # Letter did NOT pass validation. Never let a hard-fail draft be
                # mistaken for a ready letter: write it to a DO_NOT_SEND_* file and
                # prepend a banner listing the reasons. This is the deterministic
                # safety net for hard fails (forbidden-domain claims, domain
                # mismatch, word-count out of range, etc.).
                out_path = args.out / f"DO_NOT_SEND_{safe_company}_{r.vacancy_id[:8]}.txt"
                reasons = "; ".join(r.violations) if r.violations else (r.error or "validation failed")
                banner = (
                    "⚠️ НЕ ОТПРАВЛЯТЬ — письмо не прошло валидацию.\n"
                    f"Причины: {reasons}\n"
                    "Исправьте проблемы или перегенерируйте письмо перед отправкой.\n"
                    "-----8<----- черновик ниже -----8<-----\n\n"
                )
                out_path.write_text(banner + r.letter + signature, encoding="utf-8")
                failed += 1
                print(f"WARN {out_path.name}  ({r.word_count} words, attempts={r.attempts}): validation errors -> flagged DO_NOT_SEND")
        else:
            failed += 1
            print(
                f"FAIL {safe_company} {r.vacancy_id[:8]}: {r.error or 'no_letter'}",
                file=sys.stderr,
            )
        summary.append(r.to_dict())

    (args.out / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nDone: {generated} generated, {failed} failed. Summary at {args.out / '_summary.json'}")
    return 0 if failed == 0 else 3


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
