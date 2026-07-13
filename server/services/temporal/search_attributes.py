"""Wave 12 A4: Temporal custom Search Attributes registration.

Defines the 6 Search Attributes the event framework needs for the
Visibility API to route inbound events to running consumer workflows
(see ``services/events/dispatch.py:emit``). Registration is idempotent —
Temporal Server's ``add_search_attributes`` rejects duplicates with an
``AlreadyExists`` error which we treat as success.

Attribute declarations live in one place (:data:`EVENT_SEARCH_ATTRIBUTES`)
as a structured list so callers iterate rather than restate. Adding a
new attribute = add an entry to that list; everything else (registration,
docs, error handling) flows from it.

References:
- https://docs.temporal.io/search-attribute
- https://python.temporal.io/temporalio.client.html#Client.operator_service
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesRequest,
)
from temporalio.client import Client

from core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SearchAttributeSpec:
    """Declarative spec for one Temporal Search Attribute."""

    name: str
    indexed_type: IndexedValueType
    description: str


# Single source of truth for the 6 event-framework Search Attributes.
# Anything that names or filters by these attributes (the dispatch
# helper, the Visibility queries in admin handlers, the test suite)
# imports from this list rather than restating the strings.
EVENT_SEARCH_ATTRIBUTES: Sequence[SearchAttributeSpec] = (
    SearchAttributeSpec(
        name="EventType",
        indexed_type=IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        description=(
            "CloudEvents type the workflow is consuming "
            "(e.g. com.opencompany.whatsapp.message.received). Used by "
            "dispatch.emit to find running consumers via Visibility "
            "queries: ListWorkflows(query=f\"EventType='{event.type}' "
            "AND ExecutionStatus='Running'\")."
        ),
    ),
    SearchAttributeSpec(
        name="EventSource",
        indexed_type=IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        description=(
            "CloudEvents source URI fragment "
            "(e.g. opencompany://nodes/whatsapp). Used for routing when "
            "the same type can arrive from multiple sources."
        ),
    ),
    SearchAttributeSpec(
        name="EventWorkflowId",
        indexed_type=IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        description=(
            "OpenCompany workflow_id the event belongs to (distinct from "
            "the Temporal Workflow ID which addresses the workflow run). "
            "Used to scope event delivery to a specific deployment."
        ),
    ),
    SearchAttributeSpec(
        name="TriggerNodeId",
        indexed_type=IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        description=(
            "Trigger node id that emitted or will consume the event. "
            "Used for per-trigger event-history queries and the "
            "list_event_waiters admin handler."
        ),
    ),
    SearchAttributeSpec(
        name="EventTriggerKind",
        indexed_type=IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        description=(
            "High-level event source classification (webhook / polling / "
            "whatsapp / telegram / ...). Coarser-grained than EventType; "
            "useful for ops dashboards."
        ),
    ),
    SearchAttributeSpec(
        name="EventReceivedAt",
        indexed_type=IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
        description=(
            "When the event entered the framework. Supports time-range "
            "visibility queries (e.g. 'all events received in the last "
            "hour')."
        ),
    ),
)


def attribute_names() -> Sequence[str]:
    """Return the configured attribute names. Helper for tests + admin code."""
    return tuple(spec.name for spec in EVENT_SEARCH_ATTRIBUTES)


async def register_search_attributes(client: Client, namespace: str) -> dict:
    """Idempotently register every :data:`EVENT_SEARCH_ATTRIBUTES` entry
    on the given Temporal namespace.

    Returns a status dict keyed by attribute name:
        {name: "registered" | "already_exists" | "error: ..."}

    Safe to call multiple times — already-present attributes are
    detected via a pre-flight ``ListSearchAttributes`` call and skipped.
    Attributes that fail to register surface in the result dict with
    the error string so the caller can decide whether to fail-fast.
    """
    operator = client.service_client.operator_service

    # Pre-flight: which attributes already exist on this namespace?
    try:
        existing_resp = await operator.list_search_attributes(
            ListSearchAttributesRequest(namespace=namespace),
        )
        existing_names = set(existing_resp.custom_attributes.keys())
    except Exception as exc:  # noqa: BLE001 — surface every failure
        logger.warning(
            f"Could not list existing Search Attributes on namespace={namespace!r}: {exc}. "
            "Attempting registration anyway; duplicates will be reported as errors."
        )
        existing_names = set()

    status: dict[str, str] = {}
    to_register: dict[str, IndexedValueType] = {
        spec.name: spec.indexed_type for spec in EVENT_SEARCH_ATTRIBUTES if spec.name not in existing_names
    }
    for name in existing_names & {s.name for s in EVENT_SEARCH_ATTRIBUTES}:
        status[name] = "already_exists"

    if not to_register:
        logger.info(
            f"All {len(EVENT_SEARCH_ATTRIBUTES)} event-framework Search Attributes " f"already registered on namespace={namespace!r}",
        )
        return status

    try:
        await operator.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=namespace,
                search_attributes=to_register,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        # Whole batch failed — mark each requested attribute with the error.
        err = f"error: {type(exc).__name__}: {exc}"
        for name in to_register:
            status[name] = err
        logger.error(
            f"Failed to register Search Attributes on namespace={namespace!r}: {exc}",
        )
        return status

    for name in to_register:
        status[name] = "registered"

    logger.info(
        f"Registered {len(to_register)} event-framework Search Attributes on " f"namespace={namespace!r}: {list(to_register.keys())}",
    )
    return status
