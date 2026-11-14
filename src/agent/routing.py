"""Small deterministic signals for the model router.

This module reads only the user's current message and trusted application state.
It does not inspect tool results or repeat security detection.
"""

import re

from src.agent.schemas import ConstraintSet, RoutingFeatures
from src.agent.security import SecurityFlags
from src.agent.state import DialogueState


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


_TIME = r"\b(?:сегодня|завтра|послезавтра|вчера|выходн\w*|недел\w*|месяц\w*|месяц|суббот\w*|воскресень\w*|пятниц\w*|вечер\w*|утр\w*|день|дня|час\w*|до\s+\d{1,2}:\d{2}|\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?|\d{4}-\d{2}-\d{2})\b"
_GEO = r"\b(?:в\s+(?:уфе|стерлитамаке|салавате|нефтекамске|бирске|сибае|белорецке|ишимбае|октябрьском|туймазах)|из\s+\w+\s+в\s+\w+|рядом|поблизости|недалеко|ближе|добраться|доехать|маршрут\w*|пешком|на\s+машине|в\s+другом\s+городе)\b"
_COMPARISON = r"\b(?:дешевле|подешевле|ближе|поближе|лучше|похож\w*|сравн\w*|выгоднее)\b"
_FOLLOW_UP = r"(?:^|\b)(?:а\s+еще|а\s+в\s+(?:субботу|воскресенье|пятницу|выходные)|подешевле|только\s+бесплатн\w*|в\s+другой\s+день|а\s+если|из\s+них|из\s+этих|это\s+событие|тот\s+же|такие\s+же)\b"
_PLANNER = r"\b(?:спланир\w*|(?:сделай|составь)\s+(?:план|маршрут|программу)|план\s+на\s+(?:день|выходные|субботу|воскресенье)|за\s+один\s+день|успеть\s+на|несколько\s+(?:мероприятий|событий)|после\s+этого|сначала\b.+\bпотом)\b"
_MULTI_STEP = r"\b(?:сначала\b.+\bпотом|после\s+(?:этого|концерта|спектакля)|затем|по\s+пути|успеть\s+на|за\s+один\s+день|несколько\s+(?:мероприятий|событий))\b"


def _intent(text: str, *, follow_up: bool) -> str:
    if _has(text, r"\b(?:отменили|отменен[а-я]*|отменён[а-я]*|состоится\s+ли)\b"):
        return "event_status"
    if _has(text, r"\b(?:подпиши|подписаться|подписку|уведомляй|уведомления)\b"):
        return "subscribe"
    if _has(text, r"\b(?:отзыв|жалоб\w*|обратн\w*\s+связ\w*|сообщить\s+об\s+ошибке)\b"):
        return "save_feedback"
    if _has(text, _PLANNER):
        return "planner"
    if _has(text, r"\b(?:расписание|афиша)\s+(?:в|на|у)\s+(?:театр\w*|кинотеатр\w*|филармони\w*|площадк\w*|дк\b|зале\b)") or _has(text, r"\b(?:что\s+идет|что\s+будет)\s+в\s+(?:театре|кинотеатре|филармонии|дк)\b"):
        return "get_venue_schedule"
    if _has(text, r"\b(?:похож\w*|аналоги|что\s+то\s+вроде)\b"):
        return "find_similar"
    if _has(text, r"\b(?:подробн\w*|расскажи\s+об\s+(?:этом|событии)|карточк\w*\s+событи\w*|/event_[0-9a-f-]+)\b"):
        return "get_event"
    if follow_up or _has(text, r"\b(?:найди|покажи|посоветуй|предложи|куда\s+(?:пойти|сходить)|что\s+(?:посетить|интересного|посмотреть)|есть\s+ли|мероприяти\w*|событи\w*|афиш\w*|концерт\w*|спектакл\w*|балет\w*|джаз\w*|выставк\w*|фестивал\w*|кино|экскурси\w*)\b"):
        return "search_events"
    return "other"


def _constraint_count(text: str, state: DialogueState, parsed: ConstraintSet | None) -> int:
    """Count distinct filter dimensions, not every word or repeated mention."""
    active = state.active_constraints
    dimensions = {
        "city": bool(active.city) or _has(text, _GEO),
        "category": bool(active.category) or _has(text, r"\b(?:концерт\w*|спектакл\w*|выставк\w*|фестивал\w*|кино|экскурси\w*|театр\w*|лекци\w*)\b"),
        "date": bool(active.date_from or active.date_to) or _has(text, _TIME),
        "price": active.max_price is not None or active.is_free_only or _has(text, r"\b(?:бесплатн\w*|подешевле|дешевле|до\s+\d+\s*(?:руб\w*|₽)|не\s+дороже)\b"),
        "age": active.max_age_limit is not None or _has(text, r"\b(?:для\s+детей|с\s+ребенком|с\s+детьми|\d{1,2}\+)\b"),
        "language": _has(text, r"\b(?:на\s+башкирском|на\s+русском|на\s+английском)\b"),
        "venue": _has(text, r"\b(?:в\s+театре|в\s+кинотеатре|в\s+филармонии|на\s+площадке)\b"),
    }
    if parsed is not None:
        dimensions["city"] |= bool(parsed.city)
        dimensions["category"] |= bool(parsed.category)
        dimensions["date"] |= bool(parsed.date_from or parsed.date_to)
        dimensions["price"] |= parsed.max_price is not None or parsed.is_free_only
        dimensions["age"] |= parsed.max_age_limit is not None
    return sum(dimensions.values())


class RoutingFeatureExtractor:
    """Extract routing signals after security preprocessing has produced flags."""

    def extract(
        self, message: str, state: DialogueState, security_flags: SecurityFlags,
        *, parsed_constraints: ConstraintSet | None = None,
    ) -> RoutingFeatures:
        text = " ".join(message.casefold().replace("ё", "е").split())
        follow_up = _has(text, _FOLLOW_UP)
        intent = _intent(text, follow_up=follow_up)
        comparison = _has(text, _COMPARISON)
        temporal = _has(text, _TIME) or bool(state.active_constraints.date_from or state.active_constraints.date_to)
        geo = _has(text, _GEO) or bool(state.active_constraints.city)
        multi_step = intent == "planner" or _has(text, _MULTI_STEP)
        context = follow_up or _has(text, r"\b(?:этот|этом|это|эти|этого|того|из\s+них|тот\s+же|там\s+же|похож\w*)\b")
        depth = len(state.turns)
        return RoutingFeatures(
            intent=intent,
            constraint_count=_constraint_count(text, state, parsed_constraints),
            message_length=len(message), dialogue_turns=depth,
            dialogue_depth=depth, has_previous_results=state.has_previous_results,
            requires_tools=intent != "other", requires_multi_step=multi_step,
            requires_comparison=comparison,
            requires_temporal_reasoning=temporal,
            requires_geo_reasoning=geo,
            requires_dialogue_context=context,
            requires_clarification=intent == "other" or (context and not state.has_previous_results),
            **security_flags.model_dump(),
        )

