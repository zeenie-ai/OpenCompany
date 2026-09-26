"""The scripts each Browser operation pipes to the browser-use CLI.

The CLI (browser-use 0.13, a front for browser-harness) runs Python from
stdin with its helpers pre-imported: ``cdp``, ``goto_url``, ``page_info``,
``click_at_xy``, ``type_text``, ``press_key``, ``scroll``,
``capture_screenshot``, ``js``, ``new_tab``, ``switch_tab``, ``close_tab``,
``current_tab``, ``list_tabs``, ``wait``, ``wait_for_load``,
``wait_for_element``, ``wait_for_network_idle``. It has no element refs of
its own, so ``snapshot`` builds them here from the accessibility tree, and a
ref is resolved back to its element box before a click.

Two rules keep this safe:

- **Arguments are data, never code.** They travel as one JSON string literal
  (``json.loads(<repr>)``); no user or model text is ever formatted into the
  script. ``run_python`` code is ``exec``'d from that JSON, not spliced in.
- **Results cannot be forged by page content.** The script prints its result
  on a line tagged with a per-call random nonce, and the parser takes the
  last such line, so text a page makes the script print earlier (page text,
  a tool's output) cannot pose as the result.
"""

from __future__ import annotations

import json
from typing import Any, Dict

MARKER = "OCR1"

PRELUDE = r'''
import json as _oc_json
import sys as _oc_sys
_oc_args = _oc_json.loads(__ARGS__)
_oc_nonce = __NONCE__


class _OcError(Exception):
    def __init__(self, kind, message):
        Exception.__init__(self, message)
        self.kind = kind


def _oc_page():
    try:
        t = current_tab()
        return {"target_id": t.get("targetId") or t.get("target_id"), "url": t.get("url"), "title": t.get("title")}
    except Exception:
        return None


def _oc_emit(payload):
    payload["page"] = _oc_page()
    print("__MARKER__:" + _oc_nonce + ":" + _oc_json.dumps(payload, default=str), flush=True)


def _oc_node_center(backend_id):
    try:
        cdp("DOM.scrollIntoViewIfNeeded", backendNodeId=backend_id, _response_timeout=10)
    except Exception:
        pass
    try:
        quad = cdp("DOM.getBoxModel", backendNodeId=backend_id, _response_timeout=10)["model"]["content"]
    except Exception:
        raise _OcError("stale_ref", "That element is no longer on the page; take a new snapshot.")
    xs, ys = quad[0::2], quad[1::2]
    return sum(xs) / 4.0, sum(ys) / 4.0


def _oc_selector_center(selector):
    point = js(
        "(() => { const el = document.querySelector(" + _oc_json.dumps(selector) + "); if (!el) return null;"
        " el.scrollIntoView({block: 'center', inline: 'center'}); const b = el.getBoundingClientRect();"
        " return [b.left + b.width / 2, b.top + b.height / 2]; })()"
    )
    if not point:
        raise _OcError("not_found", "No element matches the selector " + selector)
    return float(point[0]), float(point[1])


def _oc_point(a):
    if a.get("backend_node_id") is not None:
        return _oc_node_center(int(a["backend_node_id"]))
    if a.get("selector"):
        return _oc_selector_center(a["selector"])
    if a.get("x") is not None and a.get("y") is not None:
        return float(a["x"]), float(a["y"])
    raise _OcError("script", "Give a ref from snapshot, a CSS selector, or x and y.")


def _oc_object(a):
    if a.get("backend_node_id") is not None:
        try:
            return cdp("DOM.resolveNode", backendNodeId=int(a["backend_node_id"]), _response_timeout=10)["object"]["objectId"]
        except Exception:
            raise _OcError("stale_ref", "That element is no longer on the page; take a new snapshot.")
    if a.get("selector"):
        found = cdp(
            "Runtime.evaluate",
            expression="document.querySelector(" + _oc_json.dumps(a["selector"]) + ")",
            _response_timeout=10,
        ).get("result", {})
        if not found.get("objectId"):
            raise _OcError("not_found", "No element matches the selector " + a["selector"])
        return found["objectId"]
    raise _OcError("script", "Give a ref from snapshot or a CSS selector.")


def _oc_settle(seconds):
    try:
        wait_for_load(seconds)
    except Exception:
        pass
'''

