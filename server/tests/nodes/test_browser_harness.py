"""Contract tests for the browserHarness node (Wave 19).

Freezes the node's input -> output behaviour + the op -> generated-code
mapping. All subprocess side-effects are mocked:
  - node tests patch `get_browser_harness_service` with a fake exposing
    async `run_code` / `doctor` (mirrors _FakeBrowserService in
    test_web_automation.py).
  - service tests patch `BrowserHarnessService._run_sync` so no
    subprocess is spawned.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.node_contract


# ============================================================================
# Helpers
# ============================================================================


class _FakeHarnessService:
    def __init__(self, canned: dict | None = None, error: Exception | None = None):
        self._canned = canned if canned is not None else {"output": "ok"}
        self._error = error
        self.code_calls: list[tuple[str, int]] = []
        self.doctor_calls: int = 0

    async def run_code(self, code, timeout=60):
        self.code_calls.append((code, timeout))
        if self._error:
            raise self._error
        return self._canned

    async def doctor(self):
        self.doctor_calls += 1
        if self._error:
            raise self._error
        return {"output": "doctor report"}


def _patch_service(svc):
    return patch(
        "nodes.browser.browser_harness._service.get_browser_harness_service",
        return_value=svc,
    )


# ============================================================================
# Node contract
# ============================================================================


class TestBrowserHarnessNode:
    async def test_run_python_pipes_code_verbatim(self, harness):
        fake = _FakeHarnessService(canned={"result": {"title": "Example"}, "output": "{}"})
        code = "ensure_real_tab()\nprint(page_info())"

        with _patch_service(fake):
            result = await harness.execute(
                "browserHarness",
                {"operation": "run_python", "code": code, "timeout": 45},
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["operation", "data"])
        payload = result["result"]
        assert payload["operation"] == "run_python"
        assert payload["data"]["result"] == {"title": "Example"}
        assert fake.code_calls == [(code, 45)]

    async def test_goto_generates_navigation_snippet(self, harness):
        fake = _FakeHarnessService()

        with _patch_service(fake):
            result = await harness.execute(
                "browserHarness",
                {"operation": "goto", "url": "https://example.com"},
            )

        harness.assert_envelope(result, success=True)
        code, _ = fake.code_calls[-1]
        assert "goto_url('https://example.com')" in code
        assert "wait_for_load()" in code
        assert "page_info()" in code

    async def test_goto_url_with_quotes_is_repr_safe(self, harness):
        """repr()-embedding must survive quotes in user input — the
        generated text is executed as Python by the harness."""
        fake = _FakeHarnessService()
        tricky = "https://example.com/?q='a'\"b\""

        with _patch_service(fake):
            await harness.execute("browserHarness", {"operation": "goto", "url": tricky})

        code, _ = fake.code_calls[-1]
        assert repr(tricky) in code

    async def test_js_requires_expression(self, harness):
        fake = _FakeHarnessService()

        with _patch_service(fake):
            result = await harness.execute("browserHarness", {"operation": "js"})

        harness.assert_envelope(result, success=False)
        assert "expression is required" in (result.get("error") or "")

    async def test_screenshot_maps_full_page(self, harness):
        fake = _FakeHarnessService()

        with _patch_service(fake):
            await harness.execute(
                "browserHarness",
                {"operation": "screenshot", "full_page": True},
            )

        code, _ = fake.code_calls[-1]
        assert "capture_screenshot(full=True)" in code

    async def test_doctor_bypasses_code_path(self, harness):
        fake = _FakeHarnessService()

        with _patch_service(fake):
            result = await harness.execute("browserHarness", {"operation": "doctor"})

        harness.assert_envelope(result, success=True)
        assert fake.doctor_calls == 1
        assert fake.code_calls == []
        assert result["result"]["data"]["output"] == "doctor report"

    async def test_service_unavailable_is_user_error(self, harness):
        with patch(
            "nodes.browser.browser_harness._service.get_browser_harness_service",
            return_value=None,
        ):
            result = await harness.execute(
                "browserHarness",
                {"operation": "run_python", "code": "print(1)"},
            )

        harness.assert_envelope(result, success=False)
        assert result.get("error_type") == "NodeUserError"
        assert "uv" in (result.get("error") or "")

    async def test_node_user_error_passthrough(self, harness):
        from services.plugin.base import NodeUserError

        fake = _FakeHarnessService(error=NodeUserError("browser-harness cannot reach Chrome over CDP. detail"))

        with _patch_service(fake):
            result = await harness.execute(
                "browserHarness",
                {"operation": "run_python", "code": "print(1)"},
            )

        harness.assert_envelope(result, success=False)
        assert result.get("error_type") == "NodeUserError"
        assert "Chrome" in (result.get("error") or "")


# ============================================================================
# Service unit tests (no subprocess)
# ============================================================================


class TestBrowserHarnessService:
    def _svc(self):
        from nodes.browser.browser_harness._service import BrowserHarnessService

        return BrowserHarnessService("browser-harness")

    async def test_shape_output_parses_trailing_json(self):
        svc = self._svc()
        with patch.object(svc, "_run_sync", return_value='navigating\n{"title": "Example"}'):
            out = await svc.run_code("print(1)")
        assert out["result"] == {"title": "Example"}
        assert "navigating" in out["output"]

    async def test_shape_output_plain_text_fallback(self):
        svc = self._svc()
        with patch.object(svc, "_run_sync", return_value="C:\\shots\\shot.png"):
            out = await svc.run_code("print(p)")
        assert out == {"output": "C:\\shots\\shot.png"}

    async def test_empty_code_is_user_error(self):
        from services.plugin.base import NodeUserError

        svc = self._svc()
        with pytest.raises(NodeUserError, match="code is required"):
            await svc.run_code("   ")

    def test_chrome_hint_maps_to_user_error(self):
        """stderr containing a Chrome-connection fragment must raise
        NodeUserError with the doctor guidance, not a raw RuntimeError."""
        import subprocess as _sp
        from unittest.mock import MagicMock

        from services.plugin.base import NodeUserError

        svc = self._svc()
        proc = MagicMock(returncode=1, stdout="", stderr="DevToolsActivePort not found in [...]")
        with patch("nodes.browser.browser_harness._service.subprocess.run", return_value=proc):
            with pytest.raises(NodeUserError, match="remote-debugging-port"):
                svc._run_sync(["browser-harness"], "print(1)", 30)
        assert _sp  # keep import referenced

    def test_timeout_maps_to_user_error(self):
        import subprocess as _sp

        from services.plugin.base import NodeUserError

        svc = self._svc()
        with patch(
            "nodes.browser.browser_harness._service.subprocess.run",
            side_effect=_sp.TimeoutExpired(cmd="browser-harness", timeout=30),
        ):
            with pytest.raises(NodeUserError, match="timed out"):
                svc._run_sync(["browser-harness"], "print(1)", 30)

    async def test_doctor_tolerates_exit_1(self):
        """doctor exits 1 when checks fail — that's a report, not an error."""
        import subprocess as _sp
        from unittest.mock import MagicMock

        svc = self._svc()
        proc = MagicMock(returncode=1, stdout="", stderr="[FAIL] daemon alive")
        with patch("nodes.browser.browser_harness._service.subprocess.run", return_value=proc):
            out = await svc.doctor()
        assert "[FAIL] daemon alive" in out["output"]
        assert _sp  # keep import referenced
