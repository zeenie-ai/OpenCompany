"""Every media size constant, in one place.

These exist because the execution engine multiplies whatever a node
returns. One payload is copied at least six ways: ``_serialize_result``
-> ``node_outputs`` x3 -> WebSocket broadcast x2 -> the status cache
(retained for the process lifetime) -> the Temporal activity result ->
``MachinaWorkflow``'s aggregate -> every downstream activity input ->
and, for a ``usable_as_tool`` node, verbatim into an LLM message.

Temporal's blob limits are the binding constraint. They are server
defaults; nothing in this repo configures a ``DataConverter`` or a
payload codec, so they apply as-is:

    warning   524_288 B   -> PayloadSizeWarning (not filtered, so visible)
    error   2_097_152 B   -> _PayloadSizeError, and the activity RETRIES
                             three times before reporting a generic failure

That is why :class:`services.media.AudioRef` carries no bytes at all.
"""

from __future__ import annotations

# Temporal's own thresholds, restated so call sites can reason about
# headroom without importing temporalio.
TEMPORAL_PAYLOAD_WARN_BYTES = 524_288
TEMPORAL_PAYLOAD_ERROR_BYTES = 2_097_152

# Largest upload accepted by the workspace upload route. Deliberately far
# above the Temporal limit: uploaded bytes land on disk and only a ~400 B
# reference ever enters a payload.
MEDIA_MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Largest photo an employee may show. Every avatar of theirs (the team
# list, the chat header, beside each reply) loads this one file.
EMPLOYEE_PHOTO_MAX_BYTES = 5 * 1024 * 1024

# Largest file a node will pull back off disk into memory to hand to a
# provider. Bounds one activity's peak RSS; the bytes are discarded as
# soon as the HTTP call returns.
MEDIA_MAX_READ_BYTES = 25 * 1024 * 1024

# Longest clip accepted when the duration is actually known. Unknown
# duration falls through to the byte cap rather than failing -- see
# services.media.inspect for why inspection must never be load-bearing.
MEDIA_MAX_AUDIO_SECONDS = 60 * 60

__all__ = [
    "MEDIA_MAX_AUDIO_SECONDS",
    "MEDIA_MAX_READ_BYTES",
    "MEDIA_MAX_UPLOAD_BYTES",
    "TEMPORAL_PAYLOAD_ERROR_BYTES",
    "TEMPORAL_PAYLOAD_WARN_BYTES",
]
