from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml

from vacancy_agent.config import (
    APPLICATIONS_FILE,
    CANDIDATE_PROFILE_FILE,
    SOURCES_FILE,
    VACANCIES_FILE,
)
from vacancy_agent.logger import log
from vacancy_agent.schemas import ApplicationDraft, CandidateProfile, Vacancy, VacancySource, VacancyStatus


class Storage:
    def __init__(
        self,
        vacancies_file: Path = VACANCIES_FILE,
        sources_file: Path = SOURCES_FILE,
        applications_file: Path = APPLICATIONS_FILE,
    ):
        self.vacancies_file = vacancies_file
        self.sources_file = sources_file
        self.applications_file = applications_file

    def _load_entities(self, file: Path, model):
        if not file.exists():
            return []

        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            return [model(**item) for item in data]
        except Exception as exc:
            log.error(f"Failed to load {getattr(model, '__name__', str(model))}: {exc}")
            return []

    def _vacancies_dir(self) -> Path:
        # Store per-source vacancy files under the same data directory.
        return self.vacancies_file.parent / "vacancies_by_source"

    def _vacancies_file_for_source(self, source_name: str) -> Path:
        # Normalise the source name for filesystem safety.
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in (source_name or "unknown"))
        return self._vacancies_dir() / f"{safe}.json"

    def load_vacancies(self) -> list[Vacancy]:
        # Prefer split per-source files when present.
        vac_dir = self._vacancies_dir()
        if vac_dir.exists():
            vacancies: list[Vacancy] = []
            for file in sorted(vac_dir.glob("*.json")):
                vacancies.extend(self._load_entities(file, Vacancy))
            return vacancies

        # Fallback to legacy single-file store.
        return self._load_entities(self.vacancies_file, Vacancy)

    def save_vacancies(self, vacancies: list[Vacancy]) -> None:
        # Save as split per-source files.
        vac_dir = self._vacancies_dir()
        vac_dir.mkdir(parents=True, exist_ok=True)

        by_source: dict[str, list[Vacancy]] = {}
        for v in vacancies:
            key = v.source_name or "unknown"
            by_source.setdefault(key, []).append(v)

        total = 0
        for source_name, items in by_source.items():
            file = self._vacancies_file_for_source(source_name)
            data = [vacancy.model_dump(mode="json") for vacancy in items]
            file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            total += len(items)
            log.info(f"Saved {len(items)} vacancies to {file}")

        # NOTE: we intentionally do not delete the legacy vacancies.json automatically.
        log.info(f"Saved {total} vacancies (split by source) under {vac_dir}")

    def merge_vacancies(self, new_vacancies: list[Vacancy]) -> list[Vacancy]:
        """Merge newly fetched vacancies with the existing store.

        - **New vacancies** are added.
        - **Existing vacancies** are updated with fresh data **but keep the original
          `created_at` timestamp** so the chronological order stays intact.
        - The status from the freshly parsed page (e.g., ``REJECTED``, ``INVITED``,
          ``APPLIED``) now overwrites the old status, allowing the agent to track
          changes like "вам отказали" after a subsequent scan.
        """
        existing = self.load_vacancies()
        by_id = {vacancy.id: vacancy for vacancy in existing}

        added = 0
        updated = 0

        for vacancy in new_vacancies:
            if vacancy.id not in by_id:
                # Completely new vacancy – keep its generated ``created_at``
                by_id[vacancy.id] = vacancy
                added += 1
            else:
                # Existing vacancy – preserve original creation time but refresh
                # all other fields (including the possibly updated ``status``).
                old = by_id[vacancy.id]
                vacancy.created_at = old.created_at
                by_id[vacancy.id] = vacancy
                updated += 1

        merged = list(by_id.values())
        merged.sort(key=lambda item: item.created_at, reverse=True)
        self.save_vacancies(merged)
        log.info(f"Merge vacancies: added={added}, updated={updated}, total={len(merged)}")
        return merged

    def find_vacancy(self, vacancy_id_or_prefix: str) -> Optional[Vacancy]:
        """Find a vacancy by internal UUID, HH numeric ID, or full URL.

        The original implementation only matched the internal UUID (`vacancy.id`).
        After adding duplicate‑skip logic we also match on the numeric HH‑ID
        extracted from the stored URL (e.g. ``https://hh.ru/vacancy/123456``) or
        on the full URL itself. This makes ``find_vacancy('123456')`` correctly
        locate the vacancy.
        """
        vacancies = self.load_vacancies()
        matches = []
        for v in vacancies:
            # Direct match on internal UUID or its prefix
            if v.id == vacancy_id_or_prefix or v.id.startswith(vacancy_id_or_prefix):
                matches.append(v)
                continue
            # Match on numeric HH ID extracted from stored URL
            try:
                stored_id = v.url.split('/')[-1].split('?')[0]
                # If the query looks like a numeric HH ID, match exactly to avoid prefix collisions.
                if vacancy_id_or_prefix.isdigit():
                    if stored_id == vacancy_id_or_prefix:
                        matches.append(v)
                        continue
                else:
                    if stored_id == vacancy_id_or_prefix or stored_id.startswith(vacancy_id_or_prefix):
                        matches.append(v)
                        continue
            except Exception:
                pass
            # Match on full URL (in case the caller passes it)
            if v.url == vacancy_id_or_prefix:
                matches.append(v)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            log.warning(f"Ambiguous vacancy identifier '{vacancy_id_or_prefix}' – returning first match")
            return matches[0]
        return None

    def update_vacancy_status(self, vacancy_id_or_prefix: str, status: VacancyStatus) -> None:
        vacancies = self.load_vacancies()
        changed = False

        for vacancy in vacancies:
            if vacancy.id == vacancy_id_or_prefix or vacancy.id.startswith(vacancy_id_or_prefix):
                vacancy.status = status
                changed = True
                break

        if not changed:
            raise ValueError(f"Vacancy not found: {vacancy_id_or_prefix}")

        self.save_vacancies(vacancies)


    def set_vacancy_applied_by_us(self, vacancy_id_or_prefix: str, applied: bool = True) -> None:
        vacancies = self.load_vacancies()
        changed = False

        for vacancy in vacancies:
            if vacancy.id == vacancy_id_or_prefix or vacancy.id.startswith(vacancy_id_or_prefix):
                vacancy.applied_by_us = applied
                changed = True
                break

        if not changed:
            raise ValueError(f"Vacancy not found: {vacancy_id_or_prefix}")

        self.save_vacancies(vacancies)

    def load_sources(self) -> list[VacancySource]:
        return self._load_entities(self.sources_file, VacancySource)

    def save_sources(self, sources: list[VacancySource]) -> None:
        self.sources_file.parent.mkdir(parents=True, exist_ok=True)
        data = [source.model_dump(mode="json") for source in sources]
        self.sources_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(f"Saved {len(sources)} sources to {self.sources_file}")

    def find_source(self, name_or_id: str) -> Optional[VacancySource]:
        sources = self.load_sources()
        for source in sources:
            if source.id == name_or_id or source.name == name_or_id:
                return source
        return None

    def load_candidate_profile(self) -> Optional[CandidateProfile]:
        if not CANDIDATE_PROFILE_FILE.exists():
            return None

        try:
            data = yaml.safe_load(CANDIDATE_PROFILE_FILE.read_text(encoding="utf-8"))
            return CandidateProfile(**data)
        except Exception as exc:
            log.error(f"Failed to load candidate profile: {exc}")
            return None

    def save_candidate_profile(self, profile: CandidateProfile) -> None:
        CANDIDATE_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CANDIDATE_PROFILE_FILE.write_text(
            yaml.dump(profile.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def load_applications(self) -> list[ApplicationDraft]:
        return self._load_entities(self.applications_file, ApplicationDraft)

    def save_applications(self, applications: list[ApplicationDraft]) -> None:
        self.applications_file.parent.mkdir(parents=True, exist_ok=True)
        data = [item.model_dump(mode="json") for item in applications]
        self.applications_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def upsert_application(self, draft: ApplicationDraft) -> None:
        applications = self.load_applications()
        by_id = {app.id: app for app in applications}
        by_id[draft.id] = draft
        self.save_applications(list(by_id.values()))

    def get_stats(self) -> dict[str, object]:
        vacancies = self.load_vacancies()
        applications = self.load_applications()

        by_status: dict[str, int] = {}
        by_source: dict[str, int] = {}

        for vacancy in vacancies:
            by_status[vacancy.status.value] = by_status.get(vacancy.status.value, 0) + 1
            by_source[vacancy.source_name] = by_source.get(vacancy.source_name, 0) + 1

        return {
            "vacancies_total": len(vacancies),
            "applications_total": len(applications),
            "by_status": by_status,
            "by_source": by_source,
        }


storage = Storage()
