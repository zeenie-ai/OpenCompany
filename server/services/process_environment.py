"""Environment containment for ordinary application child processes."""

from __future__ import annotations

import os
from collections.abc import Mapping


def without_onepassword_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy the final environment without 1Password bootstrap/auth variables.

    Call after overrides and additions, at the spawn boundary. A missing
    environment keeps ordinary ambient inheritance. The private credential
    resolver supplies its own separately restricted environment.
    """
    source = os.environ if env is None else env
    return {key: value for key, value in source.items() if not key.upper().startswith("OP_")}
