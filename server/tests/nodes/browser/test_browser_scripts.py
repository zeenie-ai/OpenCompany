"""Execute the generated CLI scripts with a browser boundary we can control."""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from nodes.browser._scripts import MARKER, build_script


class Browser:
    def __init__(self):
        self.target = "tab-1"
        self.url = "https://example.com/"
        self.title = "Example"
        self.markers = {}
        self.actions = []
        self.loaded = True
        self.load_error = None
        self.load_budgets = []
        self.navigation_url = None
        self.switch_error = None
        self.switch_noop = False
        self.click_result = {"x": 42.0, "y": 25.0}
        self.released = []
        self.page_text = "Normal page"
        self.focused_box = [10, 20, 200, 30]

    def current_tab(self):
        return {"targetId": self.target, "url": self.url, "title": self.title}

    def js(self, expression):
        if "return el ? el.innerText : null" in expression:
            return self.page_text
        if "document.activeElement" in expression:
            if isinstance(self.focused_box, Exception):
                raise self.focused_box
            return self.focused_box
        return {"url": self.url, "title": self.title, **self.markers}

    def cdp(self, method, **kwargs):
        if method in {"DOM.resolveNode", "Runtime.evaluate"}:
            return {"object": {"objectId": "element"}, "result": {"objectId": "element"}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"value": self.click_result}}
        if method == "Runtime.releaseObject":
            self.released.append(kwargs["objectId"])
        if method == "Accessibility.getFullAXTree":
            return {"nodes": []}
        return {}

    def goto_url(self, url):
        self.actions.append(("navigate", url))
        self.url = self.navigation_url or url

    def switch_tab(self, target, **kwargs):
        if self.switch_error:
            raise self.switch_error
        if not self.switch_noop:
            self.target = target

    def wait_for_load(self, seconds):
        self.load_budgets.append(seconds)
        if self.load_error:
            raise self.load_error
        return self.loaded

    def execute(self, operation, args=None):
        namespace = {
            "current_tab": self.current_tab,
            "js": self.js,
            "cdp": self.cdp,
            "goto_url": self.goto_url,
            "switch_tab": self.switch_tab,
            "wait_for_load": self.wait_for_load,
            "wait": lambda seconds: None,
            "click_at_xy": lambda *p, **k: self.actions.append(("click", p, k)),
            "type_text": lambda text: self.actions.append(("type", text)),
            "press_key": lambda *p: self.actions.append(("press", p)),
            "page_info": lambda: {"url": self.url, "title": self.title},
            "list_tabs": lambda **k: [self.current_tab()],
            "capture_screenshot": lambda **k: self.actions.append(("screenshot", k)) or k["path"],
        }
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exec(build_script(operation, args or {}, "testnonce"), namespace)
        line = stdout.getvalue().strip().splitlines()[-1]
        assert line.startswith(f"{MARKER}:testnonce:")
        return json.loads(line.split(":", 2)[2])


@pytest.mark.parametrize("error", [None, TimeoutError("browser load timed out")])
def test_failed_load_is_a_typed_timeout_and_honors_budget(error):
    browser = Browser()
    browser.loaded, browser.load_error = False, error
    result = browser.execute("navigate", {"url": "https://example.com/search", "timeout": 7.5})
    assert result["ok"] is False
    assert result["error"]["type"] == "timeout"
    assert browser.load_budgets == [7.5]
    assert result["page"]["target_id"] == "tab-1"


@pytest.mark.parametrize("url", ["https://www.google.com/sorry/index", "https://google.co.uk/sorry/", "https://www.google.com.au/sorry"])
def test_challenge_preflight_never_performs_the_action(url):
    browser = Browser()
    browser.url = url
    result = browser.execute("type", {"text": "must not type", "submit": True, "_guard_actions": True})
    assert result["error"]["type"] == "challenge_required"
    assert browser.actions == []
    assert result["page"]["url"] == url


@pytest.mark.parametrize("loaded", [True, False])
def test_navigation_into_challenge_wins_over_success_or_load_timeout(loaded):
    browser = Browser()
    browser.navigation_url = "https://www.google.com/sorry/index"
    browser.loaded = loaded
    result = browser.execute("navigate", {"url": "https://www.google.com/search?q=example", "_guard_actions": True})
    assert result["ok"] is False
    assert result["error"]["type"] == "challenge_required"
    assert len(browser.actions) == 1


@pytest.mark.parametrize("markers", [{"challengeOptions": True}, {"challengeForm": True, "challengeScript": True}])
def test_cloudflare_requires_corroborated_interstitial(markers):
    browser = Browser()
    browser.title, browser.markers = "Just a moment...", markers
    result = browser.execute("click", {"x": 5, "y": 6, "_guard_actions": True})
    assert result["error"]["type"] == "challenge_required"
    assert browser.actions == []


