from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse
import re
from datetime import datetime, timezone, timedelta

from bs4 import BeautifulSoup

from vacancy_agent.config import settings
from vacancy_agent.schemas import Vacancy, WorkFormat
from vacancy_agent.utils.ids import make_id
from vacancy_agent.utils.text import clean_multiline, normalize_space


_DASHES_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")


def _normalize_dashes(text: str) -> str:
    return _DASHES_RE.sub("-", text)


def _is_hh_domain(netloc: str) -> bool:
    host = (netloc or "").lower()
    return host.endswith("hh.ru") or host.endswith("hh.kz")


def _is_hirify_domain(netloc: str) -> bool:
    host = (netloc or "").lower()
    return host.endswith("hirify.me")


class VacancyExtractor:
    def __init__(
        self,
        html: str,
        url: str,
        source_id: str,
        source_name: str,
        selectors: dict[str, str] | None = None,
    ):
        self.soup = BeautifulSoup(html, "html.parser")
        self.url = url
        self.source_id = source_id
        self.source_name = source_name
        self.selectors = selectors or {}

    def extract_vacancy(self) -> Vacancy:
        title = self._extract_text("title", ["h1", "[data-qa='vacancy-title']", ".vacancy-title", ".job-title"])
        company = self._extract_text(
            "company",
            [
                "[data-qa='vacancy-company-name']",
                "[data-qa='bloko-header-2']",
                ".company-name",
                ".employer",
                ".organization",
            ],
        )
        salary = self._extract_text("salary", ["[data-qa='vacancy-salary']", ".salary", ".vacancy-salary"])
        location = self._extract_text(
            "location",
            ["[data-qa='vacancy-view-location']", "[data-qa='vacancy-location']", ".location", ".vacancy-location"],
        )
        description = self._extract_description()

        requirements, responsibilities, benefits = self._split_description(description or "")

        # Hirify-specific extras (tags, salary/location/meta)
        hirify_tags: list[str] = []
        hirify_country: Optional[str] = None
        hirify_location: Optional[str] = None
        hirify_salary: Optional[str] = None
        hirify_employment_type: Optional[str] = None
        hirify_experience: Optional[str] = None
        hirify_work_format: Optional[WorkFormat] = None
        hirify_work_format_raw: Optional[str] = None
        hirify_employment_type_raw: Optional[str] = None
        hirify_experience_raw: Optional[str] = None
        hirify_published_at: Optional[datetime] = None
        hirify_english_level: Optional[str] = None

        # Extract additional structured fields for HH.ru pages.
        employment_type = None
        experience = None
        work_schedule = None
        working_hours = None
        hh_work_format = None
        hh_work_format_raw = None
        try:
            parsed_url = urlparse(self.url)
            if _is_hh_domain(parsed_url.netloc):
                employment_type = self._extract_hh_employment_type()
                experience = self._extract_hh_experience()
                work_schedule = self._extract_hh_work_schedule()
                working_hours = self._extract_hh_working_hours()
                hh_work_format_raw = self._extract_hh_work_format_raw()
                hh_work_format = self._normalize_hh_work_format(hh_work_format_raw)
        except Exception:
            pass

        vacancy_id = make_id(self.url, length=16)

        parsed_url = urlparse(self.url)
        seen_on_site = False
        try:
            if _is_hirify_domain(parsed_url.netloc):
                seen_on_site = self._extract_hirify_seen_on_site()
                hirify_tags = self._extract_hirify_tags()
                hirify_country = self._extract_hirify_country()
                hirify_location = self._extract_hirify_location()
                hirify_salary = self._extract_hirify_salary()
                hirify_employment_type = self._extract_hirify_employment_type()
                hirify_experience = self._extract_hirify_experience()
                hirify_work_format = self._extract_hirify_work_format()
                hirify_work_format_raw = self._extract_hirify_work_format_raw()
                hirify_employment_type_raw = self._extract_hirify_employment_type_raw()
                hirify_experience_raw = self._extract_hirify_experience_raw()
                hirify_published_at = self._extract_hirify_published_at()
                hirify_english_level = self._extract_hirify_english_level()
        except Exception:
            seen_on_site = False

        return Vacancy(
            id=vacancy_id,
            source_id=self.source_id,
            source_name=self.source_name,
            title=title or "Не указано",
            company=company or "Не указано",
            # For hirify, avoid inheriting `salary` from generic extraction (it can pick up values
            # from other vacancy cards rendered below). Only use the hirify-specific salary.
            salary=hirify_salary if _is_hirify_domain(urlparse(self.url).netloc) else salary,
            # For hirify, avoid inheriting `location` and `country` from generic extraction (can be polluted by other cards).
            country=hirify_country if _is_hirify_domain(urlparse(self.url).netloc) else None,
            location=(hirify_location if _is_hirify_domain(urlparse(self.url).netloc) else (hirify_location or location)),
            work_format=(
                hirify_work_format
                or hh_work_format
                or self._detect_work_format(" ".join([description or "", hirify_location or location or ""]))
            ),
            experience=hirify_experience or experience,
            employment_type=hirify_employment_type or employment_type,
            work_format_raw=hirify_work_format_raw or hh_work_format_raw,
            employment_type_raw=hirify_employment_type_raw,
            experience_raw=hirify_experience_raw,
            work_schedule=work_schedule,
            working_hours=working_hours,
            english_level=hirify_english_level,
            description=description,
            requirements=requirements,
            responsibilities=responsibilities,
            benefits=benefits,
            tags=hirify_tags,
            url=self.url,
            published_at=(
                self._extract_hh_published_at()
                if _is_hh_domain(urlparse(self.url).netloc)
                else hirify_published_at
            ),
            seen_on_site=seen_on_site,
            raw_html=str(self.soup) if settings.store_raw_html else None,
        )

    def _extract_hirify_seen_on_site(self) -> bool:
        """Return True if hirify marks this vacancy as seen ("просмотрено")."""
        el = self.soup.select_one("div.seen-badge")
        if not el:
            return False
        txt = normalize_space(el.get_text(" ", strip=True)).lower()
        return txt == "просмотрено"

    def _extract_hirify_tags(self) -> list[str]:
        # Prefer vacancy page tags: <div class="vacancy-detail-tags"> <button class="tag tag-clickable">...</button>
        tags: list[str] = []
        for el in self.soup.select("div.vacancy-detail-tags button.tag.tag-clickable"):
            t = normalize_space(el.get_text(" ", strip=True))
            if t:
                tags.append(t)

        # Fallback for cards/other layouts: many <div class="tag">...</div>
        if not tags:
            for el in self.soup.select("div.tag"):
                t = normalize_space(el.get_text(" ", strip=True))
                if not t:
                    continue
                # Skip the "+N skills" aggregator.
                if "skills" in t.lower() and "+" in t:
                    continue
                tags.append(t)
        # De-dupe while preserving order
        out = []
        seen = set()
        for t in tags:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out[:50]

    def _extract_hirify_common_details(self) -> dict[str, str]:
        """Parse Hirify label/value details into a dict.

        Example structure:
          <div class="vacancy-common-tags">
            <div class="common-detail-item">
              <div class="label">Формат работы</div>
              <div class="value">remote (Global)</div>
        """
        details: dict[str, str] = {}
        for item in self.soup.select("div.vacancy-common-tags div.common-detail-item"):
            label_el = item.select_one("div.label")
            value_el = item.select_one("div.value")
            if not label_el or not value_el:
                continue
            label = normalize_space(label_el.get_text(" ", strip=True))
            value = normalize_space(value_el.get_text(" ", strip=True))
            if label and value:
                details[label] = value
        return details

    def _extract_hirify_country(self) -> Optional[str]:
        details = self._extract_hirify_common_details()
        value = details.get("Страна")
        if value:
            return value.strip()
        return None

    def _extract_hirify_location(self) -> Optional[str]:
        details = self._extract_hirify_common_details()

        # Если на Hirify появится отдельное поле города/локации, оно попадёт сюда.
        for key in ["Локация", "Город", "Местоположение"]:
            value = details.get(key)
            if value:
                return value.strip()

        # Пока у Hirify часто есть только "Страна".
        # Чтобы колонка location не была пустой, можно использовать страну как fallback.
        country = details.get("Страна")
        if country:
            return country.strip()

        return None

    def _extract_hirify_english_level(self) -> Optional[str]:
        details = self._extract_hirify_common_details()
        val = details.get("Английский")
        if val:
            return val.lower()
        return None

    def _extract_hirify_employment_type(self) -> Optional[str]:
        details = self._extract_hirify_common_details()
        val = details.get("Тип работы")
        if val:
            return val.lower()

        # IMPORTANT: Do not infer from generic tag clouds outside the vacancy root.
        # Hirify pages may include a "similar vacancies" section below with its own tags
        # (e.g. "fulltime"), which must NOT leak into the current vacancy.
        return None

    def _extract_hirify_experience(self) -> Optional[str]:
        details = self._extract_hirify_common_details()
        val = details.get("Грейд")
        if val:
            return val.lower()

        # IMPORTANT: Do not infer grade from generic tag clouds outside the vacancy root.
        # Hirify pages may include a "similar vacancies" section below with its own tags
        # (e.g. "senior"), which must NOT leak into the current vacancy.
        # If Hirify doesn't provide an explicit "Грейд" in common details, we keep it None.
        # (If we ever re-enable inference, it must be scoped strictly to the vacancy header/root.)
        return None

    def _extract_hirify_work_format(self) -> Optional[WorkFormat]:
        details = self._extract_hirify_common_details()
        val = (details.get("Формат работы") or "").lower()
        if val:
            if "remote" in val:
                return WorkFormat.REMOTE
            if "hybrid" in val:
                return WorkFormat.HYBRID
            if "onsite" in val or "office" in val:
                return WorkFormat.OFFICE

        # IMPORTANT: Do not infer from generic tag clouds outside the vacancy root.
        # Hirify pages may include a "similar vacancies" section below with its own tags
        # (e.g. "remote", "onsite"), which must NOT leak into the current vacancy.
        return None

    def _extract_hirify_work_format_raw(self) -> Optional[str]:
        details = self._extract_hirify_common_details()
        val = details.get("Формат работы")
        return val.strip() if val else None

    def _extract_hirify_employment_type_raw(self) -> Optional[str]:
        details = self._extract_hirify_common_details()
        val = details.get("Тип работы")
        return val.strip() if val else None

    def _extract_hirify_experience_raw(self) -> Optional[str]:
        details = self._extract_hirify_common_details()
        val = details.get("Грейд")
        return val.strip() if val else None

    def _extract_hirify_salary(self) -> Optional[str]:
        # Prefer vacancy page header salary:
        #   <div class="font-bold text-[28px]">500<span class="text-tertiary">$</span></div>
        el = self.soup.select_one("div.font-bold.text-\[28px\]")
        if el:
            txt = normalize_space(el.get_text("", strip=True))
            if txt:
                return txt

        # No salary shown in vacancy header.
        # Do NOT fall back to scanning other salary blocks on hirify vacancy pages
        # because the page may include other vacancies below.
        return None

    def _extract_hirify_published_at(self) -> Optional[datetime]:
        # Hirify vacancy page shows freshness near the vacancy header, e.g.:
        #   "обновлено 4 часа назад" OR just "1 день назад".
        # We must NOT scan the whole page because it often includes other vacancies at the bottom.
        # Instead, we search only inside the vacancy header block.

        header = self.soup.select_one("div.vacancy-header")
        if not header:
            return None

        text = ""
        for el in header.select("div.text-tertiary"):
            t = normalize_space(el.get_text(" ", strip=True)).replace("\xa0", " ")
            if "назад" in t.lower():
                text = t
                break

        if not text:
            return None

        # Pattern: "2 дня назад" / "4 часа назад" / "10 минут назад"
        m = re.search(
            r"(\d{1,3})\s*(секунд[а-я]*|минут[а-я]*|час[а-я]*|день|дня|дней|недел[яиь])\s+назад",
            text,
            re.IGNORECASE,
        )
        if m:
            try:
                n = int(m.group(1))
                unit = m.group(2).lower()
                now = datetime.now(timezone.utc)

                if unit.startswith("сек"):
                    target = now - timedelta(seconds=n)
                elif unit.startswith("мин"):
                    target = now - timedelta(minutes=n)
                elif unit.startswith("час"):
                    target = now - timedelta(hours=n)
                elif unit.startswith("нед"):
                    target = now - timedelta(days=7 * n)
                else:
                    # days
                    target = now - timedelta(days=n)

                d = target.date()
                return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            except Exception:
                pass
        # Pattern: "4 май" (no year)
        m = re.search(r"\b(\d{1,2})\s+([а-яё]{3,})\b", text, re.IGNORECASE)
        if m:
            day = int(m.group(1))
            mon = m.group(2).lower()
            months = {
                "янв": 1,
                "фев": 2,
                "мар": 3,
                "апр": 4,
                "май": 5,
                "июн": 6,
                "июл": 7,
                "авг": 8,
                "сен": 9,
                "oct": 10,
                "ноя": 11,
                "дек": 12,
            }
            for k, v in months.items():
                if mon.startswith(k):
                    year = datetime.now(timezone.utc).year
                    try:
                        return datetime(year, v, day, tzinfo=timezone.utc)
                    except Exception:
                        break
        return None

    def _extract_text(self, field_name: str, fallback_selectors: list[str]) -> Optional[str]:
        selectors = []
        if field_name in self.selectors:
            selectors.append(self.selectors[field_name])
        selectors.extend(fallback_selectors)

        for selector in selectors:
            element = self.soup.select_one(selector)
            if not element:
                continue
            text = normalize_space(element.get_text(" ", strip=True))
            if text:
                return text
        return None

    def _extract_description(self) -> Optional[str]:
        selectors = []
        if "description" in self.selectors:
            selectors.append(self.selectors["description"])

        selectors.extend(
            [
                "[data-qa='vacancy-description']",
                ".vacancy-description",
                ".job-description",
                ".description",
                "main",
                "body",
            ]
        )

        for selector in selectors:
            element = self.soup.select_one(selector)
            if not element:
                continue

            for script in element(["script", "style", "noscript"]):
                script.decompose()

            text = clean_multiline(element.get_text("\n", strip=True))
            if len(text) > 50:
                return text

        return None

    def _split_description(self, description: str) -> tuple[list[str], list[str], list[str]]:
        requirements: list[str] = []
        responsibilities: list[str] = []
        benefits: list[str] = []

        current: Optional[str] = None
        section_map = {
            "требования": "requirements",
            "requirements": "requirements",
            "что нужно": "requirements",
            "обязанности": "responsibilities",
            "responsibilities": "responsibilities",
            "задачи": "responsibilities",
            "условия": "benefits",
            "benefits": "benefits",
            "мы предлагаем": "benefits",
        }

        for raw_line in description.splitlines():
            line = normalize_space(raw_line)
            if not line:
                continue

            lower = line.lower().strip(":")
            matched_section = None
            for keyword, section in section_map.items():
                if keyword in lower and len(lower) <= 80:
                    matched_section = section
                    break

            if matched_section:
                current = matched_section
                continue

            if current == "requirements":
                requirements.append(line)
            elif current == "responsibilities":
                responsibilities.append(line)
            elif current == "benefits":
                benefits.append(line)

        return requirements[:20], responsibilities[:20], benefits[:20]

    def _extract_hh_work_format_raw(self) -> Optional[str]:
        """Extract exact HH work format text.

        Examples:
        "Формат работы: удалённо"
        "Формат работы: на месте работодателя"
        "Формат работы: на месте работодателя, удалённо или гибрид"
        """
        el = self.soup.select_one("[data-qa='work-formats-text']")
        if not el:
            return None

        text = normalize_space(el.get_text(" ", strip=True))
        if not text:
            return None

        text = text.replace("\xa0", " ")
        text = normalize_space(text)

        prefix = "Формат работы:"
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()

        return text or None

    def _normalize_hh_work_format(self, raw: Optional[str]) -> Optional[WorkFormat]:
        if not raw:
            return None

        text = raw.lower().replace("\xa0", " ")
        text = normalize_space(text)

        has_remote = "удал" in text or "remote" in text
        has_hybrid = "гибрид" in text or "hybrid" in text
        has_office = (
            "на месте работодателя" in text
            or "офис" in text
            or "office" in text
            or "onsite" in text
        )

        # Если HH пишет несколько вариантов сразу:
        # "на месте работодателя, удалённо или гибрид"
        # это не чистый remote и не чистый office.
        if has_hybrid or (has_remote and has_office):
            return WorkFormat.HYBRID

        if has_remote:
            return WorkFormat.REMOTE

        if has_office:
            return WorkFormat.OFFICE

        return WorkFormat.UNKNOWN

    def _extract_hh_employment_type(self) -> Optional[str]:
        # Prefer stable data-qa selectors when present.
        for sel in ["[data-qa='common-employment-text']"]:
            el = self.soup.select_one(sel)
            if el:
                val = normalize_space(el.get_text(" ", strip=True))
                if val:
                    return val

        # Fallback: search in visible text.
        text = _normalize_dashes(self.soup.get_text("\n", strip=True))
        for needle in ["Полная занятость", "Частичная занятость", "Проектная работа", "Стажировка", "Волонтерство"]:
            if needle in text:
                return needle
        return None

    def _extract_hh_experience(self) -> Optional[str]:
        # Prefer stable data-qa selectors when present.
        for sel in ["[data-qa='vacancy-experience']", "[data-qa='work-experience-text']"]:
            el = self.soup.select_one(sel)
            if el:
                val = normalize_space(el.get_text(" ", strip=True))
                if val:
                    # Normalize dash variants inside the value.
                    return _normalize_dashes(val)

        # Fallback: search in visible text.
        text = _normalize_dashes(self.soup.get_text("\n", strip=True))
        for needle in ["Без опыта", "Опыт 1-3 года", "Опыт 3-6 лет", "Опыт более 6 лет"]:
            if needle in text:
                return needle
        m = re.search(r"Опыт\s*(\d+\s*-\s*\d+\s*(?:года|лет)|более\s*\d+\s*(?:года|лет))", text, re.IGNORECASE)
        if m:
            return "Опыт " + normalize_space(m.group(1))
        return None

    def _extract_hh_work_schedule(self) -> Optional[str]:
        # Often shows like "График 6/1". Prefer data-qa when present.
        for sel in ["[data-qa='work-schedule-by-days-text']"]:
            el = self.soup.select_one(sel)
            if el:
                val = normalize_space(el.get_text(" ", strip=True))
                if val:
                    return _normalize_dashes(val)

        text = _normalize_dashes(self.soup.get_text("\n", strip=True))
        m = re.search(r"График\s*([0-9]+\s*/\s*[0-9]+)", text)
        if m:
            return f"График {normalize_space(m.group(1))}"
        return None

    def _extract_hh_working_hours(self) -> Optional[str]:
        # Often shows like "Рабочие часы 8". Prefer data-qa when present.
        for sel in ["[data-qa='working-hours-text']"]:
            el = self.soup.select_one(sel)
            if el:
                val = normalize_space(el.get_text(" ", strip=True))
                if val:
                    return _normalize_dashes(val)

        text = _normalize_dashes(self.soup.get_text("\n", strip=True))
        m = re.search(r"Рабочие\s+часы\s*(\d+)", text, re.IGNORECASE)
        if m:
            return f"Рабочие часы {normalize_space(m.group(1))}"
        return None

    def _extract_hh_work_format_raw(self) -> Optional[str]:
        # HH often shows work format in a dedicated block, e.g.:
        # <p class="vacancy-description-list-item">Удалённая работа</p>
        # or via data-qa="vacancy-view-employment-mode"
        for sel in ["[data-qa='vacancy-view-employment-mode']"]:
            el = self.soup.select_one(sel)
            if el:
                val = normalize_space(el.get_text(" ", strip=True))
                if val:
                    return val

        # Fallback: search in the description list items
        for el in self.soup.select("p.vacancy-description-list-item"):
            val = normalize_space(el.get_text(" ", strip=True))
            if val and any(word in val.lower() for word in ["удал", "офис", "гибрид"]):
                return val

        return None

    def _normalize_hh_work_format(self, raw: Optional[str]) -> Optional[WorkFormat]:
        if not raw:
            return None

        text = raw.lower()
        if any(word in text for word in ["удал", "remote"]):
            return WorkFormat.REMOTE
        if any(word in text for word in ["гибрид", "hybrid"]):
            return WorkFormat.HYBRID
        if any(word in text for word in ["офис", "office", "на территории"]):
            return WorkFormat.OFFICE
        return None

    def _extract_hh_published_at(self) -> Optional[datetime]:
        # Search for the phrase "Опубликована" (or "Вакансия опубликована") and extract the following date.
        # HH can display the date in two main formats:
        #   1) "8 апреля 2026" – Russian month name (genitive or prepositional).
        #   2) "20.04.2026"      – DD.MM.YYYY numeric format.
        # We'll try both patterns and return a UTC datetime if parsing succeeds.
        text = self.soup.get_text("\n", strip=True)
        # Normalise non‑breaking spaces to regular spaces for easier regex matching.
        text = text.replace('\xa0', ' ')
        # -----------------------------------------------------------------
        # 1️⃣ Russian month name pattern
        # -----------------------------------------------------------------
        m = re.search(r"Опубликована\s*[:‑-]?\s*([0-9]{1,2}\s+[а-яё]+\s+[0-9]{4})", text, re.IGNORECASE)
        if not m:
            # some pages prepend "Вакансия" before the word
            m = re.search(r"Вакансия\s+опубликована\s*[:‑-]?\s*([0-9]{1,2}\s+[а-яё]+\s+[0-9]{4})", text, re.IGNORECASE)
        if m:
            date_str = m.group(1).strip()
            months = {
                'января': '01', 'февраля': '02', 'марта': '03', 'апреля': '04', 'мая': '05', 'июня': '06',
                'июля': '07', 'августа': '08', 'сентября': '09', 'октября': '10', 'ноября': '11', 'декабря': '12',
                'январе': '01', 'феврале': '02', 'марте': '03', 'апреле': '04', 'мае': '05', 'июне': '06',
                'июле': '07', 'августе': '08', 'сентябре': '09', 'октябре': '10', 'ноябре': '11', 'декабре': '12',
            }
            parts = date_str.split()
            if len(parts) == 3:
                day, month_name, year = parts
                month = months.get(month_name.lower())
                if month:
                    try:
                        return datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
                    except Exception:
                        pass
        # -----------------------------------------------------------------
        # 2️⃣ Numeric DD.MM.YYYY pattern (e.g., "20.04.2026")
        # -----------------------------------------------------------------
        m2 = re.search(r"Опубликована\s*[:‑-]?\s*([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4})", text, re.IGNORECASE)
        if not m2:
            m2 = re.search(r"Вакансия\s+опубликована\s*[:‑-]?\s*([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4})", text, re.IGNORECASE)
        if m2:
            date_str = m2.group(1)
            try:
                day, month, year = map(int, date_str.split('.'))
                return datetime(year, month, day, tzinfo=timezone.utc)
            except Exception:
                pass
        return None

    def _detect_work_format(self, text: str) -> WorkFormat:
        lower = normalize_space(text.lower().replace("\xa0", " "))

        has_remote = any(word in lower for word in ["удал", "remote", "remotely"])
        has_hybrid = any(word in lower for word in ["гибрид", "hybrid"])
        has_office = any(
            word in lower
            for word in ["офис", "office", "onsite", "на месте работодателя"]
        )

        if has_hybrid or (has_remote and has_office):
            return WorkFormat.HYBRID

        if has_remote:
            return WorkFormat.REMOTE

        if has_office:
            return WorkFormat.OFFICE

        return WorkFormat.UNKNOWN
