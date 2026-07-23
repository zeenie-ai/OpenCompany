"""Fixtures for the core-config test suite.

Isolated from tests/conftest.py (which stubs core.logging for LLM provider tests).
This suite needs the REAL core.config module.
"""

import sys
from pathlib import Path

# Make sure server/ is importable even when pytest is invoked from repo root.
SERVER_DIR = Path(__file__).resolve().parents[2]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

# The parent conftest at server/tests/conftest.py unconditionally stubs `core`
# and `core.logging` for the LLM provider tests.  We need the REAL modules.
# Wipe ANY core.* entries so subsequent imports load from disk.
for mod_name in [name for name in list(sys.modules) if name == "core" or name.startswith("core.")]:
    del sys.modules[mod_name]
