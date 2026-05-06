"""Gmail event-trigger filter builder (Wave 11.I, milestone K).

Moved verbatim from ``services/event_waiter.build_gmail_filter``.
``filter_query`` is applied at the Gmail API level during polling
(see :mod:`._polling`); this filter only checks labels for events
already on the bus.
"""

from __future__ import annotations

from typing import Callable, Dict


def build_gmail_filter(params: Dict) -> Callable[[Dict], bool]:
    """Build filter function for Gmail email events."""
    label_filter = params.get('label_filter', 'INBOX')

    def matches(data: Dict) -> bool:
        if label_filter and label_filter != 'all':
            labels = data.get('labels', [])
            if label_filter not in labels:
                return False
        return True

    return matches
