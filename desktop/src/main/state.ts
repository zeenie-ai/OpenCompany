/** Small persisted shell state (`desktop-state.json`). */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

export interface DesktopState {
  /** Last port the backend was served on; reused so the SPA origin (and its localStorage) stays stable. */
  port?: number;
  lastAppVersion?: string;
  lastProvisionStamp?: string;
  /** The page's last theme colour ('#rrggbb'); the main window opens in it, so a light theme does not flash dark. */
  windowBackground?: string;
}

/** The form Electron reports a theme colour in. */
const HEX_COLOR = /^#[0-9a-f]{6}$/i;

export function loadState(file: string): DesktopState {
  try {
    if (!existsSync(file)) return {};
    const parsed = JSON.parse(readFileSync(file, "utf-8")) as DesktopState;
    if (!parsed || typeof parsed !== "object") return {};
    // Anything else stored there is dropped, so the window opens in its default colour.
    if (typeof parsed.windowBackground !== "string" || !HEX_COLOR.test(parsed.windowBackground)) delete parsed.windowBackground;
    return parsed;
  } catch {
    return {};
  }
}

export function saveState(file: string, state: DesktopState): void {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, JSON.stringify(state, null, 2) + "\n", "utf-8");
}
