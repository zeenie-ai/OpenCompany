"""Everything that touches boto3 / botocore, imported lazily.

The node never imports the SDK at module level: the plugin must register (and
the credentials modal must answer) even when boto3 is missing, and the import
costs startup time the rest of the server should not pay. Every function here
that needs the SDK imports it inside its body.

Three jobs:

* **Introspection** (no credentials, no network): :func:`service_names` and
  :func:`operations` read botocore's bundled service models, so
  ``list_operations`` and the panel dropdowns always match the installed SDK.
* **Calls**: :func:`call`, :func:`collect` and :func:`download_object` run a
  client method in a worker thread (the SDK is synchronous) with the stored
  key pair passed explicitly, bounded timeouts and the SDK's standard retry
  mode. Clients come from one shared botocore session, which caches models.
* **Shaping**: :func:`to_plain` turns a response into JSON-ready data the way
  the AWS CLI prints it (timestamps as ISO 8601, blobs as base64), and
  :func:`raise_user_error` maps botocore's errors onto ``NodeUserError``.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import difflib
import functools
import itertools
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, NoReturn, Tuple

from core.logging import get_logger
from services.plugin import NodeUserError

from ._credentials import ACCESS_KEY_ID_FIELD

logger = get_logger(__name__)

_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 60
# The SDK's "standard" retry mode: at most this many attempts in total, and
# only for throttling, transient 5xx and connection errors.
_MAX_ATTEMPTS = 3
# A streaming response body (a Lambda invoke Payload, ...) is read inline up
# to this size; S3 objects go through s3_download instead.
_MAX_INLINE_BODY_BYTES = 1024 * 1024
_MAX_SUGGESTIONS = 5
_SUMMARY_CHARS = 200
_TAG_RE = re.compile(r"<[^>]+>")
_MB = 1024 * 1024

# Error codes that get a pointed hint: the key pair itself is wrong, the IAM
# identity lacks the permission, or AWS throttled the call past the retries.
_AUTH_CODES = frozenset(
    {
        "AuthFailure",
        "ExpiredToken",
        "IncompleteSignature",
        "InvalidAccessKeyId",
        "InvalidClientTokenId",
        "SignatureDoesNotMatch",
        "UnrecognizedClientException",
    }
)
_DENIED_CODES = frozenset({"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"})
_THROTTLE_CODES = frozenset(
    {"RequestLimitExceeded", "SlowDown", "Throttling", "ThrottlingException", "TooManyRequestsException"}
)


def _require_sdk() -> None:
    try:
        import boto3  # noqa: F401
    except ImportError as exc:
        raise NodeUserError("The 'boto3' package is not installed. Run `uv sync` in server/.") from exc


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperationInfo:
    service: str  # "ec2"
    operation: str  # boto3 method name, "describe_instances"
    api_name: str  # AWS API name, "DescribeInstances"
    required: Tuple[str, ...]
    params: Tuple[str, ...]
    streaming_output: bool
    summary: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "api_name": self.api_name,
            "required": list(self.required),
            "params": list(self.params),
            "streaming_output": self.streaming_output,
            "summary": self.summary,
        }


@functools.lru_cache(maxsize=1)
def _botocore_session() -> Any:
    _require_sdk()
    import botocore.session

    return botocore.session.get_session()


def service_names() -> List[str]:
    """Every service the installed SDK knows (``ec2``, ``s3``, ``lambda``, ...)."""
    return sorted(_botocore_session().get_available_services())


def _summary(documentation: str) -> str:
    """The first sentence of an operation's HTML documentation, as plain text."""
    text = " ".join(_TAG_RE.sub(" ", documentation or "").split())
    first = text.split(". ")[0]
    return first if len(first) <= _SUMMARY_CHARS else first[: _SUMMARY_CHARS - 3] + "..."


def _known_service(service: str) -> str:
    names = service_names()
    key = service.strip().lower()
    if key in names:
        return key
    suggestions = difflib.get_close_matches(key, names, n=_MAX_SUGGESTIONS, cutoff=0.6)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    raise NodeUserError(
        f"Unknown AWS service '{service.strip()}'.{hint} "
        "Use operation=list_operations without a service to list them."
    )


