from __future__ import annotations

import csv
import json
from pathlib import Path

from vacancy_agent.config import DATA_DIR
from vacancy_agent.logger import log
from vacancy_agent.schemas import Vacancy


def _ensure_safe_output_path(filepath: Path) -> Path:
    # Prevent path traversal / writing outside the project data dir.
    filepath = filepath.expanduser()
    if not filepath.is_absolute():
        filepath = (DATA_DIR / filepath).resolve()

    data_dir = DATA_DIR.resolve()
    if data_dir != filepath and data_dir not in filepath.parents:
        raise ValueError(f"Output must be inside data directory: {DATA_DIR}")

    filepath.parent.mkdir(parents=True, exist_ok=True)
    return filepath


def export_to_json(vacancies: list[Vacancy], filepath: Path) -> None:
    filepath = _ensure_safe_output_path(filepath)
    data = [vacancy.model_dump(mode="json") for vacancy in vacancies]
    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Exported {len(vacancies)} vacancies to {filepath}")


def export_to_csv(vacancies: list[Vacancy], filepath: Path) -> None:
    filepath = _ensure_safe_output_path(filepath)
    if not vacancies:
        filepath.write_text("", encoding="utf-8")
        return

    fieldnames = [
        "id",
        "source_name",
        "title",
        "company",
        "salary",
        "location",
        "work_format",
        "experience",
        "employment_type",
        "url",
        "status",
        "created_at",
    ]

    with filepath.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for vacancy in vacancies:
            row = vacancy.model_dump(mode="json")
            row["work_format"] = vacancy.work_format.value
            row["status"] = vacancy.status.value
            writer.writerow({key: row.get(key) for key in fieldnames})

    log.info(f"Exported {len(vacancies)} vacancies to {filepath}")


def export_to_xlsx(vacancies: list[Vacancy], filepath: Path) -> None:
    filepath = _ensure_safe_output_path(filepath)
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Vacancies"

    headers = [
        "ID",
        "Источник",
        "Название",
        "Компания",
        "Зарплата",
        "Локация",
        "Формат",
        "Опыт",
        "URL",
        "Статус",
        "Дата добавления",
    ]
    ws.append(headers)

    for vacancy in vacancies:
        ws.append(
            [
                vacancy.id,
                vacancy.source_name,
                vacancy.title,
                vacancy.company,
                vacancy.salary,
                vacancy.location,
                vacancy.work_format.value,
                vacancy.experience,
                vacancy.url,
                vacancy.status.value,
                vacancy.created_at.isoformat(),
            ]
        )

    wb.save(filepath)
    log.info(f"Exported {len(vacancies)} vacancies to {filepath}")
