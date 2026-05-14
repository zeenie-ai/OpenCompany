/**
 * Adapter: backend NodeSpec (wire format) → legacy INodeTypeDescription.
 *
 * Wave 6 Phase 2 bridge. Consumers still read INodeTypeDescription today;
 * this adapter lets us switch the source from frontend-owned
 * `nodeDefinitions/*.ts` to the server-emitted NodeSpec without a big-bang
 * refactor of every consumer. The adapter is intentionally thin — it
 * translates JSON-Schema-shaped input properties into the
 * INodeProperties[] array the parameter panel expects.
 *
 * Phase 6 (capstone) inlines this adapter once all consumers read NodeSpec
 * directly; for now it's the seam that makes Phase 3's per-file deletions
 * safe.
 */

import type {
  INodeProperties,
  INodeTypeDescription,
  NodeConnectionType,
} from '../types/INodeProperties';

/** One React Flow handle on a node. Wire mirror of
 *  server/models/node_metadata.NodeHandle (Wave 10.A). */
export interface NodeSpecHandle {
  name: string;
  kind: 'input' | 'output';
  position: 'top' | 'bottom' | 'left' | 'right';
  offset?: string;
  label?: string;
  role?: string;
}

/** Wire shape emitted by GET /api/schemas/nodes/{type}/spec.json. */
export interface NodeSpec {
  type: string;
  displayName: string;
  icon: string;
  group: string[];
  description?: string;
  subtitle?: string;
  version: number;
  /** JSON Schema 7 document describing input parameters (Pydantic-derived). */
  inputs?: JsonSchema;
  /** JSON Schema 7 document describing runtime output (Wave 3 contract). */
  outputs?: JsonSchema;
  credentials?: string[];
  uiHints?: Record<string, unknown>;
  // Wave 10.A — full visual contract (every field is optional on the wire;
  // the backend emits each one only when seeded on that node).
  color?: string;
  componentKind?: 'square' | 'circle' | 'trigger' | 'start' | 'agent' | 'chat' | 'tool' | 'model' | 'generic';
  handles?: NodeSpecHandle[];
  hideOutputHandle?: boolean;
  hideInputHandle?: boolean;
  visibility?: 'all' | 'normal' | 'dev';
}

interface JsonSchema {
  type?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  additionalProperties?: boolean | JsonSchema;
  title?: string;
  description?: string;
  /**
   * Field-group metadata lifted from the Pydantic Params class's
   * ``model_config = ConfigDict(json_schema_extra={"groups": {...}})``.
   *
   * Each field that carries ``json_schema_extra={"group": "<key>"}`` gets
   * nested into a synthetic ``type: "collection"`` INodeProperty at
   * adapter time. ``groups[<key>]`` lets the Params class override the
   * display name and placeholder; missing keys fall back to title-cased
   * defaults.
   */
  groups?: Record<string, GroupMetadata>;
}

interface GroupMetadata {
  display_name?: string;
  placeholder?: string;
}

interface JsonSchemaProperty {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  format?: string;
  anyOf?: JsonSchemaProperty[];
  items?: JsonSchemaProperty;
  additionalProperties?: boolean | JsonSchemaProperty;
  /** Pydantic Field(json_schema_extra={"displayOptions": {...}}) lands
   *  at the property top-level, not under a uiHints wrapper. */
  displayOptions?: Record<string, unknown>;
  placeholder?: string;
  validation?: unknown[];
  /** Editor hints. Either top-level or nested under uiHints - the
   *  adapter reads both locations so Pydantic authors can use either. */
  uiHints?: Record<string, unknown>;
  [key: string]: unknown;
}

type InodeType = INodeProperties['type'];

/**
 * Convert a JSON Schema property → INodeProperties.type value expected
 * by the parameter renderer. Prefers `enum` → `options`, then falls back
 * to the JSON Schema primitive type. Unknown shapes map to `string` so
 * the user sees an editable field rather than a blank slot.
 */