@functools.lru_cache(maxsize=None)
def _operations(service: str) -> Tuple[OperationInfo, ...]:
    from botocore import xform_name

    model = _botocore_session().get_service_model(service)
    infos = []
    for api_name in model.operation_names:
        op = model.operation_model(api_name)
        shape = op.input_shape
        infos.append(
            OperationInfo(
                service=service,
                operation=xform_name(api_name),
                api_name=api_name,
                required=tuple(shape.required_members) if shape is not None else (),
                params=tuple(shape.members) if shape is not None else (),
                streaming_output=op.has_streaming_output,
                summary=_summary(op.documentation),
            )
        )
    return tuple(sorted(infos, key=lambda info: info.operation))


def operations(service: str) -> List[OperationInfo]:
    """The operations of one service, with their parameter names."""
    return list(_operations(_known_service(service)))


def resolve_operation(service: str, operation: str) -> OperationInfo:
    """Map ``describe_vpcs`` or ``DescribeVpcs`` to the service's operation,
    failing before any network call with the closest names."""
    from botocore import xform_name

    infos = operations(service)
    key = xform_name(operation.strip())
    for info in infos:
        if info.operation == key:
            return info
    names = [info.operation for info in infos]
    suggestions = difflib.get_close_matches(key, names, n=_MAX_SUGGESTIONS, cutoff=0.5)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    service_name = infos[0].service if infos else service.strip()
    raise NodeUserError(
        f"Unknown {service_name} operation '{operation.strip()}'.{hint} "
        f"Use operation=list_operations with service={service_name} to list them."
    )


# ---------------------------------------------------------------------------
# Response + error shaping
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _stream_types() -> Tuple[type, type]:
    from botocore.eventstream import EventStream
    from botocore.response import StreamingBody

    return StreamingBody, EventStream


def _read_body(body: Any) -> str:
    try:
        payload = body.read(_MAX_INLINE_BODY_BYTES + 1)
    finally:
        body.close()
    if len(payload) > _MAX_INLINE_BODY_BYTES:
        raise NodeUserError(
            "The response body is larger than 1 MB. For S3 objects use operation=s3_download, "
            "which saves the object to the workspace."
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return base64.b64encode(payload).decode("ascii")


def to_plain(value: Any) -> Any:
    """JSON-ready data, printed the way the AWS CLI prints it: timestamps as
    ISO 8601, blobs as base64, a streaming body read inline (up to 1 MB)."""
    streaming_body, event_stream = _stream_types()
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, streaming_body):
        return _read_body(value)
    if isinstance(value, event_stream):
        raise NodeUserError("This operation answers with an event stream, which the node cannot return.")
    return value


def raise_user_error(exc: Exception, label: str, region: str) -> NoReturn:
    """Translate a botocore ``ClientError`` / ``BotoCoreError`` into a
    ``NodeUserError`` carrying AWS's own code and message."""
    from botocore.exceptions import (
        ClientError,
        ConnectTimeoutError,
        EndpointConnectionError,
        ParamValidationError,
        ReadTimeoutError,
    )

    if isinstance(exc, ClientError):
        error = exc.response.get("Error") or {}
        code = error.get("Code") or "Unknown"
        message = f"AWS {label} failed ({code}): {error.get('Message') or exc}"
        if code in _AUTH_CODES:
            message += ". Check the key pair in Credentials -> AWS."
        elif code in _DENIED_CODES:
            message += ". The IAM identity behind the key lacks this permission."
        elif code in _THROTTLE_CODES:
            message += ". AWS throttled the request after retries; try again shortly."
        request_id = (exc.response.get("ResponseMetadata") or {}).get("RequestId")
        if request_id:
            message += f" [request_id={request_id}]"
    elif isinstance(exc, ParamValidationError):
        message = f"AWS {label} rejected the request: {exc}"
    elif isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        message = f"Could not reach AWS for {label} in region {region}: {exc}"
    else:
        message = f"AWS {label} failed: {exc}"
    raise NodeUserError(message) from exc


# ---------------------------------------------------------------------------
# Sessions + calls
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyPair:
    """The stored IAM access key pair, handed to every client explicitly."""

    access_key_id: str
    secret_access_key: str = field(repr=False)


def key_pair(secrets: Dict[str, Any]) -> KeyPair:
    """The key pair from ``AwsCredential.resolve()``. Explicit keys outrank
    every ambient source (environment, ``~/.aws``, an instance profile), so
    the server's own AWS identity never answers for a node."""
    return KeyPair(access_key_id=secrets[ACCESS_KEY_ID_FIELD], secret_access_key=secrets["api_key"])


# One botocore session caches the service models and client classes for every
# call (a client from a warm session costs ~10 ms against ~110 ms from a fresh
# one). It holds no credentials: each client gets its key pair explicitly.
# botocore sessions are not thread-safe, so client creation is serialised;
# the API calls themselves run concurrently (clients are thread-safe).
_CLIENT_LOCK = threading.Lock()


