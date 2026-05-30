from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
LOGS_DIR = PROJECT_ROOT / "logs"

VACANCIES_FILE = DATA_DIR / "vacancies.json"
SOURCES_FILE = DATA_DIR / "sources.json"
APPLICATIONS_FILE = DATA_DIR / "applications.json"
CANDIDATE_PROFILE_FILE = DATA_DIR / "candidate_profile.yaml"
COVER_LETTER_TEMPLATE_FILE = TEMPLATES_DIR / "cover_letter_template.md"
LOG_FILE = LOGS_DIR / "vacancy_agent.log"

for path in (DATA_DIR, TEMPLATES_DIR, LOGS_DIR):
    path.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    browser_headless: bool = False
    browser_cdp_url: Optional[str] = None
    browser_timeout_ms: int = 30000
    browser_delay_min: float = 1.0
    browser_delay_max: float = 3.0

    max_retries: int = 2
    max_vacancies_per_source: int = 30
    store_raw_html: bool = False

    log_level: str = "INFO"

    # Cover-letter generation provider.
    # auto: use external cover-letter-gen when found, otherwise local fallback.
    # cover_letter_gen: require external generator.
    # simple: force local fallback template generator.
    cover_letter_provider: str = "auto"
    cover_letter_gen_path: Optional[Path] = None
    cover_letter_gen_resume: Optional[Path] = None
    cover_letter_gen_settings: Optional[Path] = None
    cover_letter_append_signature: bool = True


settings = Settings()
