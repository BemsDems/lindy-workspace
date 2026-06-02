"""Domain grounding helpers.

For a selected project's domain_tag, this module returns the signal words of
ALL OTHER domains so they can be passed to the validator as extra forbidden
claims. Goal: prevent the letter from drifting into vocabulary from a project
that wasn't selected.
"""

_DOMAIN_SIGNALS = {
    "food":        ["доставка еды", "ресторан", "меню", "корзин", "самовывоз", "бонус", "кэшбэк", "cashback"],
    "realestate":  ["недвижим", "застройщик", "квартир", "ипотек", "риелтор", "девелопер"],
    "social":      ["социальн", "сообществ", "канал", "подписк", "лент", "пост "],
    "b2b_marking": ["маркировк", "честный знак", "erp", "gtin", "оборот товаров"],
    "planner":     ["заметк", "планировани", "календар", "to-do", "голосов"],
}


def foreign_domain_terms(selected_domain_tag: str) -> list[str]:
    """Сигнальные слова всех доменов, КРОМЕ домена выбранного проекта."""
    terms: list[str] = []
    for tag, words in _DOMAIN_SIGNALS.items():
        if tag != selected_domain_tag:
            terms.extend(words)
    return terms
