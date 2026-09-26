import { describe, expect, it } from 'vitest';
import { browserModifiers, browserPoint, decodeBrowserFrame } from '../protocol';

const metadata = { seq: 7, device_width: 1280, device_height: 720, page_scale_factor: 1, offset_top: 0 };
function envelope(header = metadata): ArrayBuffer {
  const json = new TextEncoder().encode(JSON.stringify(header));
  const result = new Uint8Array(4 + json.length + 3);
  result.set([1, 1]); new DataView(result.buffer).setUint16(2, json.length, false);
  result.set(json, 4); result.set([255, 216, 255], 4 + json.length);
  return result.buffer;
}

describe('browser frame protocol', () => {
  it('reads a big-endian JSON header and separates the JPEG bytes', () => {
    const result = decodeBrowserFrame(envelope());
    expect(result.header).toEqual(metadata);
    expect(result.jpeg.size).toBe(3);
    expect(result.jpeg.type).toBe('image/jpeg');
  });
  it('rejects unsupported versions, truncated headers and invalid dimensions', () => {
    const version = envelope(); new Uint8Array(version)[0] = 2;
    expect(() => decodeBrowserFrame(version)).toThrow('Unsupported');
    expect(() => decodeBrowserFrame(envelope().slice(0, 7))).toThrow('Incomplete');
    expect(() => decodeBrowserFrame(envelope({ ...metadata, device_width: 0 }))).toThrow('Invalid');
  });
  it('maps the center of a letterboxed image to viewport CSS pixels', () => {
    expect(browserPoint(200, 200, 400, 400, 640, 360, metadata)).toEqual({ x: 640, y: 360 });
    expect(browserPoint(200, 20, 400, 400, 640, 360, metadata)).toBeNull();
  });
  it('maps pillarboxing and adjusts for page scale and browser top offset', () => {
    expect(browserPoint(400, 200, 800, 400, 640, 640, { ...metadata, device_height: 1280, page_scale_factor: 2, offset_top: 20 })).toEqual({ x: 320, y: 310 });
    expect(browserPoint(10, 200, 800, 400, 640, 640, metadata)).toBeNull();
  });
  it('uses CDP modifier bits', () => {
    expect(browserModifiers({ altKey: true, ctrlKey: true, metaKey: false, shiftKey: true })).toBe(11);
  });
});
