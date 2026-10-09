import { describe, expect, it } from 'vitest';
import { browserModifiers, browserPoint, decodeBrowserFrame, framePosition, parseAgentAction } from '../protocol';

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
  it('puts a viewport point back on the picture, the inverse of browserPoint', () => {
    // browserPoint read the middle of this pillarboxed picture as (320, 310).
    expect(framePosition(320, 310, { ...metadata, device_height: 1280, page_scale_factor: 2, offset_top: 20 })).toEqual({ x: 0.5, y: 0.5 });
    expect(framePosition(0, 0, metadata)).toEqual({ x: 0, y: 0 });
  });
  it('reads where the agent acted, and nothing else', () => {
    expect(parseAgentAction({ type: 'agent_action', action: 'click', target_id: 'T1', x: 10, y: 20 }, 1)).toEqual({ id: 1, action: 'click', targetId: 'T1', point: { x: 10, y: 20 } });
    // A field: the cursor goes 55% across (at most 60px in) and 55% down.
    expect(parseAgentAction({ action: 'type', box: [100, 200, 300, 40] }, 2)).toEqual({
      id: 2, action: 'type', box: { left: 100, top: 200, width: 300, height: 40 }, point: { x: 160, y: 222 },
    });
    for (const made_up of [{ action: 'drag', x: 1, y: 1 }, { action: 'click', x: '1', y: 1 }, { action: 'click', x: NaN, y: 1 }, { action: 'select', box: [1, 2, -3, 4] }, { action: 'hover' }]) {
      expect(parseAgentAction(made_up, 3)).toBeNull();
    }
  });
});