EPILOGUE = r'''
try:
    _oc_target = _oc_args.get("_target_id")
    if _oc_target:
        try:
            _oc_current = current_tab()
            if (_oc_current.get("targetId") or _oc_current.get("target_id")) != _oc_target:
                switch_tab(_oc_target)
        except Exception:
            pass
    _oc_emit({"ok": True, "value": _oc_main(_oc_args)})
except _OcError as _oc_e:
    _oc_emit({"ok": False, "error": {"type": _oc_e.kind, "message": str(_oc_e)}})
except SystemExit:
    raise
except BaseException as _oc_e:
    _oc_text = str(_oc_e)
    _oc_kind = "cdp" if ("'code'" in _oc_text and "'message'" in _oc_text) else "script"
    _oc_emit({"ok": False, "error": {"type": _oc_kind, "message": type(_oc_e).__name__ + ": " + _oc_text[:2000]}})
'''

_OPS: Dict[str, str] = {}

_OPS["navigate"] = r'''
def _oc_main(a):
    goto_url(a["url"])
    _oc_settle(float(a.get("timeout", 20)))
    return page_info()
'''

_OPS["snapshot"] = r'''
_OC_INTERACTIVE = {
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox", "option",
    "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "switch", "slider", "spinbutton", "treeitem",
}
_OC_ALWAYS = {"heading", "dialog", "alertdialog", "alert", "main", "navigation", "form", "img", "table"}
_OC_SKIP = {"none", "generic", "InlineTextBox", "LineBreak", "presentation"}


def _oc_prop(n, name):
    for p in n.get("properties") or []:
        if p.get("name") == name:
            return (p.get("value") or {}).get("value")
    return None


def _oc_main(a):
    nodes = cdp("Accessibility.getFullAXTree", _response_timeout=30).get("nodes") or []
    by_id = {n.get("nodeId"): n for n in nodes}
    roots = [n for n in nodes if not n.get("parentId")] or nodes[:1]
    budget = int(a.get("max_chars", 20000))
    interactive_only = bool(a.get("interactive_only", True))
    lines, refs, state = [], {}, {"used": 0, "truncated": False, "n": 0}

    def add(text):
        if state["used"] + len(text) + 1 > budget:
            state["truncated"] = True
            return False
        lines.append(text)
        state["used"] += len(text) + 1
        return True

    def visit(n, depth):
        if state["truncated"] or n is None:
            return
        role = (n.get("role") or {}).get("value") or ""
        name = " ".join(str((n.get("name") or {}).get("value") or "").split())[:160]
        shown = False
        if not n.get("ignored") and role not in _OC_SKIP:
            backend = n.get("backendDOMNodeId")
            interactive = role in _OC_INTERACTIVE and backend is not None and not _oc_prop(n, "disabled")
            text_node = role == "StaticText" and name
            if interactive or role in _OC_ALWAYS or (not interactive_only and (text_node or (name and role))):
                parts = ["  " * depth]
                if interactive:
                    state["n"] += 1
                    ref = "e" + str(state["n"])
                    refs[ref] = backend
                    parts.append("[" + ref + "] ")
                parts.append("text" if role == "StaticText" else role)
                if name:
                    parts.append(" " + _oc_json.dumps(name, ensure_ascii=False))
                value = (n.get("value") or {}).get("value")
                if value not in (None, "") and role in ("textbox", "searchbox", "combobox", "spinbutton", "slider"):
                    parts.append(" value=" + _oc_json.dumps(str(value)[:120], ensure_ascii=False))
                for flag in ("checked", "expanded", "selected", "pressed"):
                    flag_value = _oc_prop(n, flag)
                    if flag_value not in (None, False, "false"):
                        parts.append(" " + flag + "=" + str(flag_value).lower())
                if role == "heading" and _oc_prop(n, "level"):
                    parts.append(" level=" + str(_oc_prop(n, "level")))
                if not add("".join(parts)):
                    return
                shown = True
        for child in n.get("childIds") or []:
            visit(by_id.get(child), depth + 1 if shown else depth)

    for root in roots:
        visit(root, 0)
    return {"text": "\n".join(lines), "refs": refs, "truncated": state["truncated"], "info": page_info()}
'''

