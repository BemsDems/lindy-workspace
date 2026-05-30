def _score_project_for_vacancy(vacancy: Vacancy, project: object) -> int:
    vacancy_text = _vacancy_text(vacancy)
    project_text = _project_text(project)

    score = 0

    # Базовое совпадение технологий
    for term in [
        "flutter", "dart", "bloc", "cubit", "clean architecture", "dio",
        "rest", "grpc", "firebase", "sentry", "sqlite", "drift",
        "secure storage", "jwt", "deep links", "branch sdk", "auto route",
        "webview", "yandex mapkit",
    ]:
        if term in vacancy_text and term in project_text:
            score += 3

    # B2B / backend / API / full stack
    b2b_vacancy = any(
        x in vacancy_text
        for x in [
            "full stack", "fullstack", "api", "backend", "бэкенд", "сервер",
            "интеграц", "grpc", "rest", "b2b", "бизнес-логик",
            "корпоратив", "личный кабинет", "база данных",
        ]
    )
    b2b_project = any(
        x in project_text
        for x in [
            "b2b", "честный знак", "маркировк", "grpc", "rest",
            "бизнес-формат", "корпоратив", "api",
        ]
    )
    if b2b_vacancy and b2b_project:
        score += 12

    # Медиа / видео / подписки / офлайн
    media_vacancy = any(
        x in vacancy_text
        for x in [
            "видео", "video", "drm", "плеер", "player", "offline",
            "офлайн", "контент", "подписк", "медиа",
        ]
    )
    media_project = any(
        x in project_text
        for x in [
            "социальн", "пост", "канал", "подписк", "медиа",
            "контент", "offline", "офлайн", "retry",
        ]
    )
    if media_vacancy and media_project:
        score += 12

    # Доставка еды / корзина / заказ
    food_vacancy = any(
        x in vacancy_text
        for x in [
            "доставк", "еда", "food", "заказ", "корзин", "самовывоз",
            "бонус", "cashback",
        ]
    )
    food_project = any(
        x in project_text
        for x in [
            "доставк", "еда", "food", "заказ", "корзин", "самовывоз",
            "бонус", "cashback",
        ]
    )
    if food_vacancy and food_project:
        score += 12

    # Авторизация / профиль / роли
    auth_vacancy = any(
        x in vacancy_text
        for x in [
            "авторизац", "auth", "profile", "профил", "otp",
            "роль", "роли", "права", "vk id", "yandex id",
        ]
    )
    auth_project = any(
        x in project_text
        for x in [
            "авторизац", "auth", "profile", "профил", "otp",
            "роль", "роли", "права", "vk id", "yandex id",
        ]
    )
    if auth_vacancy and auth_project:
        score += 8

    # Не даём B2B-проекту побеждать просто потому, что там сильные цифры
    generic_flutter_vacancy = (
        "flutter" in vacancy_text
        and not b2b_vacancy
        and not media_vacancy
        and not food_vacancy
        and not auth_vacancy
    )
    if generic_flutter_vacancy and b2b_project:
        score -= 6

    # Не выбираем соцсеть, если вакансия вообще не про медиа/контент/профили/роли
    social_project = any(
        x in project_text
        for x in ["социальн", "сообществ", "канал", "пост", "подписк"]
    )
    if social_project and not media_vacancy and not auth_vacancy:
        score -= 5

    return score