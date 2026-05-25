"""Per-node plugin modules (Wave 11.C).

Each plugin lives in ONE file under the subpackage named after its
primary palette group. The structure mirrors
``server/nodes/groups.py`` — every group registered there has a
matching subdirectory here. Adding a new search node =
``server/nodes/search/my_search.py`` with a single class.

Auto-discovery is **recursive**: ``pkgutil.walk_packages`` imports
every ``.py`` under this tree at startup, running every
``register_node(...)`` or ``BaseNode`` subclass registration as a
side effect before FastAPI serves its first NodeSpec request.

Layout::

    server/nodes/
    ├── agent/           # AI Agents (aiAgent, chatAgent, specialized agents)
    ├── model/           # AI Models (openai, anthropic, gemini, …)
    ├── skill/           # AI Skills (masterSkill, simpleMemory)
    ├── tool/            # AI Tools (calculatorTool, currentTimeTool, …)
    ├── trigger/         # Workflow triggers (webhookTrigger, chatTrigger, …)
    ├── workflow/        # Workflow control (start, timer, cronScheduler)
    ├── search/          # Search APIs (braveSearch, serperSearch, perplexitySearch)
    ├── google/          # Google Workspace (gmail, calendar, drive, …)
    ├── android/         # Android service nodes
    ├── whatsapp/        # WhatsApp integration
    ├── social/          # Unified social messaging (socialSend, socialReceive)
    ├── code/            # Code executors (python, javascript, typescript)
    ├── utility/         # HTTP, webhooks, console, proxy, process manager
    ├── browser/         # Browser automation
    ├── scraper/         # Web scraping (crawlee, apify, httpScraper)
    ├── filesystem/      # File I/O + shell + process
    ├── document/        # Document parsing / chunking / embedding
    ├── location/        # Google Maps nodes
    ├── email/           # IMAP/SMTP (emailSend, emailReceive, emailRead)
    ├── telegram/        # Telegram bot
    ├── twitter/         # Twitter/X
    ├── proxy/           # Proxy providers
    ├── chat/            # Chat send / history
    ├── scheduler/       # Schedulers
    ├── text/            # Text generation
    ├── groups.py        # Palette group metadata (label / icon / color)
    └── __init__.py      # This file — recursive discovery.

Legacy bulk-registration files (``agents.py``, ``services.py``,
``tools.py``, ``triggers.py``, ``utilities.py``) stay at the root
during 11.C — they hold metadata-only entries for types not yet
migrated to their subpackage. Each file shrinks as its nodes move
into their proper folder; gone entirely by end of 11.D.

Canonical plugin file shape::

    # server/nodes/search/my_search.py
    from pydantic import BaseModel, Field
    from services.plugin import ActionNode, ApiKeyCredential, Operation

    class MyCredential(ApiKeyCredential):
        id = "my_service"
        ...

    class MyParams(BaseModel):
        query: str

    class MyOutput(BaseModel):
        results: list = []

    class MyNode(ActionNode):
        type = "mySearch"
        display_name = "My Search"
        group = ("search", "tool")
        credentials = (MyCredential,)
        Params = MyParams
        Output = MyOutput

        @Operation("search")
        async def search(self, ctx, params): ...

Zero edits required elsewhere. The node appears in the editor at the
next backend restart, categorised by the primary ``group`` entry.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

logger = logging.getLogger(__name__)


def _discover() -> list[str]:
    """Recursively import every ``.py`` under this package so all
    ``register_node(...)`` + ``BaseNode`` subclass side effects run
    before anyone asks for a NodeSpec. Private modules (``_foo.py``,
    ``_subpkg/``) are skipped.
    """
    imported: list[str] = []
    for module_info in pkgutil.walk_packages(__path__, prefix=f"{__name__}."):
        # Skip private modules and private subpackages anywhere in the path.
        parts = module_info.name.split(".")
        if any(part.startswith("_") for part in parts[len(__name__.split(".")) :]):
            continue
        try:
            importlib.import_module(module_info.name)
            # Short name for logging: drop the package prefix.
            imported.append(module_info.name.removeprefix(f"{__name__}."))
        except Exception:
            logger.exception("Failed to import node plugin %s", module_info.name)
    return imported


_DISCOVERED = _discover()
logger.info("node plugins loaded: %d modules", len(_DISCOVERED))