function mapPropertyType(prop: JsonSchemaProperty): InodeType {
  // ``json_schema_extra={"hidden": True}`` on the Pydantic Field hides
  // the parameter from the panel. ParameterRenderer.tsx returns ``null``
  // when ``parameter.type === 'hidden'``. Check both the top-level flag
  // (Pydantic flattens ``json_schema_extra`` keys onto the property) and
  // ``uiHints.hidden`` (nested alternate). Honoured BEFORE enum/editor
  // checks so hidden enum fields don't render as visible dropdowns.
  if ((prop as any).hidden === true || prop.uiHints?.hidden === true) return 'hidden';
  if (prop.enum && prop.enum.length > 0) return 'options';
  // Pydantic Field(json_schema_extra={"editor": "code"}) lands at the
  // property top-level; nested ``uiHints: {...}`` is the alternate
  // location. Check both so authors can use either.
  const editor = (prop as any).editor ?? prop.uiHints?.editor;
  if (editor === 'code') return 'code';
  if (editor === 'json') return 'json';
  const widget = (prop as any).widget ?? prop.uiHints?.widget;
  if (widget === 'file' || prop.format === 'binary') return 'file';
  if (prop.format === 'date-time' || prop.format === 'date') return 'dateTime';
  // anyOf([T, null]) is the Pydantic Optional[T] pattern — take the non-null branch.
  if (prop.anyOf) {
    const nonNull = prop.anyOf.find(b => b.type && b.type !== 'null');
    if (nonNull) return mapPropertyType(nonNull);
  }
  const t = Array.isArray(prop.type) ? prop.type[0] : prop.type;
  switch (t) {
    case 'boolean':
      return 'boolean';
    case 'integer':
    case 'number':
      return 'number';
    case 'array':
      return 'collection';
    case 'object':
      return 'fixedCollection';
    case 'string':
    default:
      return 'string';
  }
}

function toInodeProperty(
  name: string,
  prop: JsonSchemaProperty,
  required: boolean,
): INodeProperties {
  const type = mapPropertyType(prop);
  // For `options`: prefer uiHints.options (richer labels) when supplied,
  // otherwise derive bare {name, value} pairs from JSON Schema enum.
  const richOptions = prop.uiHints?.options as INodeProperties['options'] | undefined;
  const options =
    richOptions ??
    prop.enum?.map(v => ({ name: String(v), value: v as string | number | boolean }));
  const out: INodeProperties = {
    displayName: (prop.uiHints?.displayName as string | undefined) || prop.title || name,
    name,
    type,
    default: prop.default,
    description: prop.description,
    options,
  };
  if (required) out.required = true;
  if (prop.minimum !== undefined || prop.maximum !== undefined) {
    out.typeOptions = {
      ...(prop.minimum !== undefined ? { minValue: prop.minimum } : {}),
      ...(prop.maximum !== undefined ? { maxValue: prop.maximum } : {}),
    };
  }
  // Lift Pydantic Field(json_schema_extra=...) hints. Pydantic merges
  // json_schema_extra into the property top-level, but authors can also
  // nest under ``uiHints: {...}`` for grouping - we read both locations.
  const readHint = <T>(key: string): T | undefined =>
    (prop as any)[key] !== undefined
      ? ((prop as any)[key] as T)
      : (prop.uiHints?.[key] as T | undefined);

  const displayOptions = readHint<INodeProperties['displayOptions']>('displayOptions');
  if (displayOptions) out.displayOptions = displayOptions;
  const placeholder = readHint<string>('placeholder');
  if (placeholder) out.placeholder = placeholder;
  const noDataExpression = readHint<boolean>('noDataExpression');
  if (noDataExpression) out.noDataExpression = noDataExpression;
  const validation = readHint<INodeProperties['validation']>('validation');
  if (validation) out.validation = validation;
  // typeOptions lifts: loadOptionsMethod, password, rows, editor, accept, etc.
  const typeOptionsKeys = [
    'loadOptionsMethod', 'loadOptionsDependsOn',
    'multipleValues', 'multipleValueButtonText',
    'numberStepSize',
    'password', 'rows',
    'editor', 'editorLanguage',
    'dynamicOptions', 'dependsOn',
    'accept',
  ] as const;
  const lifted: Record<string, unknown> = {};
  for (const key of typeOptionsKeys) {
    const v = readHint<unknown>(key);
    if (v !== undefined) lifted[key] = v;
  }
  if (Object.keys(lifted).length > 0) {
    out.typeOptions = { ...out.typeOptions, ...lifted } as INodeProperties['typeOptions'];
  }
  // Stash the group key on the INodeProperty if set — picked up by
  // ``collectGroups`` in the grouping pass. Not consumed by the
  // renderer directly.
  const groupKey = readHint<string>('group');
  if (groupKey) (out as any).__group = groupKey;
  return out;
}

/**
 * Convert `snake_case` / `kebab-case` / `camelCase` to Title Case.
 * Adapter defaults for group display names when the Params class didn't
 * declare explicit metadata.
 */
