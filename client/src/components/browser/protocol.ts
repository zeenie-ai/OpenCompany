/** Versioned browser stream envelope. The bytes after the UTF-8 JSON header are JPEG. */
export interface BrowserFrameHeader {
  seq: number;
  target_id?: string;
  device_width: number;
  device_height: number;
  page_scale_factor?: number;
  offset_top?: number;
}

export function decodeBrowserFrame(buffer: ArrayBuffer): { header: BrowserFrameHeader; jpeg: Blob } {
  if (buffer.byteLength < 5) throw new Error('Incomplete browser frame');
  const view = new DataView(buffer);
  if (view.getUint8(0) !== 1 || view.getUint8(1) !== 1) throw new Error('Unsupported browser stream version');
  const end = 4 + view.getUint16(2, false);
  if (end >= buffer.byteLength) throw new Error('Incomplete browser frame payload');
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, end - 4))) as BrowserFrameHeader;
  if (!Number.isFinite(header.seq) || !Number.isFinite(header.device_width) || !Number.isFinite(header.device_height) || !(header.device_width > 0) || !(header.device_height > 0)) {
    throw new Error('Invalid browser frame metadata');
  }
  return { header, jpeg: new Blob([new Uint8Array(buffer, end)], { type: 'image/jpeg' }) };
}

/** Map the aspect-fitted image into CDP viewport CSS coordinates; letterbox clicks are ignored. */
export function browserPoint(
  x: number, y: number, width: number, height: number,
  imageWidth: number, imageHeight: number, header: BrowserFrameHeader,
): { x: number; y: number } | null {
  if (![width, height, imageWidth, imageHeight].every((n) => n > 0)) return null;
  const scale = Math.min(width / imageWidth, height / imageHeight);
  const drawnWidth = imageWidth * scale;
  const drawnHeight = imageHeight * scale;
  const px = x - (width - drawnWidth) / 2;
  const py = y - (height - drawnHeight) / 2;
  if (px < 0 || py < 0 || px > drawnWidth || py > drawnHeight) return null;
  const pageScale = header.page_scale_factor && header.page_scale_factor > 0 ? header.page_scale_factor : 1;
  return {
    x: (px / drawnWidth * header.device_width) / pageScale,
    y: Math.max(0, (py / drawnHeight * header.device_height - (header.offset_top ?? 0)) / pageScale),
  };
}

export function browserModifiers(event: { altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }): number {
  return (event.altKey ? 1 : 0) | (event.ctrlKey ? 2 : 0) | (event.metaKey ? 4 : 0) | (event.shiftKey ? 8 : 0);
}
