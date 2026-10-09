"""Persistent optional driver process. Stdio is private to the device broker."""

from __future__ import annotations
import base64
import json
import re
import sys
from io import BytesIO
from urllib.parse import urlsplit

WIRE = sys.stdout


class AndroidDriver:
    def __init__(self, serial: str, source: str):
        if not re.fullmatch(r"emulator-\d+", serial):
            raise ValueError("Only the explicitly managed emulator is supported")
        sys.path.insert(0, source)
        import uiautomator2 as u2

        self.device = u2.connect(serial)
        if "dev.mobile.maestro" in self.device.app_list():
            raise ValueError("Maestro's helper conflicts with mobile automation. Remove it manually before connecting.")

    def geometry(self):
        info = self.device.info
        width, height = info.get("displayWidth"), info.get("displayHeight")
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            width, height = self.device.window_size()
        return {"width": width, "height": height, "rotation": info.get("displayRotation", 0)}

    def call(self, op: str, p: dict):
        d = self.device
        geometry = self.geometry() if op in {"geometry", "observe", "tap", "swipe", "touch"} else None
        if op in {"tap", "swipe", "touch"}:
            if p.pop("geometry", None) != geometry:
                raise ValueError("Screen changed; refresh before interacting")
            for key, limit in (
                ("x", geometry["width"]),
                ("y", geometry["height"]),
                ("end_x", geometry["width"]),
                ("end_y", geometry["height"]),
            ):
                if key in p and not 0 <= float(p[key]) < limit:
                    raise ValueError("Coordinates outside device screen")
        if op == "geometry":
            return geometry
        if op == "observe":
            from minitap.mobile_use.clients.ui_automator_client import _parse_hierarchy_xml_to_elements

            output = BytesIO()
            d.screenshot().save(output, "PNG")
            elements = _parse_hierarchy_xml_to_elements(d.dump_hierarchy())
            return {
                "base64": base64.b64encode(output.getvalue()).decode(),
                "elements": elements,
                "width": geometry["width"],
                "height": geometry["height"],
                "platform": "android",
                "geometry": geometry,
            }
        if op == "screenshot":
            output = BytesIO()
            d.screenshot().save(output, "PNG")
            return {"base64": base64.b64encode(output.getvalue()).decode()}
        if op == "date":
            return d.shell(["date"]).output
        if op == "packages":
            return "\n".join(sorted(d.app_list()))
        if op == "foreground":
            return d.app_current().get("package", "")
        if op == "tap":
            duration = min(max(float(p.get("duration", 0)), 0), 3000)
            if duration:
                d.long_click(p["x"], p["y"], duration / 1000)
            else:
                d.click(p["x"], p["y"])
        elif op == "swipe":
            d.swipe(p["x"], p["y"], p["end_x"], p["end_y"], min(max(float(p.get("duration", 400)), 50), 3000) / 1000)
        elif op == "touch":
            action = p.get("action")
            if action not in {"down", "move", "up"}:
                raise ValueError("Invalid touch action")
            getattr(d.touch, action)(p["x"], p["y"])
        elif op == "text":
            text = str(p.get("text", ""))
            if len(text) > 10000:
                raise ValueError("Text too long")
            d.send_keys(text, clear=False)
        elif op == "erase":
            count = min(max(int(p.get("count", 50)), 1), 1000)
            d.shell(["input", "keyevent", *(["KEYCODE_DEL"] * count)])
        elif op == "key":
            key = p.get("key")
            if key not in {"home", "back", "enter", "recent", "power"}:
                raise ValueError("Unsupported key")
            d.press(key)
        elif op in {"launch", "terminate"}:
            package = p.get("package") or d.app_current().get("package")
            if not package or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]+", package):
                raise ValueError("Invalid package")
            if op == "launch":
                d.app_start(package)
            else:
                d.app_stop(package)
        elif op == "url":
            url = str(p.get("url", ""))
            if urlsplit(url).scheme not in {"https", "http"} or len(url) > 4096:
                raise ValueError("Unsupported URL")
            d.shell(["am", "start", "-a", "android.intent.action.VIEW", "-d", url])
        elif op == "rotate":
            orientation = p.get("orientation")
            if orientation not in {"natural", "left", "right"}:
                raise ValueError("Invalid orientation")
            d.set_orientation(orientation)
        else:
            raise ValueError("Unsupported device operation")
        return True


def main():
    sys.stdout = sys.stderr
    config = json.loads(sys.stdin.readline())
    try:
        driver = AndroidDriver(config["serial"], config["source"])
        print(json.dumps({"success": True, "result": driver.geometry()}), file=WIRE, flush=True)
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)[:500]}), file=WIRE, flush=True)
        return
    for line in sys.stdin:
        try:
            request = json.loads(line)
            result = driver.call(request["operation"], request.get("parameters", {}))
            reply = {"success": True, "result": result}
        except Exception as exc:
            reply = {
                "success": False,
                "error": str(exc)[:500],
                "code": "invalid_action" if isinstance(exc, (ValueError, KeyError, TypeError)) else "device_action_failed",
            }
        print(json.dumps(reply), file=WIRE, flush=True)


if __name__ == "__main__":
    main()
