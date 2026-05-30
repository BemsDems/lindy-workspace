from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from pydantic import HttpUrl, ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vacancy_agent import __version__
from urllib.parse import quote_plus
from vacancy_agent.apply_manager import apply_manager
from vacancy_agent.approval_adapters import ApprovalStatus
from vacancy_agent.application_flow import run_application_flow
from vacancy_agent.batch_generation import pre_generate_cover_letters
from vacancy_agent.browser import browser_manager
from vacancy_agent.config import CANDIDATE_PROFILE_FILE, DATA_DIR, LOGS_DIR
from vacancy_agent.cover_letter import ensure_default_template
from vacancy_agent.export import export_to_csv, export_to_json, export_to_xlsx
from vacancy_agent.runner import runner
from vacancy_agent.schemas import CandidateProfile, SearchParams, SourceType, VacancySource, VacancyStatus
from vacancy_agent.storage import storage
from vacancy_agent.utils.ids import make_id

app = typer.Typer(
    name="vacancy-cli",
    help="CLI-инструмент для сбора вакансий и подготовки откликов",
    add_completion=False,
)
console = Console()


@app.command()
def mark_applied(
    vacancy_id: Optional[str] = typer.Option(None, "--id", help="Vacancy id in local DB"),
    vacancy_url: Optional[str] = typer.Option(None, "--url", help="Vacancy URL"),
) -> None:
    """Manually mark a vacancy as applied by us.

    Supports lookup by either `--id` or `--url` (or both).
    Sets `applied_by_us=true` in the stored vacancy record.
    """

    if not vacancy_id and not vacancy_url:
        console.print("[red]Provide --id or --url[/red]")
        raise typer.Exit(1)

    vacancies = storage.load_vacancies()
    updated = 0
    for v in vacancies:
        if vacancy_id and v.id == vacancy_id:
            v.applied_by_us = True
            updated += 1
            continue
        if vacancy_url and v.url == vacancy_url:
            v.applied_by_us = True
            updated += 1

    if updated == 0:
        console.print("[yellow]No matching vacancy found[/yellow]")
        raise typer.Exit(2)

    storage.save_vacancies(vacancies)
    console.print(f"[green]Marked applied_by_us=true for {updated} vacancy record(s)[/green]")


@app.command()
def init_profile() -> None:
    """Создать шаблон профиля кандидата и шаблон письма.

    Важно: профиль создаётся в nested resume-схеме из cover-letter-gen,
    а не в старой плоской demo-схеме.
    """
    if CANDIDATE_PROFILE_FILE.exists():
        console.print(f"[yellow]Профиль уже существует: {CANDIDATE_PROFILE_FILE}[/yellow]")
    else:
        profile = CandidateProfile(
            personal={
                "name": "Дуков Тамерлан",
                "birth_date": "2003-01-04",
                "age": 23,
                "gender": "male",
                "location": "Нальчик",
                "citizenship": "Россия",
                "relocation": False,
                "business_trips": "редкие",
            },
            contacts={
                "phone": "+7 (964) 034-30-55",
                "email": "avapa2015@yandex.ru",
                "telegram": "@jaqFresco",
                "telegram_url": "https://t.me/jaqFresco",
            },
            desired={
                "title": "Flutter developer",
                "specializations": ["Программист, разработчик"],
                "employment_type": "полная занятость",
                "work_format": ["удалённо", "на месте работодателя", "гибрид"],
                "commute_time": "не имеет значения",
            },
            summary=(
                "Flutter-разработчик с опытом более 3-х лет коммерческой разработки. "
                "Работал как с legacy-кодом, так и с проектами с нуля: "
                "архитектура, инфраструктура, интеграции."
            ),
            skills={
                "primary": ["Dart", "Flutter", "BLoC", "Cubit", "Clean Architecture", "REST API", "gRPC"],
                "secondary": ["Firebase", "FCM", "JWT", "Secure Storage", "WebView", "Freezed", "Auto Route", "SQLite", "Drift", "Sentry"],
                "soft": ["Git", "GitHub", "GitLab", "Jira", "Figma", "Agile", "CI/CD"],
                "architecture": ["Clean Architecture", "MVVM", "MVC", "Feature-based Architecture"],
                "principles": ["SOLID", "DRY", "ООП"],
            },
            experience={
                "total_years": 3,
                "total_months": 2,
                "positions": [],
            },
            restrictions=[
                "не указывать несуществующий коммерческий опыт",
                "не завышать уровень английского",
                "не обещать релокацию, если она не указана",
            ],
        )
        storage.save_candidate_profile(profile)
        console.print(f"[green]Создан профиль: {CANDIDATE_PROFILE_FILE}[/green]")

    ensure_default_template()
    console.print("[green]Шаблон сопроводительного письма готов[/green]")


