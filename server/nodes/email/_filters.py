"""Email event-trigger filter builder (Wave 11.I, milestone K).

Moved verbatim from ``services/event_waiter.build_email_filter``.
"""

from __future__ import annotations

from typing import Callable, Dict


def build_filter(params: Dict) -> Callable[[Dict], bool]:
    """Build filter for email events (Himalaya IMAP polling)."""
    folder_filter = params.get('folder', 'INBOX')

    def matches(data: Dict) -> bool:
        if folder_filter and folder_filter != 'all':
            if data.get('folder', '') != folder_filter:
                return False
        return True

    return matches
