"""treg: a paid catalog of data and generation tools, reached through treg's own CLI.

treg (https://treg.to) puts thousands of third-party tools (search and SEO
data, social profiles, enrichment, scraping, image and video generation)
behind one sign-in, each billed per call to a prepaid team balance.

The plugin runs treg's command-line tool, the PyPI package
``tools-registry``. OpenCompany installs a pinned copy with uv into its own
tool folder (``_install.py``) and runs it with one environment that keeps
its sign-in under OpenCompany's data folder (``_service.py``). The CLI owns
that sign-in: the Credentials modal relays the link and code ``treg login``
prints, and OpenCompany stores only the connected marker (``_handlers.py``).
OpenCompany never imports or ships treg's code.
"""

from __future__ import annotations

from services.ws_handler_registry import register_ws_handlers

from ._credentials import TregCredential
from ._handlers import WS_HANDLERS

register_ws_handlers(WS_HANDLERS)

__all__ = ["TregCredential", "WS_HANDLERS"]
