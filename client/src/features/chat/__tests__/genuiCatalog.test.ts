/**
 * The chat catalogue's prop schemas (genui/catalog.ts) against the server's
 * manifest (server/config/chat_genui_catalog.json), which the server checks
 * every spec against: a schema for each of its components, in its order,
 * reading every prop it declares.
 */

import { describe, expect, it } from 'vitest';
import manifest from '../../../../../server/config/chat_genui_catalog.json';
import { CHAT_PROP_SCHEMAS } from '../genui/catalog';
import { allowedParents } from '../genui/registry';

describe('the chat catalogue', () => {
  it('has a schema for each of the manifest’s components, in its order', () => {
    expect(Object.keys(CHAT_PROP_SCHEMAS)).toEqual(Object.keys(manifest.components));
  });

  it('reads every prop the manifest declares', () => {
    for (const [type, component] of Object.entries(manifest.components)) {
      const declared = Object.keys(component.props.properties ?? {}).sort();
      expect(Object.keys(CHAT_PROP_SCHEMAS[type as keyof typeof CHAT_PROP_SCHEMAS].shape).sort(), type).toEqual(declared);
    }
  });

  it('places a component only where a layout may hold it', () => {
    expect(allowedParents('Button')).toEqual([null, 'Stack', 'Row']);
    expect(allowedParents('Select')).toEqual([null, 'Stack', 'Card']);
    expect(allowedParents('SlotPicker')).toEqual([null, 'Stack']);
  });

  it('degrades an odd prop instead of failing the element', () => {
    expect(CHAT_PROP_SCHEMAS.Select.parse({ label: 'Service', options: ['A', 'A', 7, null, 'B'] })).toEqual({
      label: 'Service',
      options: ['A', '7', 'B'],
    });
    expect(CHAT_PROP_SCHEMAS.Callout.parse({ tone: 'neon', text: 'Heads up' })).toEqual({ tone: 'info', text: 'Heads up' });
    expect(CHAT_PROP_SCHEMAS.BarChart.parse({ title: 'Replies', bars: [{ label: 'Mon', value: -3 }, { label: 'Tue', value: '4' }] }).bars).toEqual([
      { label: 'Mon', value: 0 },
      { label: 'Tue', value: 4 },
    ]);
  });
});