def _client(keys: KeyPair, service: str, region: str) -> Any:
    from botocore.config import Config

    with _CLIENT_LOCK:
        return _botocore_session().create_client(
            service,
            region_name=region,
            aws_access_key_id=keys.access_key_id,
            aws_secret_access_key=keys.secret_access_key,
            config=Config(
                connect_timeout=_CONNECT_TIMEOUT,
                read_timeout=_READ_TIMEOUT,
                retries={"mode": "standard", "max_attempts": _MAX_ATTEMPTS},
                user_agent_extra="OpenCompany",
            ),
        )


async def call(keys: KeyPair, service: str, method: str, request: Dict[str, Any], region: str) -> Dict[str, Any]:
    """``client.<method>(**request)`` in a worker thread, returned as plain data
    without the SDK's ``ResponseMetadata``."""
    from botocore.exceptions import BotoCoreError, ClientError

    def run() -> Dict[str, Any]:
        response = getattr(_client(keys, service, region), method)(**request)
        response.pop("ResponseMetadata", None)
        return to_plain(response)

    try:
        return await asyncio.to_thread(run)
    except (ClientError, BotoCoreError) as exc:
        raise_user_error(exc, f"{service}.{method}", region)


async def collect(
    keys: KeyPair,
    service: str,
    method: str,
    request: Dict[str, Any],
    region: str,
    expression: str,
    limit: int,
) -> Tuple[List[Any], bool]:
    """Items of a paginated call, projected by a JMESPath ``expression`` (the
    AWS CLI's ``--query`` language), stopping after ``limit`` items. Returns
    ``(items, truncated)``."""
    from botocore.exceptions import BotoCoreError, ClientError

    def run() -> Tuple[List[Any], bool]:
        pages = _client(keys, service, region).get_paginator(method).paginate(**request)
        found = (item for item in pages.search(expression) if item is not None)
        items = list(itertools.islice(found, limit + 1))
        return to_plain(items[:limit]), len(items) > limit

    try:
        return await asyncio.to_thread(run)
    except (ClientError, BotoCoreError) as exc:
        raise_user_error(exc, f"{service}.{method}", region)


async def download_object(keys: KeyPair, bucket: str, key: str, region: str, max_bytes: int) -> Tuple[bytes, Any]:
    """One S3 object's bytes and content type, refused past ``max_bytes``."""
    from botocore.exceptions import BotoCoreError, ClientError

    def run() -> Tuple[bytes, Any]:
        response = _client(keys, "s3", region).get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        try:
            size = response.get("ContentLength") or 0
            payload = b"" if size > max_bytes else body.read(max_bytes + 1)
        finally:
            body.close()
        if size > max_bytes or len(payload) > max_bytes:
            raise NodeUserError(
                f"s3://{bucket}/{key} is {max(size, len(payload)) // _MB} MB; "
                f"the download limit is {max_bytes // _MB} MB."
            )
        return payload, response.get("ContentType")

    try:
        return await asyncio.to_thread(run)
    except (ClientError, BotoCoreError) as exc:
        raise_user_error(exc, "s3.get_object", region)


# ---------------------------------------------------------------------------
# Dropdown loaders
# ---------------------------------------------------------------------------


async def load_aws_services(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``loadOptionsMethod`` for the ``service`` dropdown. A missing SDK must
    never break the parameter panel, so it degrades to no options."""
    try:
        names = await asyncio.to_thread(service_names)
    except NodeUserError as exc:
        logger.warning("aws service loader failed", error=str(exc))
        return []
    return [{"value": name, "label": name} for name in names]


async def load_aws_operations(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``loadOptionsMethod`` for the ``api_operation`` dropdown (depends on
    ``service``)."""
    service = str((params or {}).get("service") or "").strip()
    if not service:
        return []
    try:
        infos = await asyncio.to_thread(operations, service)
    except NodeUserError as exc:
        logger.warning("aws operation loader failed", service=service, error=str(exc))
        return []
    return [{"value": info.operation, "label": info.operation, "description": info.summary} for info in infos]


__all__ = [
    "KeyPair",
    "OperationInfo",
    "call",
    "collect",
    "download_object",
    "load_aws_operations",
    "load_aws_services",
    "operations",
    "raise_user_error",
    "resolve_operation",
    "service_names",
    "key_pair",
    "to_plain",
]