@app.command()
def add_source(
    name: str = typer.Option(..., "--name", "-n", help="Название источника"),
    url: str = typer.Option(..., "--url", "-u", help="URL страницы с вакансиями"),
    source_type: SourceType = typer.Option(SourceType.PLAYWRIGHT, "--type", "-t", help="Тип источника"),
    vacancy_link_selector: Optional[str] = typer.Option(None, "--vacancy-link-selector", help="CSS-селектор ссылок на вакансии"),
    title_selector: Optional[str] = typer.Option(None, "--title-selector", help="CSS-селектор заголовка вакансии"),
    company_selector: Optional[str] = typer.Option(None, "--company-selector", help="CSS-селектор компании"),
    description_selector: Optional[str] = typer.Option(None, "--description-selector", help="CSS-селектор описания"),
    query_param: Optional[str] = typer.Option(None, "--query-param", help="Имя query-параметра для должности"),
    city_param: Optional[str] = typer.Option(None, "--city-param", help="Имя query-параметра для города"),
) -> None:
    """Добавить источник вакансий."""
    try:
        validated_url = str(HttpUrl(url))
    except ValidationError:
        console.print("[red]Invalid URL[/red]")
        raise typer.Exit(1)

    sources = storage.load_sources()
    source_id = make_id(validated_url, length=10)

    if any(item.id == source_id for item in sources):
        console.print("[yellow]Источник с таким URL уже существует[/yellow]")
        return

    selectors = {}
    if vacancy_link_selector:
        selectors["vacancy_link"] = vacancy_link_selector
    if title_selector:
        selectors["title"] = title_selector
    if company_selector:
        selectors["company"] = company_selector
    if description_selector:
        selectors["description"] = description_selector

    settings = {}
    if query_param:
        settings["query_param"] = query_param
    if city_param:
        settings["city_param"] = city_param

    source = VacancySource(
        id=source_id,
        name=name,
        url=validated_url,
        type=source_type,
        selectors=selectors,
        settings=settings,
    )
    sources.append(source)
    storage.save_sources(sources)

    console.print(f"[green]Источник добавлен:[/green] {name} ({source_id})")


@app.command("sources")
def list_sources() -> None:
    """Показать сохранённые источники."""
    sources = storage.load_sources()
    if not sources:
        console.print("[yellow]Источники не добавлены[/yellow]")
        return

    table = Table(title="Источники")
    table.add_column("ID", style="cyan")
    table.add_column("Название", style="green")
    table.add_column("URL")
    table.add_column("Тип")
    table.add_column("Статус")

    for source in sources:
        table.add_row(source.id, source.name, source.url[:70], source.type.value, "on" if source.enabled else "off")

    console.print(table)


@app.command("search-url")
def search_url(
    urls: list[str] = typer.Argument(..., help="URL страниц с вакансиями"),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Должность"),
    city: Optional[str] = typer.Option(None, "--city", "-c", help="Город"),
    remote: bool = typer.Option(False, "--remote", help="Только удалёнка"),
    max_pages: int = typer.Option(1, "--max-pages", help="Максимум страниц"),
    max_vacancies: int = typer.Option(30, "--max-vacancies", help="Максимум вакансий на источник"),
    allow_hh_actions: bool = typer.Option(
        False,
        "--allow-hh-actions",
        help="(HH) Разрешить аккаунтные действия (например автоклик 'Поднять'). По умолчанию read-only.",
    ),
    cdp_url: Optional[str] = typer.Option(
        None,
        "--cdp",
        "--cdp-url",
        help="CDP endpoint браузера, например http://127.0.0.1:9222",
    ),
) -> None:
    """Искать вакансии по прямым URL без предварительного добавления источника."""
    browser_manager.configure(cdp_url=cdp_url)
    params = SearchParams(
        query=query,
        city=city,
        remote=remote,
        max_pages=max_pages,
        max_vacancies=max_vacancies,
        allow_hh_actions=allow_hh_actions,
    )

    with console.status("[bold green]Сбор вакансий...[/bold green]"):
        vacancies = asyncio.run(runner.search_urls(urls, params))

    console.print(f"[green]Собрано новых/обновлённых вакансий: {len(vacancies)}[/green]")
    _print_vacancies(vacancies[:10])


