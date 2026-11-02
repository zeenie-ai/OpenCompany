/**
 * The chat's generated UI for json-render: the catalogue and its registry,
 * a guarded component for each of the twelve types (views.tsx drawn through
 * lib/jsonRender/guard.tsx, which reads each element's props through the
 * catalogue's forgiving schema first and holds it to the parents it may
 * appear under). What each layout may hold is the server's manifest
 * (server/config/chat_genui_catalog.json `children`).
 *
 * Defined here, not in catalog.ts, because this module loads only with a
 * generated UI (ChatUi), and json-render and the manifest with it.
 */

import { defineCatalog } from '@json-render/core';
import { defineRegistry, schema } from '@json-render/react';
import { guarded } from '@/lib/jsonRender';
import { catalogOf } from '@/lib/jsonRender/catalog';
import { components } from '../../../../../server/config/chat_genui_catalog.json';
import { CHAT_PROP_SCHEMAS, type ChatComponentType } from './catalog';
import {
  BarChartView,
  ButtonView,
  CalloutView,
  CardView,
  RowView,
  SelectView,
  SlotPickerView,
  StackView,
  StatGridView,
  TextFieldView,
  TextView,
  ToggleView,
} from './views';

/** What each layout may hold: any component, or only these. */
const LAYOUTS: Record<string, string | readonly string[]> = Object.fromEntries(
  Object.entries(components).flatMap(([type, component]) => ('children' in component ? [[type, component.children]] : [])),
);

/** The parents a component may sit under (null: the root). */
export function allowedParents(type: ChatComponentType): (string | null)[] {
  const parents: (string | null)[] = [null];
  for (const [layout, children] of Object.entries(LAYOUTS)) {
    if (children === 'any' || (Array.isArray(children) && children.includes(type))) parents.push(layout);
  }
  return parents;
}

export const chatCatalog = defineCatalog(schema, catalogOf(CHAT_PROP_SCHEMAS, Object.keys(LAYOUTS)));

export const { registry: chatRegistry } = defineRegistry(chatCatalog, {
  components: {
    Stack: guarded('Stack', CHAT_PROP_SCHEMAS.Stack, StackView, { parents: allowedParents('Stack') }),
    Row: guarded('Row', CHAT_PROP_SCHEMAS.Row, RowView, { parents: allowedParents('Row') }),
    Card: guarded('Card', CHAT_PROP_SCHEMAS.Card, CardView, { parents: allowedParents('Card') }),
    SlotPicker: guarded('SlotPicker', CHAT_PROP_SCHEMAS.SlotPicker, SlotPickerView, { parents: allowedParents('SlotPicker') }),
    Select: guarded('Select', CHAT_PROP_SCHEMAS.Select, SelectView, { parents: allowedParents('Select') }),
    Toggle: guarded('Toggle', CHAT_PROP_SCHEMAS.Toggle, ToggleView, { parents: allowedParents('Toggle') }),
    TextField: guarded('TextField', CHAT_PROP_SCHEMAS.TextField, TextFieldView, { parents: allowedParents('TextField') }),
    Text: guarded('Text', CHAT_PROP_SCHEMAS.Text, TextView, { parents: allowedParents('Text') }),
    StatGrid: guarded('StatGrid', CHAT_PROP_SCHEMAS.StatGrid, StatGridView, { parents: allowedParents('StatGrid') }),
    BarChart: guarded('BarChart', CHAT_PROP_SCHEMAS.BarChart, BarChartView, { parents: allowedParents('BarChart') }),
    Callout: guarded('Callout', CHAT_PROP_SCHEMAS.Callout, CalloutView, { parents: allowedParents('Callout') }),
    Button: guarded('Button', CHAT_PROP_SCHEMAS.Button, ButtonView, { parents: allowedParents('Button') }),
  },
});