_OPS["click"] = r'''
def _oc_main(a):
    x, y = _oc_point(a)
    click_at_xy(x, y, button=a.get("button") or "left", clicks=int(a.get("clicks") or 1))
    wait(0.3)
    _oc_settle(5)
    return {"x": round(x, 1), "y": round(y, 1)}
'''

_OPS["hover"] = r'''
def _oc_main(a):
    x, y = _oc_point(a)
    cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    wait(0.2)
    return {"x": round(x, 1), "y": round(y, 1)}
'''

_OPS["type"] = r'''
def _oc_main(a):
    if a.get("backend_node_id") is not None or a.get("selector"):
        obj = _oc_object(a)
        cdp(
            "Runtime.callFunctionOn",
            objectId=obj,
            functionDeclaration="function() { this.scrollIntoView({block: 'center'}); this.focus(); }",
            _response_timeout=10,
        )
    if a.get("clear", True):
        press_key("a", 4 if _oc_sys.platform == "darwin" else 2)
        press_key("Backspace")
    text = a.get("text") or ""
    if text:
        type_text(text)
    if a.get("submit"):
        press_key("Enter")
        _oc_settle(10)
    return {"typed_chars": len(text), "submitted": bool(a.get("submit"))}
'''

_OPS["press"] = r'''
def _oc_main(a):
    press_key(a["key"], int(a.get("modifiers") or 0))
    wait(0.2)
    return {"key": a["key"]}
'''

_OPS["select"] = r'''
def _oc_main(a):
    obj = _oc_object(a)
    result = cdp(
        "Runtime.callFunctionOn",
        objectId=obj,
        functionDeclaration=(
            "function(vals) { if (this.tagName !== 'SELECT') return {error: 'not a <select> element'};"
            " const want = new Set(vals); let n = 0;"
            " for (const o of this.options) { const hit = want.has(o.value) || want.has(o.textContent.trim());"
            " o.selected = hit; if (hit) n++; }"
            " this.dispatchEvent(new Event('input', {bubbles: true}));"
            " this.dispatchEvent(new Event('change', {bubbles: true})); return {selected: n}; }"
        ),
        arguments=[{"value": list(a.get("values") or [])}],
        returnByValue=True,
        _response_timeout=10,
    )
    value = (result.get("result") or {}).get("value") or {}
    if value.get("error"):
        raise _OcError("script", value["error"])
    if not value.get("selected"):
        raise _OcError("not_found", "None of those options exist in the list")
    return value
'''

_OPS["scroll"] = r'''
def _oc_main(a):
    info = page_info()
    amount = int(a.get("amount") or 600)
    dx, dy = {"down": (0, amount), "up": (0, -amount), "right": (amount, 0), "left": (-amount, 0)}[a.get("direction") or "down"]
    if a.get("backend_node_id") is not None or a.get("selector"):
        x, y = _oc_point(a)
    else:
        x, y = (info.get("w") or 1280) / 2.0, (info.get("h") or 800) / 2.0
    scroll(x, y, dy=dy, dx=dx)
    wait(0.3)
    return page_info()
'''

_OPS["screenshot"] = r'''
def _oc_main(a):
    path = capture_screenshot(path=a["path"], full=bool(a.get("full_page")))
    return {"path": path or a["path"]}
'''