@app.command("search")
def search_sources(
    source: Optional[str] = typer.Option(None, "--source", "-s", help="ID или имя источника"),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Должность"),
    city: Optional[str] = typer.Option(None, "--city", "-c", help="Город"),
    remote: bool = typer.Option(False, "--remote", help="Только удалёнка"),
    max_pages: int = typer.Option(1, "--max-pages", help="Максимум страниц"),
    max_vacancies: int = typer.Option(30, "--max-vacancies", help="Максимум вакансий на источник"),
    allow_hh_actions: bool = typer.Option(
        False,
        "--allow-hh-actions",
        help="(HH) Разрешить аккаунтные действия (например автоклик 'Поднять'). По умолчанию read-only.",
    ),
    cdp_url: Optional[str] = typer.Option(
        None,
        "--cdp",
        "--cdp-url",
        help="CDP endpoint браузера, например http://127.0.0.1:9222",
    ),
) -> None:
    """Искать вакансии по сохранённым источникам."""
    browser_manager.configure(cdp_url=cdp_url)
    sources = storage.load_sources()
    if source:
        found = storage.find_source(source)
        if not found:
            console.print(f"[red]Источник не найден: {source}[/red]")
            raise typer.Exit(1)
        sources = [found]

    if not sources:
        console.print("[yellow]Нет источников. Используй add-source или search-url.[/yellow]")
        raise typer.Exit(1)

    params = SearchParams(
        query=query,
        city=city,
        remote=remote,
        max_pages=max_pages,
        max_vacancies=max_vacancies,
        allow_hh_actions=allow_hh_actions,
    )

    with console.status("[bold green]Сбор вакансий...[/bold green]"):
        vacancies = asyncio.run(runner.search_sources(sources, params))

    console.print(f"[green]Собрано новых/обновлённых вакансий: {len(vacancies)}[/green]")
    _print_vacancies(vacancies[:10])


@app.command("list")
def list_vacancies(
    status: Optional[VacancyStatus] = typer.Option(
        VacancyStatus.NEW,
        "--status",
        help="Фильтр по статусу (по умолчанию: только новые)",
    ),
    all: bool = typer.Option(False, "--all", help="Показать все вакансии (снимает фильтр по статусу)"),
    limit: int = typer.Option(30, "--limit", "-l", help="Сколько показать"),
) -> None:
    """Показать сохранённые вакансии.

    По умолчанию показывает только новые (status=new). Чтобы увидеть просмотренные и остальные,
    используй --all или --status <value>.
    """
    vacancies = storage.load_vacancies()

    if all:
        status = None

    if status:
        vacancies = [vacancy for vacancy in vacancies if vacancy.status == status]

    if not vacancies:
        console.print("[yellow]Вакансии не найдены[/yellow]")
        return

    _print_vacancies(vacancies[:limit])


@app.command()
def show(vacancy_id: str) -> None:
    """Показать вакансию подробно.

    Если вакансия была новой, помечает её как просмотренную (viewed).
    """
    vacancy = storage.find_vacancy(vacancy_id)
    if not vacancy:
        console.print(f"[red]Вакансия не найдена: {vacancy_id}[/red]")
        raise typer.Exit(1)

    if vacancy.status == VacancyStatus.NEW:
        storage.update_vacancy_status(vacancy.id, VacancyStatus.VIEWED)
        vacancy = storage.find_vacancy(vacancy.id) or vacancy

    body = (
        f"[bold]{vacancy.title}[/bold]\n"
        f"Компания: {vacancy.company}\n"
        f"Зарплата: {vacancy.salary or 'не указана'}\n"
        f"Локация: {vacancy.location or 'не указана'}\n"
        f"Формат: {vacancy.work_format.value}\n"
        f"Занятость: {getattr(vacancy, 'employment_type', None) or 'не указано'}\n"
        f"Опыт: {getattr(vacancy, 'experience', None) or 'не указано'}\n"
        f"График: {getattr(vacancy, 'work_schedule', None) or 'не указано'}\n"
        f"Часы: {getattr(vacancy, 'working_hours', None) or 'не указано'}\n"
        f"Источник: {vacancy.source_name}\n"
        f"Статус: {vacancy.status.value}\n"
        f"URL: {vacancy.url}\n\n"
        f"[bold]Описание:[/bold]\n{vacancy.description or 'нет описания'}"
    )
    console.print(Panel(body, title=f"Вакансия {vacancy.id}", border_style="blue"))


