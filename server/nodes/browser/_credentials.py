"""Protected configured-form login over the existing private CDP connection.

The ordinary browser-use path never receives these values. All failures are
reported without raw subprocess, DOM, URL, or CDP exception content.
"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit


def canonical_origin(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Unsupported login origin")
    return f"{parts.scheme}://{parts.hostname.lower()}:{parts.port or (443 if parts.scheme == 'https' else 80)}"


async def _page_origin(page: Any) -> str:
    tree = await page.send("Page.getFrameTree", timeout=5)
    return canonical_origin(tree["frameTree"]["frame"]["url"])


async def _element(page: Any, backend_id: int, *, origin: str, kind: str) -> str:
    node = await page.send("DOM.resolveNode", {"backendNodeId": backend_id}, timeout=5)
    object_id = node["object"]["objectId"]
    result = await page.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": """function(origin, kind) {
            const w = this.ownerDocument && this.ownerDocument.defaultView;
            if (!w || w !== w.top || w.location.origin !== origin || !this.isConnected || this.disabled) return false;
            if (kind === 'password') return this.tagName === 'INPUT' && this.type === 'password';
            if (kind === 'username') return this.tagName === 'INPUT' && ['text', 'email', 'tel'].includes(this.type);
            return (this.tagName === 'BUTTON' && this.type === 'submit') || (this.tagName === 'INPUT' && this.type === 'submit');
        }""",
        "arguments": [{"value": origin}, {"value": kind}], "returnByValue": True,
    }, timeout=5)
    if result.get("exceptionDetails") or result.get("result", {}).get("value") is not True:
        raise ValueError("Login target changed")
    return object_id


def _js_origin(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port and port != (443 if parts.scheme == "https" else 80) else "")


async def employee_scope(ctx: Any, auth: Any) -> str | None:
    database = getattr(auth, "database", None)
    if ctx.workflow_id and getattr(database, "engine", None) is not None:
        from services.employees.store import get_by_workflow
        employee = await get_by_workflow(database, ctx.workflow_id)
        if employee is not None and employee.owner_id == (ctx.user_id or "owner"):
            return employee.id
    return None


async def scoped_bindings(ctx: Any, auth: Any, profile_id: str) -> list[dict]:
    from services.credentials.sources import CredentialSources
    employee = await employee_scope(ctx, auth)
    allowed = []
    for binding in await CredentialSources(auth.database).list_browser(ctx.user_id or "owner"):
        if binding.get("profile_id") and binding["profile_id"] != profile_id:
            continue
        if binding.get("workflow_id") and binding["workflow_id"] != ctx.workflow_id:
            continue
        if binding.get("employee_id") and binding["employee_id"] != employee:
            continue
        allowed.append({"id": binding["id"], "label": binding["label"], "origin": binding["origin"], "fields": ["username", "password"]})
    return allowed


async def fill_credentials(ctx: Any, runtime: Any, call: Any, *, timeout: float) -> dict:
    from core.container import container
    from services.browser_owners import set_sensitive
    from services.plugin.deps import get_database

    controller = runtime.controller
    task_id = str(ctx.raw.get("_browser_task_id") or ctx.execution_id or "")
    if controller.task_id is not None and controller.task_id != task_id:
        return {"success": False, "error_type": "BrowserBusy", "error": "This profile belongs to another task."}
    page = None
    values = None
    gated = False
    try:
        if not call.credential_binding_id or not all((call.username_ref, call.password_ref, call.submit_ref)):
            raise ValueError("A configured binding and current login references are required")
        auth = container.auth_service()
        employee = await employee_scope(ctx, auth)
        binding = await auth.get_browser_credential_binding(call.credential_binding_id, ctx.user_id or "owner", profile_id=runtime.profile.id, workflow_id=ctx.workflow_id, employee_id=employee)
        if not binding.get("success_path") and not binding.get("success_selector"):
            raise ValueError("Configure an authenticated success path or selector before automatic login")
        target = controller.active_target_id
        refs = controller.refs.get(target or "", {})
        ids = [refs.get(ref) for ref in (call.username_ref, call.password_ref, call.submit_ref)]
        if not target or any(value is None for value in ids):
            raise ValueError("Take a fresh login snapshot")
        page = await runtime.page_session(target)
        origin = canonical_origin(binding["origin"])
        if await _page_origin(page) != origin:
            raise ValueError("This binding is not approved for the current website")
        initial_tree = await page.send("Page.getFrameTree", timeout=5)
        initial_loader = initial_tree["frameTree"]["frame"].get("loaderId")
        revision = controller.revision
        await set_sensitive(get_database(), runtime.profile.id, controller.task_id, True)
        await controller.set_sensitive_login(True)
        gated = True
        runtime.chrome.sensitive_login = True
        hub = getattr(runtime, "hub", None)
        if hub is not None:
            # Listener broadcasting is best effort; this privacy barrier is not.
            await hub.sensitive_barrier(True)
        webmcp = getattr(runtime, "webmcp", None)
        if hasattr(webmcp, "sensitive_barrier"):
            await webmcp.sensitive_barrier(True)
        if not await runtime.cli.suspend_for_credentials():
            raise ValueError("Browser daemon suspension could not be confirmed")
        js_origin = _js_origin(binding["origin"])
        objects = [await _element(page, int(node), origin=js_origin, kind=kind) for node, kind in zip(ids, ("username", "password", "submit"))]
        values = await auth.resolve_browser_credentials(call.credential_binding_id, ctx.user_id or "owner", profile_id=runtime.profile.id, workflow_id=ctx.workflow_id, employee_id=employee)
        if controller.active_target_id != target or controller.revision != revision + 1 or await _page_origin(page) != origin or controller._user_blocks_agent():
            raise ValueError("Browser control or login target changed during authorization")
        authorized_tree = await page.send("Page.getFrameTree", timeout=5)
        if initial_loader and authorized_tree["frameTree"]["frame"].get("loaderId") != initial_loader:
            raise ValueError("The login document changed during authorization")
        # Resolve and validate the same nodes again after an interactive CLI grant.
        objects = [await _element(page, int(node), origin=js_origin, kind=kind) for node, kind in zip(ids, ("username", "password", "submit"))]
        for object_id, field in zip(objects[:2], ("username", "password")):
            result = await page.send("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": """function(value, origin, kind) {
                    if (!this.isConnected || this.disabled || this.tagName !== 'INPUT' || this.ownerDocument.defaultView.location.origin !== origin) return false;
                    if (kind === 'password' ? this.type !== 'password' : !['text', 'email', 'tel'].includes(this.type)) return false;
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(this, value);
                    this.dispatchEvent(new Event('input', {bubbles:true}));
                    this.dispatchEvent(new Event('change', {bubbles:true}));
                    return this.value === value;
                }""", "arguments": [{"value": values[field]}, {"value": js_origin}, {"value": field}], "returnByValue": True,
            }, timeout=5)
            if result.get("exceptionDetails") or result.get("result", {}).get("value") is not True:
                raise ValueError("Login fill was not confirmed")
        if controller.active_target_id != target or controller._user_blocks_agent() or await _page_origin(page) != origin:
            raise ValueError("Browser target changed before submission")
        submission_tree = await page.send("Page.getFrameTree", timeout=5)
        if initial_loader and submission_tree["frameTree"]["frame"].get("loaderId") != initial_loader:
            raise ValueError("The login document changed before submission")
        objects[2] = await _element(page, int(ids[2]), origin=js_origin, kind="submit")
        submitted = await page.send("Runtime.callFunctionOn", {
            "objectId": objects[2], "functionDeclaration": """function(origin) {
                if (!this.isConnected || this.disabled || this.ownerDocument.defaultView.location.origin !== origin) return false;
                if (!((this.tagName === 'BUTTON' && this.type === 'submit') || (this.tagName === 'INPUT' && this.type === 'submit'))) return false;
                this.click(); return true;
            }""", "arguments": [{"value": js_origin}], "returnByValue": True,
        }, timeout=5)
        if submitted.get("exceptionDetails") or submitted.get("result", {}).get("value") is not True:
            raise ValueError("Login submission was not confirmed")
        deadline = asyncio.get_running_loop().time() + max(1, min(timeout, 45))
        success_origin = canonical_origin(binding.get("success_origin") or binding["origin"])
        while asyncio.get_running_loop().time() < deadline:
            if controller.active_target_id != target or controller._user_blocks_agent():
                break
            result = await page.send("Runtime.evaluate", {
                "expression": "(() => ({origin:location.origin,path:location.pathname,password:!!document.querySelector('input[type=password]')}))()", "returnByValue": True,
            }, timeout=5)
            facts = result.get("result", {}).get("value") or {}
            accepted = canonical_origin(facts.get("origin", "")) == success_origin and not facts.get("password")
            if accepted:
                current_tree = await page.send("Page.getFrameTree", timeout=5)
                changed_document = bool(initial_loader and current_tree["frameTree"]["frame"].get("loaderId") != initial_loader)
                if not changed_document:
                    # SPA success cues alone must not expose the filled inputs.
                    for object_id in objects[:2]:
                        absent = await page.send("Runtime.callFunctionOn", {"objectId": object_id,
                            "functionDeclaration": "function() { return !this.isConnected || this.value === ''; }", "returnByValue": True}, timeout=5)
                        accepted = accepted and absent.get("result", {}).get("value") is True
            if binding.get("success_path"):
                accepted = accepted and facts.get("path") == binding["success_path"]
            if binding.get("success_selector"):
                import json
                cue = await page.send("Runtime.evaluate", {"expression": f"!!document.querySelector({json.dumps(binding['success_selector'])})", "returnByValue": True}, timeout=5)
                accepted = accepted and cue.get("result", {}).get("value") is True
            if accepted:
                # The harness logs URLs; do not resume it on a credential-bearing
                # redirect or a page that reflects a password into DOM metadata.
                document = await page.send("Runtime.evaluate", {"expression": "document.documentElement", "returnByValue": False}, timeout=5)
                clean = await page.send("Runtime.callFunctionOn", {"objectId": document["result"]["objectId"],
                    "functionDeclaration": """function(password) {
                        return ![location.href, this.outerHTML, this.innerText || ''].some(text =>
                            text.includes(password) || text.includes(encodeURIComponent(password)));
                    }""", "arguments": [{"value": values["password"]}], "returnByValue": True}, timeout=5)
                accepted = clean.get("result", {}).get("value") is True
            if accepted:
                if hasattr(webmcp, "sensitive_barrier"):
                    await webmcp.sensitive_barrier(False)
                await set_sensitive(get_database(), runtime.profile.id, controller.task_id, False)
                await controller.set_sensitive_login(False)
                runtime.chrome.sensitive_login = False
                controller.refs.clear()
                controller.needs_observation = True
                return {"success": True}
            await asyncio.sleep(0.25)
        raise ValueError("Login completion was not confirmed")
    except asyncio.CancelledError:
        raise
    except Exception:
        # CDP/page errors may contain credential-bearing DOM or URLs.
        return {"success": False, "error_type": "login_required", "error": "Configured login could not be confirmed. Browser observations remain paused. Close and reopen this browser, then finish login manually." if gated else "Configured login is unavailable for this page. Finish login manually or check the saved binding and current references."}
    finally:
        values = None
        if page is not None:
            await page.detach()
