"""Identify a new catalog request that should not inherit a previous date."""

import re


def starts_new_undated_search(message: str, *, has_date: bool) -> bool:
    if has_date:
        return False
    text = message.strip().casefold().replace("ё", "е")
    return bool(re.match(
        r"^(?:куда\s+(?:сходить|пойти)|что\s+(?:посмотреть|посетить)|"
        r"(?:найди|покажи|посоветуй|подбери)\s+(?!еще\b))\b", text))
