"""Generated UI shown inside chat replies (docs-internal/chat_protocol.md,
"Generated UI"): checking a spec against ``config/chat_genui_catalog.json``
(``spec.py``), streaming it as JSON patches (``patches.py``), and the
owner's Generated UI switch, which turns it off with the follow-ups and the
hire screen (``switch.py``).

Core, so it never imports ``nodes/``; the ``show_ui`` tool
(``nodes/chat/chat_ui``) is its one producer.
"""