_OPS["tabs"] = r'''
def _oc_main(a):
    action = a.get("tab_action") or "list"
    if action == "new":
        new_tab(a.get("url") or "about:blank")
        _oc_settle(15)
    elif action == "switch":
        switch_tab(a["tab_id"], activate=True)
    elif action == "close":
        close_tab(a.get("tab_id"))
    tabs = []
    for t in list_tabs(include_chrome=False):
        tabs.append({"tab_id": t.get("targetId") or t.get("target_id"), "url": t.get("url"), "title": t.get("title")})
    return {"tabs": tabs}
'''

_OPS["history"] = r'''
def _oc_main(a):
    action = a.get("history_action") or "back"
    if action == "reload":
        cdp("Page.reload")
    else:
        history = cdp("Page.getNavigationHistory")
        index = history["currentIndex"] + (-1 if action == "back" else 1)
        entries = history.get("entries") or []
        if not 0 <= index < len(entries):
            raise _OcError("not_found", "There is no page to go " + ("back" if action == "back" else "forward") + " to.")
        cdp("Page.navigateToHistoryEntry", entryId=entries[index]["id"])
    _oc_settle(15)
    return page_info()
'''

_OPS["page_text"] = r'''
def _oc_main(a):
    selector = a.get("selector")
    target = "document.querySelector(" + _oc_json.dumps(selector) + ")" if selector else "document.body"
    text = js("(() => { const el = " + target + "; return el ? el.innerText : null; })()")
    if text is None:
        raise _OcError("not_found", "No element matches the selector " + str(selector))
    limit = int(a.get("max_chars") or 20000)
    return {"text": text[:limit], "truncated": len(text) > limit, "length": len(text)}
'''

_OPS["page_info"] = r'''
def _oc_main(a):
    info = page_info()
    info["tabs"] = len(list_tabs(include_chrome=False))
    return info
'''

_OPS["wait"] = r'''
def _oc_main(a):
    kind = a.get("wait_for") or "load"
    timeout = min(float(a.get("timeout") or 15), 120.0)
    value = a.get("wait_value") or ""
    if kind == "load":
        ok = wait_for_load(timeout)
    elif kind == "network_idle":
        ok = wait_for_network_idle(timeout, 500)
    elif kind == "selector":
        ok = wait_for_element(value, timeout, visible=True)
    elif kind == "text":
        import time as _oc_time
        deadline = _oc_time.time() + timeout
        ok = False
        while _oc_time.time() < deadline:
            if js("document.body && document.body.innerText.includes(" + _oc_json.dumps(value) + ")"):
                ok = True
                break
            wait(0.5)
    else:
        wait(min(float(value or 1), 30.0))
        ok = True
    if ok is False:
        raise _OcError("timeout", "Timed out waiting for " + kind + (" " + value if value else ""))
    return page_info()
'''

_OPS["evaluate"] = r'''
def _oc_main(a):
    return js(a["expression"])
'''

_OPS["run_python"] = r'''
def _oc_main(a):
    namespace = dict(globals())
    exec(compile(a["code"], "<run_python>", "exec"), namespace)
    return namespace.get("result")
'''

OPERATIONS = frozenset(_OPS)


def build_script(op: str, args: Dict[str, Any], nonce: str) -> str:
    """The full script for ``op``. ``args`` must be JSON-serializable."""
    body = _OPS[op]
    if not nonce.isalnum():
        raise ValueError("nonce must be alphanumeric")
    # The arguments go in LAST: a replacement after them could rewrite text
    # inside the argument literal (an argument containing "__NONCE__").
    prelude = PRELUDE.replace("__MARKER__", MARKER).replace("__NONCE__", repr(nonce))
    prelude = prelude.replace("__ARGS__", repr(json.dumps(args, default=str)), 1)
    return prelude + body + EPILOGUE


__all__ = ["MARKER", "OPERATIONS", "build_script"]
