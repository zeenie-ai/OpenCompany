"""Twitter event-trigger filter builder (Wave 11.I, milestone K).

Moved verbatim from ``services/event_waiter.build_twitter_filter``.
"""

from __future__ import annotations

from typing import Callable, Dict


def build_filter(params: Dict) -> Callable[[Dict], bool]:
    """Build filter function for Twitter events.

    Filters by:

    - ``trigger_type`` -- ``mentions`` / ``search`` / ``user_timeline``
    - ``search_query`` -- search query for ``search``
    - ``user_id`` -- user ID for ``user_timeline``
    """
    trigger_type = params.get('trigger_type', 'mentions')
    search_query = params.get('search_query', '')
    user_id = params.get('user_id', '')

    def matches(data: Dict) -> bool:
        event_type = data.get('trigger_type', '')
        if trigger_type != 'all' and event_type != trigger_type:
            return False
        if trigger_type == 'search' and search_query:
            event_query = data.get('query', '')
            if search_query.lower() not in event_query.lower():
                return False
        if trigger_type == 'user_timeline' and user_id:
            if data.get('user_id') != user_id:
                return False
        return True

    return matches
