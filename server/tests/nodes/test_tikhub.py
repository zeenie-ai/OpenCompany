"""Contract tests for the TikHub scraper node (``tikhubAction``).

The node is the "flattened CLI" over the official ``tikhub`` SDK: one
``call`` operation addressed by the SDK's own ``resource.method`` id, plus
``list_endpoints`` (pure introspection, no network), ``describe`` (TikHub's
published documentation for one endpoint), ``fetch_url`` (hybrid parser) and
``account``. Everything that touches the SDK lives in
``nodes/scraper/tikhub_action/_sdk.py`` and is imported lazily, so the
plugin registers without the SDK present. A large payload is saved to the
workspace and returned as a file reference with a preview (``_result.py``).

The SDK speaks httpx against ``https://api.tikhub.io``, so ``respx`` sees
every request the node makes; nothing here touches the network. The
endpoint index is built by reflecting over the installed SDK, so the
introspection tests exercise the real package — SDK drift (a renamed
resource, a platform that vanished) fails here rather than in a workflow.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import httpx
import pytest
import respx

from tests.nodes._mocks import patched_container, patched_pricing

pytestmark = pytest.mark.node_contract

import nodes.scraper.tikhub_action as tikhub_mod  # noqa: E402
from nodes.scraper._credentials import TikHubCredential  # noqa: E402
from nodes.scraper.tikhub_action import _docs, _result, _sdk  # noqa: E402
from services.node_registry import get_node_class  # noqa: E402
from services.plugin.base import NodeUserError  # noqa: E402
from services.plugin.credential import CREDENTIAL_REGISTRY, ProbeResult  # noqa: E402
from services.ws_handler_registry import get_option_loader  # noqa: E402

BASE = "https://api.tikhub.io"
FETCH_ONE_VIDEO = f"{BASE}/api/v1/douyin/web/fetch_one_video"
HYBRID_VIDEO_DATA = f"{BASE}/api/v1/hybrid/video_data"
USER_INFO = f"{BASE}/api/v1/tikhub/user/get_user_info"
USER_DAILY_USAGE = f"{BASE}/api/v1/tikhub/user/get_user_daily_usage"
SPEC_URL = _docs.SPEC_URL

CALL_ONE_VIDEO = {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}}

# The shape of the body TikHub sends with a 400: its English message, a
# Chinese copy, and links a model has no use for.
TIKHUB_400 = {
    "detail": {
        "code": 400,
        "request_id": "00000000-0000-4000-8000-000000000000",
        "message": (
            "Request failed. Please retry. Check the docs below to verify your parameters, test with the "
            "default demo parameters, and send the response JSON to support. You won't be charged for this request."
        ),
        "message_zh": "请求失败，请重试。",
        "docs": "https://api.tikhub.io/#/Douyin-Web-API/fetch_one_video_api_v1_douyin_web_fetch_one_video_get",
        "support": "Discord: https://discord.gg/aMEAS8Xsvz",
        "router": "/api/v1/douyin/web/fetch_one_video",
        "params": {"aweme_id": "1"},
    }
}

# One route of TikHub's OpenAPI document, shaped as the live one is: the
# description twice (Chinese, then English), parameter docs under ``schema``.
ONE_VIDEO_SPEC = {
    "openapi": "3.1.0",
    "paths": {
        "/api/v1/douyin/web/fetch_one_video": {
            "get": {
                "summary": "获取单个作品数据/Get single video data",
                "description": (
                    "# [中文]\n### 用途:\n- 获取单个作品数据\n\n"
                    "# [English]\n### Purpose:\n- Get single video data\n### Price:\n- 0.001 USD/request"
                ),
                "parameters": [
                    {
                        "name": "aweme_id",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string", "description": "作品id/Video id", "title": "Aweme Id"},
                    },
                    {
                        "name": "need_anchor_info",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "boolean", "description": "Anchor info", "default": False},
                    },
                ],
            }
        }
    },
}

PLUGIN_DIR = Path(tikhub_mod.__file__).parent
SCRAPER_DIR = PLUGIN_DIR.parent
SERVER_DIR = PLUGIN_DIR.parents[2]
SKILL_MD = SERVER_DIR / "skills" / "web_agent" / "tikhub-skill" / "SKILL.md"

KEYS = {"tikhub": "tk"}


def _ok(data, router="/api/v1/douyin/web/fetch_one_video", params=None):
    """A TikHub success envelope as the API returns it."""
    return {"code": 200, "router": router, "params": params or {}, "data": data}


def _big(rows: int = 12) -> dict:
    """A page of ``rows`` items a little over the inline limit."""
    caption = "c" * (_result.INLINE_LIMIT_CHARS // 10)
    return {"items": [{"id": i, "caption": caption, "gone": None} for i in range(rows)], "cursor": "next-1"}


def _reset_cache() -> None:
    _sdk._INDEX = None
    _docs._DOCS = None
    for name in ("_BY_ENDPOINT", "_BY_PATH"):
        cache = getattr(_sdk, name, None)
        if isinstance(cache, dict):
            cache.clear()
        elif cache is not None:
            setattr(_sdk, name, None)


@pytest.fixture(autouse=True)
def reset_index():
    """The index is a module-level cache; every test starts cold so the
    SDK-missing degrade test cannot be masked by a warm cache."""
    _reset_cache()
    yield
    _reset_cache()


@pytest.fixture(autouse=True)
def no_retries(monkeypatch):
    """The SDK retries 401/403/429/5xx with anyio backoff sleeps; the
    error-mapping tests only need the first response."""
    monkeypatch.setattr(_sdk, "_MAX_RETRIES", 0)


async def _execute(harness, params, *, keys=KEYS, total_cost=0.001, **kwargs):
    with patched_container(auth_api_keys=keys), patched_pricing(total_cost=total_cost):
        return await harness.execute("tikhubAction", params, **kwargs)


# ============================================================================
# 1. Registration
# ============================================================================


class TestRegistration:
    def test_class_attributes(self):
        cls = get_node_class("tikhubAction")
        assert cls is not None, "tikhubAction is not registered"
        assert cls.type == "tikhubAction"
        assert cls.tool_name == "tikhub_action"
        assert tuple(cls.group) == ("scraper", "tool")
        assert cls.usable_as_tool is True
        assert TikHubCredential in tuple(cls.credentials)

    def test_both_canvas_handles_stay_visible(self):
        # usable_as_tool=True auto-hides both handles unless the class
        # declares them; the node must stay wirable on the canvas.
        cls = get_node_class("tikhubAction")
        assert cls.hide_input_handle is False
        assert cls.hide_output_handle is False
        assert "hide_input_handle" in cls.__dict__
        assert "hide_output_handle" in cls.__dict__

    def test_option_loader_and_credential_registered(self):
        assert get_option_loader("tikhubEndpoints") is not None
        assert CREDENTIAL_REGISTRY["tikhub"] is TikHubCredential

    def test_tool_schema_is_flat(self):
        # Dual-purpose ActionNode: ``AIService._get_tool_schema`` builds the
        # LLM schema as ``inline_schema_refs(Params.model_json_schema())``.
        from services.plugin.tool import inline_schema_refs

        cls = get_node_class("tikhubAction")
        schema = inline_schema_refs(cls.Params.model_json_schema())
        dumped = json.dumps(schema)
        assert "$defs" not in dumped
        assert "definitions" not in schema
        assert '"$ref"' not in dumped
        assert set(schema["properties"]) >= {"operation", "platform", "endpoint", "params", "search", "limit", "url", "minimal"}

    def test_params_defaults(self):
        cls = get_node_class("tikhubAction")
        params = cls.Params()
        assert params.operation == "call"
        assert params.platform == "all"
        assert params.endpoint == ""
        assert params.params == {}
        assert params.minimal is False
        assert set(get_args(cls.Params.model_fields["operation"].annotation)) == {
            "call",
            "list_endpoints",
            "describe",
            "fetch_url",
            "account",
        }

    def test_reserved_field_names_absent(self):
        # ParameterRenderer keys magic on these literal names.
        cls = get_node_class("tikhubAction")
        assert not {"model", "api_key", "parameters", "action"} & set(cls.Params.model_fields)


# ============================================================================
# 2-4. call
# ============================================================================


class TestCall:
    @respx.mock
    async def test_happy_path(self, harness):
        route = respx.get(FETCH_ONE_VIDEO).mock(
            return_value=httpx.Response(200, json=_ok({"aweme_id": "7123", "desc": "hello"}))
        )

        result = await _execute(
            harness,
            {
                "operation": "call",
                "endpoint": "douyin_web.fetch_one_video",
                "params": {"aweme_id": "7123"},
            },
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["operation", "endpoint", "path", "data", "code", "router", "cost_usd"])
        payload = result["result"]
        assert payload["operation"] == "call"
        assert payload["endpoint"] == "douyin_web.fetch_one_video"
        assert payload["path"] == "/api/v1/douyin/web/fetch_one_video"
        assert payload["data"] == {"aweme_id": "7123", "desc": "hello"}
        assert payload["code"] == 200
        assert payload["router"] == "/api/v1/douyin/web/fetch_one_video"
        assert payload["cost_usd"] == 0.001

        assert route.call_count == 1
        sent = respx.calls.last.request
        assert sent.method == "GET"
        assert sent.headers["Authorization"] == "Bearer tk"
        assert sent.url.params["aweme_id"] == "7123"
        # optional kwargs left at None are dropped, not sent as "None"
        assert "need_anchor_info" not in sent.url.params

    async def test_dispatch_reads_the_operation_key(self, harness):
        # ``BaseNode._pick_operation`` reads the raw ``operation`` key for
        # every multi-op node; the Params default ("call") is what the panel
        # and the tool schema advertise, not what dispatch falls back to.
        with respx.mock:
            result = await _execute(harness, {"endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}})
            assert respx.calls.call_count == 0

        harness.assert_envelope(result, success=False)
        assert "operation" in result["error"].lower()

    @respx.mock
    async def test_params_as_json_string(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok({"x": 1})))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": '{"aweme_id": "999"}'},
        )

        harness.assert_envelope(result, success=True)
        assert respx.calls.last.request.url.params["aweme_id"] == "999"

    @respx.mock
    async def test_params_blank_string_from_panel(self, harness):
        respx.get(USER_INFO).mock(
            return_value=httpx.Response(200, json=_ok({"email": "a@b.c"}, router="/api/v1/tikhub/user/get_user_info"))
        )

        result = await _execute(harness, {"operation": "call", "endpoint": "tikhub_user.get_user_info", "params": ""})

        harness.assert_envelope(result, success=True)
        assert result["result"]["data"] == {"email": "a@b.c"}
        assert respx.calls.call_count == 1

    @respx.mock
    async def test_endpoint_as_rest_path_alias(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok({"x": 1})))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "/api/v1/douyin/web/fetch_one_video", "params": {"aweme_id": "1"}},
        )

        harness.assert_envelope(result, success=True)
        assert result["result"]["endpoint"] == "douyin_web.fetch_one_video"
        assert respx.calls.call_count == 1

    @respx.mock
    async def test_endpoint_as_slash_alias(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok({"x": 1})))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web/fetch_one_video", "params": {"aweme_id": "1"}},
        )

        harness.assert_envelope(result, success=True)
        assert result["result"]["endpoint"] == "douyin_web.fetch_one_video"

    @respx.mock
    async def test_empty_data_is_kept(self, harness):
        # An empty answer from TikHub is a real answer, not a missing key.
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok({})))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}},
        )

        harness.assert_envelope(result, success=True)
        assert "data" in result["result"]
        assert result["result"]["data"] == {}


# ============================================================================
# 4b. large results: saved to the workspace, returned as a file and a preview
# ============================================================================


class TestLargeResults:
    @respx.mock
    async def test_large_result_is_saved_and_previewed(self, harness, tmp_path):
        data = _big()
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok(data)))

        result = await _execute(harness, CALL_ONE_VIDEO, context=harness.build_context(workspace_dir=str(tmp_path)))

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(
            result, ["operation", "endpoint", "path", "file", "preview", "note", "code", "router", "cost_usd"]
        )
        payload = result["result"]
        assert "data" not in payload

        ref = payload["file"]
        assert ref["kind"] == "file"
        assert ref["path"].startswith(f"{_result.RESULT_SUBDIR}/")
        assert ref["path"].endswith(".json")
        assert ref["mime_type"] == "application/json"
        assert ref["path"] in payload["note"]

        saved = tmp_path / ref["path"]
        text = saved.read_text(encoding="utf-8")
        assert json.loads(text) == data
        assert ref["size_bytes"] == saved.stat().st_size
        # Indented, so tools that work by line can search and page through it.
        assert len(text.splitlines()) > len(data["items"])

        clipped = "c" * _result._PREVIEW_STRING_CHARS + "..."
        assert payload["preview"] == {"items": [{"id": 0, "caption": clipped}, "... 11 more"], "cursor": "next-1"}

    @respx.mock
    async def test_inline_limit_is_exact(self, harness, tmp_path):
        # A JSON string is its characters plus two quotes.
        at_limit = "x" * (_result.INLINE_LIMIT_CHARS - 2)
        respx.get(FETCH_ONE_VIDEO).mock(
            side_effect=[
                httpx.Response(200, json=_ok(at_limit)),
                httpx.Response(200, json=_ok(at_limit + "x")),
            ]
        )
        context = harness.build_context(workspace_dir=str(tmp_path))

        whole = await _execute(harness, CALL_ONE_VIDEO, context=context)
        assert whole["result"]["data"] == at_limit
        assert "file" not in whole["result"]
        assert not (tmp_path / _result.RESULT_SUBDIR).exists()

        saved = await _execute(harness, CALL_ONE_VIDEO, context=context)
        assert "data" not in saved["result"]
        assert (tmp_path / saved["result"]["file"]["path"]).is_file()

    @respx.mock
    async def test_each_large_result_gets_its_own_file(self, harness, tmp_path):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok(_big())))
        context = harness.build_context(workspace_dir=str(tmp_path))

        first = await _execute(harness, CALL_ONE_VIDEO, context=context)
        second = await _execute(harness, CALL_ONE_VIDEO, context=context)

        assert first["result"]["file"]["path"] != second["result"]["file"]["path"]

    @respx.mock
    async def test_fetch_url_saves_a_large_result_too(self, harness, tmp_path):
        data = _big()
        respx.get(HYBRID_VIDEO_DATA).mock(
            return_value=httpx.Response(200, json=_ok(data, router="/api/v1/hybrid/video_data"))
        )

        result = await _execute(
            harness,
            {"operation": "fetch_url", "url": "https://v.douyin.com/abc/"},
            context=harness.build_context(workspace_dir=str(tmp_path)),
        )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["url"] == "https://v.douyin.com/abc/"
        assert "data" not in payload
        assert json.loads((tmp_path / payload["file"]["path"]).read_text(encoding="utf-8")) == data

    @respx.mock
    async def test_usage_is_recorded_before_the_result_is_saved(self, harness):
        # No workspace in the context: saving fails, but TikHub has already
        # billed the request, so the metric must exist.
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok(_big())))

        result = await _execute(harness, CALL_ONE_VIDEO)

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "workspace" in result["error"].lower()
        harness.database.save_api_usage_metric.assert_awaited_once()


class TestPreview:
    def test_a_list_keeps_its_first_element_and_a_count_of_the_rest(self):
        assert _result.preview_of([{"a": 1}, {"a": 2}, {"a": 3}]) == [{"a": 1}, "... 2 more"]
        assert _result.preview_of([7]) == [7]
        assert _result.preview_of([]) == []

    def test_empty_values_are_left_out_but_zero_and_false_stay(self):
        value = {"none": None, "blank": "", "list": [], "dict": {}, "zero": 0, "no": False}
        assert _result.preview_of(value) == {"zero": 0, "no": False}

    def test_long_strings_are_clipped_at_every_depth(self):
        long = "u" * (_result._PREVIEW_STRING_CHARS + 1)
        clipped = "u" * _result._PREVIEW_STRING_CHARS + "..."
        assert _result.preview_of({"a": {"b": [{"url": long}, {"url": long}]}}) == {
            "a": {"b": [{"url": clipped}, "... 1 more"]}
        }
        exact = "u" * _result._PREVIEW_STRING_CHARS
        assert _result.preview_of(exact) == exact


# ============================================================================
# 5-9. errors
# ============================================================================


class TestErrors:
    @respx.mock
    async def test_unknown_endpoint_is_user_error_without_http(self, harness):
        result = await _execute(harness, {"operation": "call", "endpoint": "nope.nothing", "params": {}})

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "list_endpoints" in result["error"]
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_missing_endpoint_is_user_error_without_http(self, harness):
        result = await _execute(harness, {"operation": "call", "endpoint": ""})

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_unknown_kwarg_rejected_before_http(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok({"x": 1})))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1", "bogus": True}},
        )

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "bogus" in result["error"]
        # the message lists what the endpoint accepts
        assert "aweme_id" in result["error"]
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_missing_required_kwarg_rejected_before_http(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok({"x": 1})))

        result = await _execute(harness, {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {}})

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "aweme_id" in result["error"]
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_missing_key_is_permission_denied_envelope(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok({"x": 1})))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}},
            keys={},
        )

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "PermissionDeniedError"
        assert result["credential"]["provider"] == "tikhub"
        assert result["credential"]["reason"] == "missing"
        assert result["credential"]["remediation"] == "add_key"
        assert respx.calls.call_count == 0

    @pytest.mark.parametrize(
        ("status", "body", "headers", "needles"),
        [
            (401, {"detail": "invalid api key"}, {}, ("401",)),
            (402, {"detail": "insufficient balance"}, {}, ("402", "balance", "operation=account")),
            (403, {"detail": "missing route permission"}, {}, ("403", "permission")),
            (429, {"detail": "slow down"}, {"Retry-After": "7"}, ("429", "7")),
            (
                422,
                {"detail": [{"loc": ["query", "aweme_id"], "msg": "field required"}]},
                {},
                ("422", "query.aweme_id: field required", "operation=describe"),
            ),
            (502, {"detail": "upstream failed"}, {}, ("502",)),
        ],
    )
    @respx.mock
    async def test_http_errors_map_to_user_errors(self, harness, status, body, headers, needles):
        route = respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(status, json=body, headers=headers))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}},
        )

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError", result
        for needle in needles:
            assert needle in result["error"], result["error"]
        # _MAX_RETRIES = 0: exactly one attempt
        assert route.call_count == 1

    @respx.mock
    async def test_401_points_at_credentials(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(401, json={"detail": "bad"}))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}},
        )

        assert result["success"] is False
        assert "credential" in result["error"].lower()

    @respx.mock
    async def test_400_carries_tikhubs_words_without_blaming_the_arguments(self, harness):
        # TikHub documents 400 as a bad parameter OR a failure on its side and
        # sends this one body for both. The SDK cannot read the nested detail
        # and reports "HTTP 400".
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(400, json=TIKHUB_400))

        result = await _execute(harness, CALL_ONE_VIDEO)

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        error = result["error"]
        assert "(400)" in error
        assert "Request failed. Please retry" in error
        assert "failure on its side" in error
        assert "operation=describe" in error
        # No raw body: neither the Chinese copy nor the support links.
        assert "请求失败" not in error
        assert "discord" not in error.lower()
        assert "{" not in error

    @respx.mock
    async def test_403_does_not_send_the_user_after_the_balance(self, harness):
        # TikHub answers 402 for an empty balance; 403 is a permission problem.
        respx.get(FETCH_ONE_VIDEO).mock(
            return_value=httpx.Response(403, json={"detail": {"code": 403, "message": "Missing route permission."}})
        )

        result = await _execute(harness, CALL_ONE_VIDEO)

        assert result["success"] is False
        assert "Missing route permission" in result["error"]
        assert "balance" not in result["error"]

    @respx.mock
    async def test_in_body_error_code_on_http_200(self, harness):
        # The SDK ignores in-body codes; the node must not.
        respx.get(FETCH_ONE_VIDEO).mock(
            return_value=httpx.Response(
                200,
                json={"code": 400, "router": "/api/v1/douyin/web/fetch_one_video", "message": "aweme not found"},
            )
        )

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}},
        )

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert "aweme not found" in result["error"]


# ============================================================================
# 10. fetch_url + account
# ============================================================================


class TestFetchUrlAndAccount:
    @respx.mock
    async def test_fetch_url_uses_hybrid_parser(self, harness):
        route = respx.get(HYBRID_VIDEO_DATA).mock(
            return_value=httpx.Response(200, json=_ok({"id": "v1"}, router="/api/v1/hybrid/video_data"))
        )
        share_url = "https://www.tiktok.com/@someone/video/7123"

        result = await _execute(
            harness,
            {"operation": "fetch_url", "url": share_url, "minimal": True},
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["operation", "url", "data", "cost_usd"])
        payload = result["result"]
        assert payload["operation"] == "fetch_url"
        assert payload["url"] == share_url
        assert payload["data"] == {"id": "v1"}

        assert route.call_count == 1
        sent = respx.calls.last.request
        assert sent.headers["Authorization"] == "Bearer tk"
        assert sent.url.params["url"] == share_url
        assert sent.url.params["minimal"] == "true"
        assert "base64_url" not in sent.url.params

    @respx.mock
    async def test_fetch_url_default_minimal(self, harness):
        respx.get(HYBRID_VIDEO_DATA).mock(return_value=httpx.Response(200, json=_ok({}, router="/api/v1/hybrid/video_data")))

        result = await _execute(harness, {"operation": "fetch_url", "url": "https://v.douyin.com/abc/"})

        harness.assert_envelope(result, success=True)
        assert respx.calls.last.request.url.params["url"] == "https://v.douyin.com/abc/"

    @respx.mock
    async def test_fetch_url_requires_url(self, harness):
        result = await _execute(harness, {"operation": "fetch_url", "url": ""})

        harness.assert_envelope(result, success=False)
        assert result["error_type"] == "NodeUserError"
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_account_makes_two_gets(self, harness):
        info = respx.get(USER_INFO).mock(
            return_value=httpx.Response(
                200,
                json=_ok({"email": "a@b.c", "balance": 12.5}, router="/api/v1/tikhub/user/get_user_info"),
            )
        )
        usage = respx.get(USER_DAILY_USAGE).mock(
            return_value=httpx.Response(
                200,
                json=_ok({"requests": 42}, router="/api/v1/tikhub/user/get_user_daily_usage"),
            )
        )

        result = await _execute(harness, {"operation": "account"})

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["operation", "account", "usage"])
        payload = result["result"]
        assert payload["operation"] == "account"
        assert payload["account"] == {"email": "a@b.c", "balance": 12.5}
        assert payload["usage"] == {"requests": 42}
        assert info.call_count == 1
        assert usage.call_count == 1
        assert respx.calls.call_count == 2
        for call in respx.calls:
            assert call.request.headers["Authorization"] == "Bearer tk"


# ============================================================================
# 11. list_endpoints
# ============================================================================


class TestListEndpoints:
    async def test_platform_and_search_filter(self, harness):
        with respx.mock:
            result = await _execute(
                harness,
                {"operation": "list_endpoints", "platform": "douyin", "search": "fetch_one_video"},
            )
            assert respx.calls.call_count == 0

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["operation", "platform", "endpoints", "count", "total"])
        payload = result["result"]
        assert payload["operation"] == "list_endpoints"
        assert payload["platform"] == "douyin"
        endpoints = payload["endpoints"]
        assert endpoints
        assert payload["count"] == len(endpoints)
        assert payload["total"] >= payload["count"]
        assert all(e["endpoint"].startswith("douyin") for e in endpoints)

        by_id = {e["endpoint"]: e for e in endpoints}
        entry = by_id["douyin_web.fetch_one_video"]
        assert entry["resource"] == "douyin_web"
        assert entry["method"] == "fetch_one_video"
        assert entry["http_method"] == "GET"
        assert entry["path"] == "/api/v1/douyin/web/fetch_one_video"
        assert entry["summary"]
        params = {p["name"]: p for p in entry["params"]}
        assert params["aweme_id"]["required"] is True
        assert params["aweme_id"]["type"]
        assert params["need_anchor_info"]["required"] is False

    async def test_all_platforms_respects_limit(self, harness):
        with respx.mock:
            result = await _execute(harness, {"operation": "list_endpoints", "platform": "all", "limit": 5})
            assert respx.calls.call_count == 0

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["count"] == 5
        assert len(payload["endpoints"]) == 5
        assert payload["total"] > 500

    async def test_default_limit_caps_at_100(self, harness):
        with respx.mock:
            result = await _execute(harness, {"operation": "list_endpoints"})
            assert respx.calls.call_count == 0

        harness.assert_envelope(result, success=True)
        assert result["result"]["count"] == 100

    async def test_search_over_path(self, harness):
        with respx.mock:
            result = await _execute(
                harness,
                {"operation": "list_endpoints", "platform": "hybrid", "search": "/api/v1/hybrid/video_data"},
            )
            assert respx.calls.call_count == 0

        harness.assert_envelope(result, success=True)
        ids = [e["endpoint"] for e in result["result"]["endpoints"]]
        assert "hybrid_parsing.video_data" in ids

    async def test_no_match_is_empty_success(self, harness):
        with respx.mock:
            result = await _execute(
                harness,
                {"operation": "list_endpoints", "platform": "douyin", "search": "zzz_no_such_endpoint_zzz"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["endpoints"] == []
        assert result["result"]["count"] == 0

    async def test_list_never_needs_a_key(self, harness):
        # Discovery is introspection over the installed SDK; a missing key
        # must not block it.
        with respx.mock:
            result = await _execute(harness, {"operation": "list_endpoints", "platform": "douyin"}, keys={})

        harness.assert_envelope(result, success=True)
        assert result["result"]["count"] > 0


# ============================================================================
# 11b. describe: TikHub's published documentation for one endpoint
# ============================================================================


class TestDescribe:
    @respx.mock
    async def test_merges_tikhubs_documentation_into_the_sdk_signature(self, harness):
        spec = respx.get(SPEC_URL).mock(return_value=httpx.Response(200, json=ONE_VIDEO_SPEC))

        # No key: the document is public.
        result = await _execute(
            harness, {"operation": "describe", "endpoint": "douyin_web.fetch_one_video"}, keys={}
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(
            result, ["operation", "endpoint", "http_method", "path", "summary", "params", "description"]
        )
        payload = result["result"]
        assert payload["operation"] == "describe"
        assert payload["endpoint"] == "douyin_web.fetch_one_video"
        assert payload["http_method"] == "GET"
        assert payload["path"] == "/api/v1/douyin/web/fetch_one_video"
        assert "note" not in payload

        # The English half only.
        assert payload["description"].startswith("### Purpose:")
        assert "0.001 USD/request" in payload["description"]
        assert "用途" not in payload["description"]

        params = {p["name"]: p for p in payload["params"]}
        assert params["aweme_id"]["required"] is True
        assert params["aweme_id"]["type"]
        assert params["aweme_id"]["description"] == "作品id/Video id"
        assert params["need_anchor_info"]["required"] is False
        assert params["need_anchor_info"]["default"] is False

        assert spec.call_count == 1
        assert "Authorization" not in respx.calls.last.request.headers
        harness.database.save_api_usage_metric.assert_not_awaited()

    @respx.mock
    async def test_the_document_is_fetched_once(self, harness):
        spec = respx.get(SPEC_URL).mock(return_value=httpx.Response(200, json=ONE_VIDEO_SPEC))

        for _ in range(2):
            result = await _execute(harness, {"operation": "describe", "endpoint": "douyin_web.fetch_one_video"})
            harness.assert_envelope(result, success=True)

        assert spec.call_count == 1

    @respx.mock
    async def test_a_route_tikhub_no_longer_publishes_says_so(self, harness):
        respx.get(SPEC_URL).mock(return_value=httpx.Response(200, json={"openapi": "3.1.0", "paths": {}}))

        result = await _execute(harness, {"operation": "describe", "endpoint": "douyin_web.fetch_one_video"})

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert "description" not in payload
        assert "retired" in payload["note"]
        assert "list_endpoints" in payload["note"]
        # What the SDK knows is still returned.
        assert {p["name"] for p in payload["params"]} == {"aweme_id", "need_anchor_info"}

    @respx.mock
    async def test_unreachable_documentation_degrades_and_is_not_cached(self, harness):
        spec = respx.get(SPEC_URL).mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json=ONE_VIDEO_SPEC)]
        )
        request = {"operation": "describe", "endpoint": "douyin_web.fetch_one_video"}

        first = await _execute(harness, request)

        harness.assert_envelope(first, success=True)
        assert "description" not in first["result"]
        assert "could not be loaded" in first["result"]["note"]
        assert {p["name"] for p in first["result"]["params"]} == {"aweme_id", "need_anchor_info"}

        second = await _execute(harness, request)

        assert second["result"]["description"].startswith("### Purpose:")
        assert spec.call_count == 2

    @respx.mock
    async def test_unknown_or_missing_endpoint_fails_before_any_http(self, harness):
        for endpoint in ("nope.nothing", ""):
            result = await _execute(harness, {"operation": "describe", "endpoint": endpoint})
            harness.assert_envelope(result, success=False)
            assert result["error_type"] == "NodeUserError"
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_a_description_without_the_english_heading_is_returned_whole(self, harness):
        spec = json.loads(json.dumps(ONE_VIDEO_SPEC))
        spec["paths"]["/api/v1/douyin/web/fetch_one_video"]["get"]["description"] = "Get single video data"
        respx.get(SPEC_URL).mock(return_value=httpx.Response(200, json=spec))

        result = await _execute(harness, {"operation": "describe", "endpoint": "douyin_web.fetch_one_video"})

        assert result["result"]["description"] == "Get single video data"


# ============================================================================
# 12. index / introspection
# ============================================================================


class TestIndex:
    async def test_index_is_large_and_sorted(self):
        index = await _sdk.endpoint_index()
        # A floor well under the real count (about 1,050 in SDK 2.2.0): a
        # release that retires routes must not fail this, a broken
        # introspection must.
        assert len(index) > 500
        ids = [info.endpoint for info in index]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)

    async def test_index_is_cached(self):
        first = await _sdk.endpoint_index()
        second = await _sdk.endpoint_index()
        assert first is second

    async def test_every_platform_matches_a_live_resource(self):
        assert _sdk.PLATFORMS[0] == "all"
        index = await _sdk.endpoint_index()
        live = {info.platform for info in index}
        missing = [p for p in _sdk.PLATFORMS if p != "all" and p not in live]
        assert not missing, f"PLATFORMS entries with no SDK resource (SDK drift): {missing}"

    def test_params_platform_literal_mirrors_platforms(self):
        cls = get_node_class("tikhubAction")
        assert tuple(get_args(cls.Params.model_fields["platform"].annotation)) == tuple(_sdk.PLATFORMS)

    async def test_endpoint_info_shape(self):
        index = await _sdk.endpoint_index()
        info = next(i for i in index if i.endpoint == "douyin_web.fetch_one_video")
        assert info.resource == "douyin_web"
        assert info.method == "fetch_one_video"
        assert info.platform == "douyin"
        assert info.http_method == "GET"
        assert info.path == "/api/v1/douyin/web/fetch_one_video"
        assert info.summary
        params = {p.name: p for p in info.params}
        assert params["aweme_id"].required is True
        assert params["aweme_id"].type
        assert params["need_anchor_info"].required is False

    async def test_lookup_caches_populated(self):
        await _sdk.endpoint_index()
        assert _sdk._BY_ENDPOINT["douyin_web.fetch_one_video"].path == "/api/v1/douyin/web/fetch_one_video"
        assert _sdk._BY_PATH["/api/v1/douyin/web/fetch_one_video"].endpoint == "douyin_web.fetch_one_video"

    @pytest.mark.parametrize(
        "raw",
        [
            "douyin_web.fetch_one_video",
            "douyin_web/fetch_one_video",
            "/api/v1/douyin/web/fetch_one_video",
            "https://api.tikhub.io/api/v1/douyin/web/fetch_one_video",
            "  douyin_web.fetch_one_video  ",
        ],
    )
    async def test_resolve_endpoint_aliases(self, raw):
        info = await _sdk.resolve_endpoint(raw)
        assert info.endpoint == "douyin_web.fetch_one_video"

    async def test_resolve_unknown_suggests_close_matches(self):
        with pytest.raises(NodeUserError) as excinfo:
            await _sdk.resolve_endpoint("douyin_web.fetch_one_vidoe")
        message = str(excinfo.value)
        assert "fetch_one_video" in message
        assert "list_endpoints" in message

    async def test_resolve_blank_is_user_error(self):
        with pytest.raises(NodeUserError):
            await _sdk.resolve_endpoint("")


# ============================================================================
# 13. option loader
# ============================================================================


class TestOptionLoader:
    async def test_returns_options_for_a_platform(self):
        loader = get_option_loader("tikhubEndpoints")
        options = await loader({"platform": "douyin"})
        assert options
        for option in options:
            assert set(option) >= {"value", "label"}
            assert option["value"].startswith("douyin")
        values = [o["value"] for o in options]
        assert "douyin_web.fetch_one_video" in values
        entry = next(o for o in options if o["value"] == "douyin_web.fetch_one_video")
        assert "/api/v1/douyin/web/fetch_one_video" in entry.get("description", "")

    async def test_all_platform_returns_no_options(self):
        # ~1100 items would swamp the dropdown; the panel prompts for a
        # platform instead. The LLM path accepts any id as free text.
        loader = get_option_loader("tikhubEndpoints")
        assert await loader({"platform": "all"}) == []
        assert await loader({}) == []

    async def test_loader_matches_direct_loader_function(self):
        assert get_option_loader("tikhubEndpoints") is _sdk.load_tikhub_endpoints

    async def test_degrades_to_empty_when_sdk_missing(self):
        loader = get_option_loader("tikhubEndpoints")
        _reset_cache()
        blocked = {
            "tikhub": None,
            "tikhub.resources": None,
            "tikhub.resources._base": None,
            "tikhub.errors": None,
            "tikhub.client": None,
        }
        with patch.dict(sys.modules, blocked):
            options = await loader({"platform": "douyin"})
        assert options == []


# ============================================================================
# 14. boot-path purity
# ============================================================================


class TestNoEagerSdkImport:
    """Importing the plugin must never import ``tikhub``.

    Runs in a clean interpreter because the pytest process already has the
    SDK loaded by the introspection tests above.
    """

    def _probe(self, code: str) -> str:
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=SERVER_DIR,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, f"probe subprocess failed (rc={result.returncode}):\n{result.stderr}"
        return result.stdout

    def test_plugin_imports_without_sdk(self):
        out = self._probe(
            "import sys\n"
            "sys.modules['tikhub'] = None\n"
            "import nodes.scraper.tikhub_action\n"
            "from services.node_registry import get_node_class\n"
            "print('REGISTERED=' + str(get_node_class('tikhubAction') is not None))\n"
        )
        assert "REGISTERED=True" in out

    def test_plugin_import_does_not_load_sdk(self):
        out = self._probe(
            "import sys\n"
            "import nodes.scraper.tikhub_action\n"
            "import nodes.scraper.tikhub_action._sdk\n"
            "print('LEAKED=' + str('tikhub' in sys.modules))\n"
        )
        assert "LEAKED=False" in out


# ============================================================================
# 15. credential probe
# ============================================================================


class _ProbeResponse:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
        self.request = httpx.Request("GET", USER_INFO)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )
        return None

    def json(self):
        return self._body


def _fake_client(captured: dict, response: _ProbeResponse):
    class _Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            captured["params"] = kwargs.get("params")
            captured["json"] = kwargs.get("json")
            return response

        async def get(self, url, **kwargs):
            return await self.request("GET", url, **kwargs)

    return _Client


class TestCredentialProbe:
    def test_declaration(self):
        assert TikHubCredential.id == "tikhub"
        assert TikHubCredential.auth == "api_key"
        assert TikHubCredential.key_location == "bearer"
        assert TikHubCredential.probe_url == USER_INFO
        assert TikHubCredential.category == "Scrapers"
        assert TikHubCredential.docs_url

    def test_inject_is_bearer(self):
        request = TikHubCredential.inject({"api_key": "tk"}, {"headers": {}, "params": {}})
        assert request["headers"]["Authorization"] == "Bearer tk"

    async def test_probe_200_reads_balance(self, monkeypatch):
        import services.plugin.credential as credential_mod

        captured: dict = {}
        body = {
            "code": 200,
            "router": "/api/v1/tikhub/user/get_user_info",
            "data": {
                "user_data": {"email": "owner@example.com", "balance": 12.5, "free_credit": 1.0},
                "api_key_data": {"api_key_name": "default"},
            },
        }
        monkeypatch.setattr(credential_mod.httpx, "AsyncClient", _fake_client(captured, _ProbeResponse(200, body)))

        result = await TikHubCredential._probe("tk123")

        assert isinstance(result, ProbeResult)
        assert result.valid is True
        assert result.extra["balance"] == 12.5
        assert "owner@example.com" in result.message
        assert captured["method"] == "GET"
        assert captured["url"] == USER_INFO
        assert captured["headers"]["Authorization"] == "Bearer tk123"

    async def test_probe_tolerates_sparse_body(self, monkeypatch):
        import services.plugin.credential as credential_mod

        captured: dict = {}
        monkeypatch.setattr(
            credential_mod.httpx,
            "AsyncClient",
            _fake_client(captured, _ProbeResponse(200, {"code": 200, "data": {}})),
        )

        result = await TikHubCredential._probe("tk123")
        assert result.valid is True

    async def test_probe_401_raises(self, monkeypatch):
        import services.plugin.credential as credential_mod

        captured: dict = {}
        monkeypatch.setattr(
            credential_mod.httpx,
            "AsyncClient",
            _fake_client(captured, _ProbeResponse(401, {"detail": "invalid api key"})),
        )

        with pytest.raises(httpx.HTTPStatusError):
            await TikHubCredential._probe("bad")

    def test_handle_probe_response_reads_balance(self):
        response = httpx.Response(
            200,
            json={"code": 200, "data": {"user_data": {"email": "o@x.dev", "balance": 3.25}}},
            request=httpx.Request("GET", USER_INFO),
        )
        result = TikHubCredential._handle_probe_response(response)
        assert result.valid is True
        assert result.extra["balance"] == 3.25


# ============================================================================
# 16. catalogue + assets
# ============================================================================


class TestCatalogueAndAssets:
    def test_credential_providers_entry(self):
        config = json.loads((SERVER_DIR / "config" / "credential_providers.json").read_text(encoding="utf-8"))
        entry = config["providers"]["tikhub"]
        assert entry["name"] == "TikHub"
        assert entry["kind"] == "apiKey"
        assert entry["category"] == "scrapers"
        assert entry["usage_service"] == "tikhub"
        assert entry["icon_ref"] == "/api/schemas/credentials/tikhub/icon"
        assert "validate_as" not in entry
        field = entry["fields"][0]
        assert field["key"] == "apiKey"
        assert field["secret"] is True
        assert field["required"] is True

    def test_pricing_has_both_halves(self):
        pricing = json.loads((SERVER_DIR / "config" / "pricing.json").read_text(encoding="utf-8"))
        api = pricing["api"]["tikhub"]
        assert api["request"] == 0.001
        assert api["meta"] == 0.0
        op_map = pricing["operation_map"]["tikhub"]
        assert op_map["call"] == "request"
        assert op_map["fetch_url"] == "request"
        assert op_map["account"] == "meta"
        # every mapped operation resolves to a price line
        for target in op_map.values():
            assert target in api

    def test_node_allowlist_enables_node(self):
        allowlist = json.loads((SERVER_DIR / "config" / "node_allowlist.json").read_text(encoding="utf-8"))
        assert "tikhubAction" in allowlist["enabled_nodes"]
        assert "tikhubAction" not in allowlist.get("disabled_nodes", [])

    def test_visuals_skill_binding(self):
        visuals = json.loads((SERVER_DIR / "nodes" / "visuals.json").read_text(encoding="utf-8"))
        assert visuals["tikhubAction"]["skill"] == "tikhub-skill"

    def test_tool_name_snapshot(self):
        snapshot = json.loads((SERVER_DIR / "tests" / "fixtures" / "tool_names_snapshot.json").read_text(encoding="utf-8"))
        assert snapshot["tikhubAction"] == "tikhub_action"

    def test_plugin_folder_assets(self):
        meta = json.loads((PLUGIN_DIR / "meta.json").read_text(encoding="utf-8"))
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", meta["color"])
        icon = PLUGIN_DIR / "icon.svg"
        assert icon.exists()
        assert "<svg" in icon.read_text(encoding="utf-8")

    def test_credential_icon_is_colocated(self):
        brand = SCRAPER_DIR / "tikhub.svg"
        assert brand.exists()
        assert "<svg" in brand.read_text(encoding="utf-8")
        assert TikHubCredential.get_icon_path() == brand

    def test_skill_exists_with_tool_binding(self):
        if not SKILL_MD.exists():
            pytest.skip(f"{SKILL_MD} not written yet (owned by the docs/skill agent)")
        text = SKILL_MD.read_text(encoding="utf-8")
        assert text.startswith("---")
        frontmatter = text.split("---", 2)[1]
        assert "name: tikhub-skill" in frontmatter
        assert "tikhub_action" in frontmatter


# ============================================================================
# 17. usage tracking
# ============================================================================


class TestUsageTracking:
    @respx.mock
    async def test_call_records_one_metric_after_2xx(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok({"x": 1})))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}},
            total_cost=0.001,
        )

        harness.assert_envelope(result, success=True)
        harness.database.save_api_usage_metric.assert_awaited_once()
        metric = harness.database.save_api_usage_metric.await_args.args[0]
        assert metric["service"] == "tikhub"
        assert metric["resource_count"] == 1
        assert metric["cost"] == 0.001
        assert metric["workflow_id"] == "test_workflow"
        assert metric["session_id"] == "test_session"
        assert metric["node_id"]
        assert "/api/v1/douyin/web/fetch_one_video" in str(metric.get("endpoint"))
        assert result["result"]["cost_usd"] == 0.001

    @respx.mock
    async def test_fetch_url_records_metric(self, harness):
        respx.get(HYBRID_VIDEO_DATA).mock(return_value=httpx.Response(200, json=_ok({}, router="/api/v1/hybrid/video_data")))

        result = await _execute(harness, {"operation": "fetch_url", "url": "https://v.douyin.com/abc/"})

        harness.assert_envelope(result, success=True)
        harness.database.save_api_usage_metric.assert_awaited_once()
        assert harness.database.save_api_usage_metric.await_args.args[0]["service"] == "tikhub"

    async def test_list_endpoints_records_nothing(self, harness):
        with respx.mock:
            result = await _execute(harness, {"operation": "list_endpoints", "platform": "douyin"})

        harness.assert_envelope(result, success=True)
        harness.database.save_api_usage_metric.assert_not_awaited()

    @respx.mock
    async def test_4xx_records_nothing(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(402, json={"detail": "no balance"}))

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}},
        )

        harness.assert_envelope(result, success=False)
        harness.database.save_api_usage_metric.assert_not_awaited()

    @respx.mock
    async def test_tracking_failure_never_fails_the_call(self, harness):
        respx.get(FETCH_ONE_VIDEO).mock(return_value=httpx.Response(200, json=_ok({"x": 1})))
        harness.database.save_api_usage_metric.side_effect = RuntimeError("db down")

        result = await _execute(
            harness,
            {"operation": "call", "endpoint": "douyin_web.fetch_one_video", "params": {"aweme_id": "1"}},
        )

        harness.assert_envelope(result, success=True)
        assert result["result"]["data"] == {"x": 1}


# ============================================================================
# 18. skill endpoint ids
# ============================================================================


class TestSkillEndpointIds:
    async def test_every_cited_endpoint_resolves(self):
        if not SKILL_MD.exists():
            pytest.skip(f"{SKILL_MD} not written yet (owned by the docs/skill agent)")
        text = SKILL_MD.read_text(encoding="utf-8")
        index = await _sdk.endpoint_index()
        resources = {info.resource for info in index}
        known = {info.endpoint for info in index}

        cited = set(re.findall(r"`([a-z0-9_]+\.[a-z0-9_]+)`", text))
        # Only ids whose resource half is a real SDK resource are endpoint
        # citations; `params.aweme_id`-style dotted paths are prose.
        candidates = {c for c in cited if c.split(".", 1)[0] in resources}
        assert candidates, "SKILL.md cites no endpoint ids"
        unknown = sorted(candidates - known)
        assert not unknown, f"SKILL.md cites endpoint ids missing from the SDK index: {unknown}"
