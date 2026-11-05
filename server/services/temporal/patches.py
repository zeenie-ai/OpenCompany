"""Patch ids this release ships dormant (RFC-0006 §18.9 recommendation 2).

A new patch id ships in one release listed here: that release's runs take
the old branch and record no marker, and the next release removes the id
from the set, so runs start taking the new branch. A rollback from that
release then replays, because the release before it already has the
branch. Every worker that runs workflows passes ``activate_patch`` as its
``patch_activation_callback``.
"""

from temporalio.worker import PatchActivationInput

#: Patch ids this release ships inactive. The next release removes them from the set.
DORMANT_PATCHES: frozenset[str] = frozenset({"controller-spawn-backoff-v1"})


def activate_patch(inp: PatchActivationInput) -> bool:
    """Consulted once per patch id in a run: at its first ``patched()`` call
    outside replay, when history holds no marker for it. Replay follows the
    recorded markers."""
    return inp.patch_id not in DORMANT_PATCHES


__all__ = ["DORMANT_PATCHES", "activate_patch"]
