/**
 * The guard, rendered through json-render: props are read through the
 * component's schema with one bad prop degrading only itself, an element
 * that throws disappears alone (json-render's own boundary), a parent rule
 * holds, and the entrance plays once and only for a live UI.
 */

import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { createStateStore, defineCatalog, type Spec } from '@json-render/core';
import { JSONUIProvider, Renderer, defineRegistry, schema } from '@json-render/react';
import { z } from 'zod';
import { installWaapiStub } from '@/test/waapi';
import { LiveUiContext, guarded, parseProps, type GuardedComponentProps } from '../guard';

const Label = z.object({ title: z.string(), tone: z.enum(['calm', 'loud']).optional() });
const Box = z.object({});

function LabelView({ props, enterRef }: GuardedComponentProps<z.output<typeof Label>>) {
  return (
    <p ref={enterRef} data-tone={props.tone ?? 'none'}>
      {props.title}
    </p>
  );
}

function BoxView({ children, enterRef }: GuardedComponentProps<z.output<typeof Box>>) {
  return (
    <div ref={enterRef} data-testid="box">
      {children}
    </div>
  );
}

function Boom(): never {
  throw new Error('boom');
}

const catalog = defineCatalog(schema, {
  components: { Box: { props: Box }, Label: { props: Label }, Boom: { props: Box }, Nested: { props: Label } },
  actions: {},
});

const { registry } = defineRegistry(catalog, {
  components: {
    Box: guarded('Box', Box, BoxView),
    Label: guarded('Label', Label, LabelView),
    Boom: guarded('Boom', Box, Boom),
    Nested: guarded('Nested', Label, LabelView, { parents: ['Box'] }),
  },
});

function show(spec: Spec, live = false) {
  const store = createStateStore(spec.state ?? {});
  const view = render(
    <LiveUiContext.Provider value={live}>
      <JSONUIProvider registry={registry} store={store}>
        <Renderer spec={spec} registry={registry} />
      </JSONUIProvider>
    </LiveUiContext.Provider>,
  );
  return { ...view, store };
}

function box(children: Record<string, { type: string; props: Record<string, unknown> }>, state: Record<string, unknown> = {}): Spec {
  return { root: 'root', state, elements: { root: { type: 'Box', props: {}, children: Object.keys(children) }, ...children } };
}

describe('guarded', () => {
  let errors: MockInstance<typeof console.error>;

  beforeEach(() => {
    errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    errors.mockRestore();
  });

  it('degrades a bad prop to its default and drops an element missing a required one', () => {
    show(
      box({
        good: { type: 'Label', props: { title: 'Hello', tone: 'loud' } },
        odd: { type: 'Label', props: { title: 'Odd', tone: 'purple' } },
        bare: { type: 'Label', props: { tone: 'calm' } },
      }),
    );
    expect(screen.getByText('Hello')).toHaveAttribute('data-tone', 'loud');
    expect(screen.getByText('Odd')).toHaveAttribute('data-tone', 'none');
    expect(screen.getByTestId('box').children).toHaveLength(2);
  });

  it('reads props as json-render resolved them from state', () => {
    const { store } = show(box({ name: { type: 'Label', props: { title: { $state: '/name' } } } }, { name: 'Ana' }));
    expect(screen.getByText('Ana')).toBeInTheDocument();
    act(() => store.set('/name', 'Bo'));
    expect(screen.getByText('Bo')).toBeInTheDocument();
  });

  it('isolates an element that throws', () => {
    show(
      box({
        before: { type: 'Label', props: { title: 'Before' } },
        boom: { type: 'Boom', props: {} },
        after: { type: 'Label', props: { title: 'After' } },
      }),
    );
    expect(screen.getByText('Before')).toBeInTheDocument();
    expect(screen.getByText('After')).toBeInTheDocument();
    expect(errors).toHaveBeenCalled();
  });

  it('holds a component to the parents it may appear under', () => {
    show(box({ inside: { type: 'Nested', props: { title: 'Inside a box' } } }));
    expect(screen.getByText('Inside a box')).toBeInTheDocument();
    show({ root: 'alone', elements: { alone: { type: 'Nested', props: { title: 'At the root' } } } });
    expect(screen.queryByText('At the root')).not.toBeInTheDocument();
  });
});

describe('the entrance', () => {
  let waapi: ReturnType<typeof installWaapiStub>;

  beforeEach(() => {
    waapi = installWaapiStub();
  });

  afterEach(() => {
    waapi.restore();
  });

  it('plays once for each element of a live UI', () => {
    const { store } = show(box({ name: { type: 'Label', props: { title: { $state: '/name' } } } }, { name: 'Ana' }), true);
    expect(waapi.callsFor(screen.getByTestId('box'))).toHaveLength(1);
    expect(waapi.callsFor(screen.getByText('Ana'))).toHaveLength(1);
    expect(waapi.calls[0].options).toMatchObject({ duration: 420 });
    act(() => store.set('/name', 'Bo'));
    expect(waapi.calls).toHaveLength(2);
  });

  it('does not play for a UI shown again from history', () => {
    show(box({ name: { type: 'Label', props: { title: 'Old' } } }), false);
    expect(screen.getByText('Old')).toBeInTheDocument();
    expect(waapi.calls).toHaveLength(0);
  });
});

describe('parseProps', () => {
  it('reads all props at once when they pass, else one at a time', () => {
    expect(parseProps(Label, { title: 'A', tone: 'calm', extra: 1 })).toEqual({ title: 'A', tone: 'calm' });
    expect(parseProps(Label, { title: 'A', tone: 7 })).toEqual({ title: 'A' });
    expect(parseProps(Label, { tone: 'calm' })).toBeNull();
    expect(parseProps(Label, 'not props')).toBeNull();
    expect(parseProps(z.string(), 5)).toBeNull();
  });
});