@app.command()
def open(
    vacancy_id: str,
    include_viewed: bool = typer.Option(False, "--include-viewed", help="Разрешить открывать уже просмотренные вакансии"),
) -> None:
    """Открыть вакансию в браузере.

    По умолчанию открывает только новые вакансии и помечает их как viewed.
    """
    # Reuse a single Playwright tab instead of spawning a new external browser window.
    vacancy = storage.find_vacancy(vacancy_id)
    if not vacancy:
        console.print(f"[red]Вакансия не найдена: {vacancy_id}[/red]")
        raise typer.Exit(1)

    if (not include_viewed) and vacancy.status != VacancyStatus.NEW:
        console.print(
            f"[yellow]Вакансия уже не новая (status={vacancy.status.value}). "
            f"Используй --include-viewed чтобы открыть её.[/yellow]"
        )
        raise typer.Exit(1)

    # Mark as viewed on open
    if vacancy.status == VacancyStatus.NEW:
        storage.update_vacancy_status(vacancy.id, VacancyStatus.VIEWED)

    # Navigate the shared page to the vacancy URL (replaces address, does not open a new tab).
    async def _navigate():
        page = await browser_manager.get_shared_page()
        await page.goto(vacancy.url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
        await browser_manager.random_delay()
    asyncio.run(_navigate())
    console.print(f"[green]Открыто (перейдено к):[/green] {vacancy.url}")


@app.command()
def apply(vacancy_id: str) -> None:
    """Подготовить сопроводительное письмо и черновик отклика."""
    ok = apply_manager.apply_to_vacancy(vacancy_id)
    if not ok:
        raise typer.Exit(1)


@app.command("submit")
def submit_application(
    vacancy_id: str = typer.Argument(..., help="Vacancy id/prefix, numeric HH id, or full vacancy URL"),
    letter_file: Optional[Path] = typer.Option(
        None,
        "--letter-file",
        help="Путь к файлу с уже утверждённым сопроводительным письмом",
    ),
    cover_letter: Optional[str] = typer.Option(
        None,
        "--cover-letter",
        help="Текст утверждённого сопроводительного письма. Если не указан — берётся последний черновик или генерируется новый.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--send",
        help="По умолчанию безопасный режим: вставить письмо, но не нажимать финальную отправку. Используй --send для реальной отправки.",
    ),
    cdp_url: Optional[str] = typer.Option(
        None,
        "--cdp",
        "--cdp-url",
        help="CDP endpoint браузера, например http://127.0.0.1:9222",
    ),
) -> None:
    """Отправить утверждённое письмо через apply-adapter платформы.

    Сейчас реализован адаптер HH (`hh.ru`/`hh.kz`). Другие платформы
    добавляются отдельными адаптерами в `vacancy_agent/apply_adapters/`.
    """

    browser_manager.configure(cdp_url=cdp_url)
    ok = apply_manager.submit_to_vacancy(
        vacancy_id,
        cover_letter=cover_letter,
        letter_file=letter_file,
        dry_run=dry_run,
    )
    if not ok:
        raise typer.Exit(1)


@app.command("apply-many")
def apply_many(
    vacancy_ids: Optional[list[str]] = typer.Argument(
        None,
        help="Список vacancy id/prefix, HH id или URL. Можно не указывать, если используется --ids-file или --status.",
    ),
    ids_file: Optional[Path] = typer.Option(
        None,
        "--ids-file",
        help="Файл со списком vacancy id / HH id / URL, по одному на строку.",
    ),
    status: Optional[VacancyStatus] = typer.Option(
        VacancyStatus.NEW,
        "--status",
        help="Если ID не переданы, взять вакансии с этим статусом.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Максимум вакансий для обработки. 0 = без лимита.",
    ),
    parallel: int = typer.Option(
        3,
        "--parallel",
        "-p",
        help="Сколько писем генерировать параллельно.",
    ),
    force: bool = typer.Option(
        False,
        "--force-regenerate",
        help="Перегенерировать даже если черновик уже есть.",
    ),
    debug_letters: bool = typer.Option(
        False,
        "--debug-letters",
        envvar="COVER_LETTER_DEBUG",
        help="Сохранить подробный Markdown-отчёт: вакансия, метаданные генерации и письмо.",
    ),
    debug_letters_file: Optional[Path] = typer.Option(
        None,
        "--debug-letters-file",
        help="Куда сохранить debug-отчёт. По умолчанию logs/cover_letter_debug_YYYYMMDD_HHMMSS.md.",
    ),
) -> None:
    """Параллельно сгенерировать сопроводительные письма для нескольких вакансий."""

    resolved_ids = _resolve_many_vacancy_ids(vacancy_ids, ids_file, status, limit)

    if not resolved_ids:
        console.print("[yellow]Нет вакансий для обработки[/yellow]")
        return

    console.print(
        f"[blue]Параллельная генерация писем: {len(resolved_ids)} вакансий, parallel={parallel}[/blue]"
    )

    results = asyncio.run(
        pre_generate_cover_letters(
            resolved_ids,
            parallel=parallel,
            force=force,
            debug_letters=debug_letters,
            debug_file=debug_letters_file,
        )
    )

    success = 0
    failed = 0

    for index, result in enumerate(results, start=1):
        if result.ok:
            success += 1
            _print_batch_result_row(
                index,
                len(results),
                result.vacancy_id,
                result.status,
                result.message,
            )
        else:
            failed += 1
            _print_batch_result_row(
                index,
                len(results),
                result.vacancy_id,
                result.status,
                result.message,
            )

    console.print(
        Panel(
            f"Всего: {len(results)}\n"
            f"Успешно: {success}\n"
            f"Ошибки: {failed}",
            title="apply-many result",
            border_style="green" if failed == 0 else "yellow",
        )
    )

    if failed:
        raise typer.Exit(1)


@app.command("approve-submit")
def approve_submit_application(
    vacancy_id: str = typer.Argument(..., help="Vacancy id/prefix, numeric HH id, or full vacancy URL"),
    letter_file: Optional[Path] = typer.Option(
        None,
        "--letter-file",
        help="Путь к файлу с начальным текстом письма",
    ),
    cover_letter: Optional[str] = typer.Option(
        None,
        "--cover-letter",
        help="Начальный текст письма. Если не указан — берётся последний черновик или генерируется новый.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--send",
        help="Сначала безопасный режим: письмо вставится, но финальная отправка не нажмётся. Используй --send после проверки.",
    ),
    cdp_url: Optional[str] = typer.Option(
        None,
        "--cdp",
        "--cdp-url",
        help="CDP endpoint браузера, например http://127.0.0.1:9222",
    ),
) -> None:
    """Сквозной flow: генерация -> ручное согласование/правка -> apply-adapter."""

    browser_manager.configure(cdp_url=cdp_url)
    result = run_application_flow(
        vacancy_id,
        cover_letter=cover_letter,
        letter_file=letter_file,
        dry_run=dry_run,
    )

    apply_status = result.apply_result.status.value if result.apply_result else "not_submitted"
    apply_message = result.apply_result.message if result.apply_result else result.message
    style = "green" if result.was_sent else "yellow" if apply_status in {"dry_run_success", "not_submitted"} else "red"
    console.print(
        Panel(
            f"Vacancy: {result.vacancy_id}\n"
            f"Approval: {result.approval_status.value}\n"
            f"Apply: {apply_status}\n"
            f"URL: {result.vacancy_url}\n"
            f"Message: {apply_message or '—'}",
            title="approve-submit result",
            border_style=style,
        )
    )

    if result.apply_result and result.apply_result.is_success:
        return
    if result.approval_status.value in {"rejected", "timeout"}:
        raise typer.Exit(1)
    if result.apply_result and result.apply_result.needs_manual_action:
        raise typer.Exit(2)
    raise typer.Exit(1)


@app.command("approve-submit-many")
def approve_submit_many(
    vacancy_ids: Optional[list[str]] = typer.Argument(
        None,
        help="Список vacancy id/prefix, HH id или URL. Можно не указывать, если используется --ids-file или --status.",
    ),
    ids_file: Optional[Path] = typer.Option(
        None,
        "--ids-file",
        help="Файл со списком vacancy id / HH id / URL, по одному на строку.",
    ),
    status: Optional[VacancyStatus] = typer.Option(
        VacancyStatus.NEW,
        "--status",
        help="Если ID не переданы, взять вакансии с этим статусом.",
    ),
    limit: int = typer.Option(
        5,
        "--limit",
        "-l",
        help="Максимум вакансий для обработки. 0 = без лимита.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--send",
        help="По умолчанию безопасный режим: вставить письмо, но не нажимать финальную отправку. Используй --send для реальной отправки.",
    ),
    cdp_url: Optional[str] = typer.Option(
        None,
        "--cdp",
        "--cdp-url",
        help="CDP endpoint браузера, например http://127.0.0.1:9222",
    ),
    pre_generate: bool = typer.Option(
        True,
        "--pre-generate/--no-pre-generate",
        help="Перед approval параллельно подготовить черновики писем.",
    ),
    parallel: int = typer.Option(
        3,
        "--parallel",
        "-p",
        help="Сколько писем генерировать параллельно на этапе pre-generate.",
    ),
    force_regenerate: bool = typer.Option(
        False,
        "--force-regenerate",
        help="Перегенерировать черновики даже если они уже есть.",
    ),
    debug_letters: bool = typer.Option(
        False,
        "--debug-letters",
        envvar="COVER_LETTER_DEBUG",
        help="Сохранить подробный Markdown-отчёт: вакансия, метаданные генерации и письмо.",
    ),
    debug_letters_file: Optional[Path] = typer.Option(
        None,
        "--debug-letters-file",
        help="Куда сохранить debug-отчёт.",
    ),
) -> None:
    """Гибридный batch-flow: параллельная генерация -> последовательный approval/edit -> apply-adapter."""

    browser_manager.configure(cdp_url=cdp_url)

    resolved_ids = _resolve_many_vacancy_ids(vacancy_ids, ids_file, status, limit)

    if not resolved_ids:
        console.print("[yellow]Нет вакансий для обработки[/yellow]")
        return

    total = len(resolved_ids)

    if pre_generate:
        console.print(
            f"[blue]Этап 1/2: параллельная генерация черновиков: {total} вакансий, parallel={parallel}[/blue]"
        )

        if debug_letters:
            debug_letters_file = debug_letters_file or LOGS_DIR / f"cover_letter_debug_{datetime.now():%Y%m%d_%H%M%S}.md"
            console.print(f"[cyan]Debug-отчёт будет сохранён: {debug_letters_file}[/cyan]")

        generation_results = asyncio.run(
            pre_generate_cover_letters(
                resolved_ids,
                parallel=parallel,
                force=force_regenerate,
                debug_letters=debug_letters,
                debug_file=debug_letters_file,
            )
        )

        generation_failed_ids: set[str] = set()

        for index, item in enumerate(generation_results, start=1):
            _print_batch_result_row(
                index,
                total,
                item.vacancy_id,
                item.status,
                item.message,
            )

            if not item.ok:
                generation_failed_ids.add(item.vacancy_id)

        if generation_failed_ids:
            console.print(
                f"[yellow]Не удалось подготовить писем: {len(generation_failed_ids)}. "
                f"Эти вакансии будут пропущены на этапе отправки.[/yellow]"
            )

        resolved_ids = [
            vacancy_id
            for vacancy_id in resolved_ids
            if (storage.find_vacancy(vacancy_id) and storage.find_vacancy(vacancy_id).id not in generation_failed_ids)
            or vacancy_id not in generation_failed_ids
        ]

        if not resolved_ids:
            console.print("[yellow]После pre-generate не осталось вакансий для approval/send[/yellow]")
            raise typer.Exit(1)

    console.print(f"[blue]Этап 2/2: последовательный approval и отклик: {len(resolved_ids)} вакансий[/blue]")

    success = 0
    manual = 0
    skipped = 0
    failed = 0

    for index, vacancy_id in enumerate(resolved_ids, start=1):
        console.rule(f"[bold blue]{index}/{len(resolved_ids)} vacancy={vacancy_id}")

        try:
            result = run_application_flow(
                vacancy_id,
                dry_run=dry_run,
            )

            apply_status = result.apply_result.status.value if result.apply_result else "not_submitted"
            apply_message = result.apply_result.message if result.apply_result else result.message

            if result.apply_result and result.apply_result.is_success:
                success += 1

            elif result.approval_status == ApprovalStatus.DRAFT:
                skipped += 1

            elif result.apply_result and result.apply_result.needs_manual_action:
                manual += 1

            elif apply_status in {"archived", "unsupported_platform"} or result.approval_status.value == "rejected":
                skipped += 1

            else:
                failed += 1

            _print_batch_result_row(
                index,
                len(resolved_ids),
                result.vacancy_id,
                apply_status,
                apply_message,
            )

        except KeyboardInterrupt:
            console.print("[yellow]Batch остановлен пользователем[/yellow]")
            raise typer.Exit(130)

        except Exception as error:
            failed += 1
            _print_batch_result_row(index, len(resolved_ids), vacancy_id, "error", str(error))

    console.print(
        Panel(
            f"Всего: {len(resolved_ids)}\n"
            f"Успешно: {success}\n"
            f"Ручная обработка: {manual}\n"
            f"Пропущено: {skipped}\n"
            f"Ошибки: {failed}",
            title="approve-submit-many result",
            border_style="green" if failed == 0 else "yellow",
        )
    )

    if failed:
        raise typer.Exit(1)


@app.command()
def export(
    format: str = typer.Option("json", "--format", "-f", help="json/csv/xlsx"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Путь к файлу"),
) -> None:
    """Экспортировать вакансии."""
    vacancies = storage.load_vacancies()
    if not vacancies:
        console.print("[yellow]Нет вакансий для экспорта[/yellow]")
        return

    if not output:
        output = DATA_DIR / f"vacancies_export.{format}"

    if format == "json":
        export_to_json(vacancies, output)
    elif format == "csv":
        export_to_csv(vacancies, output)
    elif format == "xlsx":
        export_to_xlsx(vacancies, output)
    else:
        console.print("[red]Поддерживаются только json/csv/xlsx[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Экспорт готов:[/green] {output}")


@app.command()
def status() -> None:
    """Показать статистику."""
    stats = storage.get_stats()
    console.print(Panel.fit("Статус Vacancy Agent", border_style="blue"))
    console.print(f"Всего вакансий: [bold]{stats['vacancies_total']}[/bold]")
    console.print(f"Черновиков откликов: [bold]{stats['applications_total']}[/bold]")

    if stats["by_status"]:
        console.print("\n[bold]По статусам:[/bold]")
        for key, value in stats["by_status"].items():
            console.print(f"  {key}: {value}")

    if stats["by_source"]:
        console.print("\n[bold]По источникам:[/bold]")
        for key, value in stats["by_source"].items():
            console.print(f"  {key}: {value}")


@app.command()
def version() -> None:
    console.print(f"Vacancy Agent v{__version__}")


def _read_ids_file(ids_file: Path | None) -> list[str]:
    if not ids_file:
        return []

    if not ids_file.exists():
        console.print(f"[red]Файл с ID не найден: {ids_file}[/red]")
        raise typer.Exit(1)

    ids: list[str] = []

    for line_number, raw_line in enumerate(ids_file.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if "#" in line:
            line = line.split("#", 1)[0].strip()

        if not line:
            continue

        ids.append(line)

    if not ids:
        console.print(f"[yellow]Файл с ID пустой: {ids_file}[/yellow]")

    return ids


def _resolve_many_vacancy_ids(
    vacancy_ids: list[str] | None,
    ids_file: Path | None,
    status: VacancyStatus | None,
    limit: int,
) -> list[str]:
    explicit_ids = list(vacancy_ids or [])
    file_ids = _read_ids_file(ids_file)

    combined_ids = [*explicit_ids, *file_ids]

    if combined_ids:
        seen: set[str] = set()
        unique_ids: list[str] = []

        for item in combined_ids:
            if item not in seen:
                seen.add(item)
                unique_ids.append(item)

        return unique_ids[:limit] if limit > 0 else unique_ids

    vacancies = storage.load_vacancies()

    if status:
        vacancies = [vacancy for vacancy in vacancies if vacancy.status == status]

    if limit > 0:
        vacancies = vacancies[:limit]

    return [vacancy.id for vacancy in vacancies]


def _print_batch_result_row(index: int, total: int, vacancy_id: str, status: str, message: str | None = None) -> None:
    if message:
        console.print(f"[{index}/{total}] {vacancy_id}: [bold]{status}[/bold] — {message}")
    else:
        console.print(f"[{index}/{total}] {vacancy_id}: [bold]{status}[/bold]")


def _print_vacancies(vacancies) -> None:
    table = Table(title=f"Вакансии ({len(vacancies)})")
    table.add_column("ID", style="cyan")
    table.add_column("Название", style="bold")
    table.add_column("Компания", style="green")
    table.add_column("Зарплата", style="yellow")
    table.add_column("Страна")
    table.add_column("Локация")
    table.add_column("Формат")
    table.add_column("Статус")

    for vacancy in vacancies:
        table.add_row(
            vacancy.id[:8],
            vacancy.title[:50],
            vacancy.company[:30],
            vacancy.salary or "—",
            vacancy.country or "—",
            vacancy.location or "—",
            vacancy.work_format_raw or vacancy.work_format.value or "—",
            vacancy.status.value,
        )

    console.print(table)


@app.command("search-hh-priority")
def search_hh_priority(
    query: str = typer.Option("flutter", "--query", "-q", help="Должность"),
    max_pages: int = typer.Option(1, "--max-pages", help="Максимум страниц на каждый проход"),
    max_vacancies: int = typer.Option(20, "--max-vacancies", help="Максимум вакансий на каждый проход"),
    cdp_url: Optional[str] = typer.Option(
        None,
        "--cdp",
        "--cdp-url",
        help="CDP endpoint браузера, например http://127.0.0.1:9222",
    ),
) -> None:
    """Отдельный HH-поиск по приоритетам: Россия -> Беларусь -> любые страны.

    Команду search не меняет.
    """

    browser_manager.configure(cdp_url=cdp_url)

    encoded_query = quote_plus(query)

    base_url = (
        "https://nalchik.hh.ru/search/vacancy"
        "?hhtmFrom=main"
        "&hhtmFromLabel=vacancy_search_line"
        "&search_field=name"
        "&search_field=company_name"
        "&enable_snippets=false"
        "&L_save_area=true"
        f"&text={encoded_query}"
    )

    first_country = "Россия"

    sources = [
        VacancySource(
            id="hh-priority-russia-113",
            name="hh-priority-russia-113",
            url=f"{base_url}&area=113",
            type=SourceType.PLAYWRIGHT,
            enabled=True,
            settings={
                "country": "Россия",
                "priority": 1,
            },
        ),
        VacancySource(
            id="hh-priority-belarus-16",
            name="hh-priority-belarus-16",
            url=f"{base_url}&area=16",
            type=SourceType.PLAYWRIGHT,
            enabled=True,
            settings={
                "country": "Беларусь",
                "priority": 2,
            },
        ),
        VacancySource(
            id="hh-priority-any",
            name="hh-priority-any",
            url=base_url,
            type=SourceType.PLAYWRIGHT,
            enabled=True,
            settings={
                "priority": 3,
            },
        ),
    ]

    params = SearchParams(
        query=None,
        max_pages=max_pages,
        max_vacancies=max_vacancies,
    )

    console.print(
        "[blue]HH priority search:[/blue] "
        "Россия -> Беларусь -> любые страны"
    )

    all_vacancies = []

    for source in sources:
        console.print(f"[blue]Проход:[/blue] {source.name}")

        with console.status(f"[bold green]Сбор: {source.name}...[/bold green]"):
            batch = asyncio.run(runner.search_sources([source], params))

        all_vacancies.extend(batch)

        console.print(
            f"[green]{source.name}: собрано {len(batch)} новых/обновлённых вакансий[/green]"
        )

    console.print(f"[green]Всего собрано: {len(all_vacancies)}[/green]")
    _print_vacancies(all_vacancies[:30])


if __name__ == "__main__":
    app()