function titleCase(key: string): string {
  return key
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .split(' ')
    .filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/**
 * Collapse properties marked with ``json_schema_extra={"group": "X"}``
 * into a synthetic ``type: "collection"`` parent. Ungrouped properties
 * keep their original positions; the collection container takes the
 * position of the **first** field with that group key so schema order
 * is preserved.
 *
 * The convention is opt-in — plugins that don't declare groups get the
 * same flat output as before. See docs-internal/plugin_system.md for the
 * convention spec.
 */
function groupProperties(
  properties: INodeProperties[],
  groupsMeta: Record<string, GroupMetadata>,
): INodeProperties[] {
  // First pass: figure out group membership + first-position index per group.
  const groupKeyOf = (p: INodeProperties): string | undefined =>
    (p as any).__group as string | undefined;
  const firstIndex = new Map<string, number>();
  for (let i = 0; i < properties.length; i++) {
    const g = groupKeyOf(properties[i]);
    if (g && !firstIndex.has(g)) firstIndex.set(g, i);
  }
  if (firstIndex.size === 0) {
    // Clean up the private marker before returning.
    return properties.map(p => {
      const { __group, ...rest } = p as any;
      return rest as INodeProperties;
    });
  }

  const collected: Record<string, INodeProperties[]> = {};
  const emitted = new Set<string>();
  const result: INodeProperties[] = [];

  for (let i = 0; i < properties.length; i++) {
    const p = properties[i];
    const g = groupKeyOf(p);
    // Strip marker before storing.
    const { __group, ...clean } = p as any;
    const childProp = clean as INodeProperties;

    if (!g) {
      result.push(childProp);
      continue;
    }
    collected[g] = collected[g] || [];
    collected[g].push(childProp);
    if (firstIndex.get(g) === i && !emitted.has(g)) {
      emitted.add(g);
      // Placeholder; filled in after all children are collected.
      result.push({ __groupPlaceholder: g } as unknown as INodeProperties);
    }
  }

  // Second pass: replace placeholders with the collection wrapper.
  return result.map(p => {
    const placeholder = (p as any).__groupPlaceholder as string | undefined;
    if (!placeholder) return p;
    const meta = groupsMeta[placeholder] || {};
    const displayName = meta.display_name || titleCase(placeholder);
    const defaultPlaceholder = `Add ${displayName.replace(/s$/, '') || displayName}`;
    const collection: INodeProperties = {
      displayName,
      name: placeholder,
      type: 'collection',
      placeholder: meta.placeholder || defaultPlaceholder,
      default: {},
      options: collected[placeholder],
    };
    return collection;
  });
}

/**
 * Derive the INodeTypeDescription.inputs/outputs handle shape. When the
 * backend NodeSpec doesn't yet carry handle configs (Phase 1/2 stub),
 * default to a single 'main' handle — matches the legacy nodeDefinitions
 * default and keeps React Flow happy.
 */
function defaultHandles(): NodeConnectionType[] {
  return ['main' as NodeConnectionType];
}

/**
 * Top-level conversion. Adapter is pure — no side effects, no throws.
 * Missing fields become sensible defaults so the parameter panel always
 * has something to render.
 */
export function nodeSpecToDescription(spec: NodeSpec): INodeTypeDescription {
  const propsObject = spec.inputs?.properties ?? {};
  const requiredSet = new Set(spec.inputs?.required ?? []);
  const flatProperties: INodeProperties[] = Object.entries(propsObject).map(
    ([name, prop]) => toInodeProperty(name, prop, requiredSet.has(name)),
  );
  // Lift ``json_schema_extra={"group": "X"}`` fields into synthetic
  // ``type: "collection"`` containers. Group display metadata comes from
  // ``model_config.json_schema_extra.groups`` on the Pydantic Params
  // class; unspecified groups get a title-cased default.
  const groupsMeta = spec.inputs?.groups ?? {};
  const properties = groupProperties(flatProperties, groupsMeta);

  return {
    displayName: spec.displayName,
    name: spec.type,
    icon: spec.icon,
    group: spec.group ?? [],
    version: spec.version ?? 1,
    subtitle: spec.subtitle,
    description: spec.description ?? '',
    defaults: {
      name: spec.displayName,
    },
    inputs: defaultHandles(),
    outputs: defaultHandles(),
    properties,
    credentials: spec.credentials?.map(name => ({ name })),
    uiHints: spec.uiHints as INodeTypeDescription['uiHints'],
  };
}