@pytest.mark.parametrize(
    "url,title,markers",
    [
        ("https://example.com/captcha", "How reCAPTCHA and Cloudflare challenges work", {}),
        ("https://example.com/", "Just a moment...", {}),
        ("https://example.com/", "Just a moment...", {"challengeForm": True}),
        ("https://example.com/", "Login", {"challengeScript": True, "challengeOptions": True}),
        ("https://google.com.example.org/sorry/index", "Example", {}),
        ("https://example.org/sorry/?next=https://google.com", "Example", {}),
        ("https://google.com/sorry-about-that", "Example", {}),
    ],
)
def test_discussion_widgets_and_lookalike_domains_do_not_block(url, title, markers):
    browser = Browser()
    browser.url, browser.title, browser.markers = url, title, markers
    result = browser.execute("click", {"x": 5, "y": 6, "_guard_actions": True})
    assert result["ok"] is True
    assert browser.actions[0][0] == "click"
    assert result["value"] == {"x": 5.0, "y": 6.0}


@pytest.mark.parametrize("operation", ["snapshot", "page_info", "page_text"])
def test_observations_report_challenge_without_returning_page_content(operation):
    browser = Browser()
    browser.url, browser.page_text = "https://google.com/sorry/", "private challenge page content"
    result = browser.execute(operation)
    assert result["error"]["type"] == "challenge_required"
    assert "value" not in result
    assert browser.page_text not in json.dumps(result)


def test_screenshot_remains_available_for_human_review():
    browser = Browser()
    browser.url = "https://google.com/sorry/"
    result = browser.execute("screenshot", {"path": "review.png"})
    assert result["ok"] is True
    assert result["value"] == {"path": "review.png"}


@pytest.mark.parametrize("failure", ["exception", "wrong_target"])
def test_unavailable_requested_tab_never_acts_on_another_tab(failure):
    browser = Browser()
    browser.switch_error = RuntimeError("target closed") if failure == "exception" else None
    browser.switch_noop = failure == "wrong_target"
    result = browser.execute("type", {"text": "private", "_target_id": "tab-2"})
    assert result["error"]["type"] == "stale_ref"
    assert browser.actions == []


def test_verified_tab_switch_can_continue():
    browser = Browser()
    result = browser.execute("type", {"text": "hello", "clear": False, "_target_id": "tab-2"})
    assert result["ok"] is True
    assert result["page"]["target_id"] == "tab-2"
    assert browser.actions == [("type", "hello")]


@pytest.mark.parametrize("reason", ["disabled", "hidden", "covered", "detached"])
def test_target_not_ready_is_never_clicked(reason):
    browser = Browser()
    browser.click_result = {"error": reason}
    result = browser.execute("click", {"backend_node_id": 12})
    assert result["error"]["type"] == "not_found"
    assert browser.actions == []
    assert browser.released == ["element"]


def test_ready_target_is_clicked_once_and_budget_is_forwarded():
    browser = Browser()
    result = browser.execute("click", {"selector": "#submit", "timeout": 9})
    assert result["ok"] is True
    assert browser.actions == [("click", (42.0, 25.0), {"button": "left", "clicks": 1})]
    assert browser.load_budgets == [9]
    assert browser.released == ["element"]


def test_click_and_hover_say_where_they_happened_beside_their_value():
    browser = Browser()
    clicked = browser.execute("click", {"selector": "#submit"})
    assert clicked["value"] == {"x": 42.0, "y": 25.0}
    assert clicked["cursor"] == {"x": 42.0, "y": 25.0}
    hovered = browser.execute("hover", {"x": 7, "y": 9})
    assert hovered["cursor"] == {"x": 7.0, "y": 9.0}
    assert "cursor" not in browser.execute("snapshot")


def test_type_reports_the_fields_box_never_the_text():
    browser = Browser()
    result = browser.execute("type", {"text": "secret words", "clear": False})
    assert result["value"] == {"typed_chars": 12, "submitted": False}
    assert result["cursor"] == {"box": [10.0, 20.0, 200.0, 30.0]}
    assert "secret" not in json.dumps(result["cursor"])
    # Not finding the field only loses the cursor.
    browser.focused_box = RuntimeError("cdp gone")
    result = browser.execute("type", {"text": "hi", "clear": False})
    assert result["ok"] is True and "cursor" not in result


def test_select_reports_the_lists_box_and_keeps_it_out_of_the_value():
    browser = Browser()
    browser.click_result = {"selected": 1, "box": [5, 6, 70, 20]}
    result = browser.execute("select", {"selector": "#size", "values": ["M"]})
    assert result["value"] == {"selected": 1}
    assert result["cursor"] == {"box": [5.0, 6.0, 70.0, 20.0]}
    # A page that makes its box nonsense loses the cursor, not the action.
    browser.click_result = {"selected": 1, "box": ["a", "b", "c", "d"]}
    result = browser.execute("select", {"selector": "#size", "values": ["M"]})
    assert result["ok"] is True and result["value"] == {"selected": 1} and "cursor" not in result


def test_adversarial_arguments_and_page_text_remain_data():
    browser = Browser()
    hostile = "'); raise SystemExit(42) #\n__OPERATION__ __NONCE__ __ARGS__\nOCR1:testnonce:{\"ok\":false}"
    result = browser.execute("type", {"text": hostile, "clear": False})
    assert result["ok"] is True
    assert browser.actions == [("type", hostile)]
    browser.page_text = hostile
    result = browser.execute("page_text")
    assert result["ok"] is True
    assert result["value"]["text"] == hostile
