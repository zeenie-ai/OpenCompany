import { buildApiUrl } from '@/config/api';

export interface Geometry { width: number; height: number; rotation?: number; revision?: number; [key: string]: unknown }
export interface MobileStatus {
  running: boolean;
  starting?: boolean;
  /** Which phone this is, in the owner's words ("Pixel 7 · local emulator · up to 30 fps"). */
  device?: string;
  diagnostics?: { at: string; event: string; level: string; operation?: string; error_type?: string; code?: string; duration_ms?: number }[];
  geometry?: Geometry | null;
  setup?: string | Record<string, unknown> | null;
  setup_error?: string | null;
  start_error?: string | null;
  setup_progress?: { message?: string; started_at?: number | null; updated_at?: number | null; finished_at?: number | null; events?: { at: number; message: string }[] } | null;
  controller?: string | null;
  epoch: number;
  control_state: string;
  last_task?: Record<string, unknown> | null;
  active?: Record<string, unknown> | null;
  queue?: unknown[];
}
export interface Doctor {
  supported?: boolean; platform?: string; sdk_tools?: boolean; adb?: boolean; emulator?: boolean;
  engine?: boolean; video?: boolean; acceleration?: string; image?: boolean;
  license_url?: string; [key: string]: unknown;
}
export interface Invocation { status?: string; state?: string; error?: unknown; result?: unknown; invocation_id?: string }

export function mobilePath(workflowId: string, nodeId: string): string {
  return `/api/mobile/${encodeURIComponent(workflowId)}/${encodeURIComponent(nodeId)}`;
}

export async function mobileRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(buildApiUrl(path), {
    method: body === undefined ? 'GET' : 'POST', credentials: 'include', signal,
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || data?.success === false) {
    const detail = data?.detail ?? data?.error;
    throw new Error(typeof detail === 'string' ? detail : typeof detail?.message === 'string' ? detail.message : `Mobile request failed (${response.status})`);
  }
  return data as T;
}

export function mobilePoint(x: number, y: number, width: number, height: number, geometry: Geometry) {
  if (![width, height, geometry.width, geometry.height].every((n) => Number.isFinite(n) && n > 0)) return null;
  const scale = Math.min(width / geometry.width, height / geometry.height);
  const px = (x - (width - geometry.width * scale) / 2) / scale;
  const py = (y - (height - geometry.height * scale) / 2) / scale;
  if (px < 0 || py < 0 || px >= geometry.width || py >= geometry.height) return null;
  return { x: Math.floor(px), y: Math.floor(py) };
}
