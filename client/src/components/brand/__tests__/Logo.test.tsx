import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OcLogo, OcMark } from '../Logo';
import { resetLogoIntroForTests } from '../logoIntro';
import { installWaapiStub } from '../../../test/waapi';

function gradientRefs(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('[stroke]'))
    .map((el) => /^url\(#(.+)\)$/.exec(el.getAttribute('stroke') ?? '')?.[1])
    .filter((id): id is string => Boolean(id));
}

describe('OcLogo', () => {
  it('reads as one "OpenCompany" label with the wordmark shown', () => {
    const { container } = render(<OcLogo />);
    expect(container.textContent).toBe('OpenCompany');
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('labels the mark itself when the wordmark is hidden', () => {
    render(<OcLogo wordmark={false} />);
    expect(screen.getByRole('img', { name: 'OpenCompany' })).toBeInTheDocument();
  });

  it.each([
    ['sidebar', 34, 23],
    ['header', 29, 20],
    ['settings', 23, 16],
  ] as const)('%s size draws the mark at %dx%d', (size, width, height) => {
    const { container } = render(<OcLogo size={size} />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveAttribute('width', String(width));
    expect(svg).toHaveAttribute('height', String(height));
    expect(svg).toHaveAttribute('viewBox', '0 0 140 96');
  });

  it('gives every instance its own gradients, each resolvable in the document', () => {
    const { container } = render(
      <>
        <OcLogo />
        <OcLogo size="header" />
      </>,
    );
    const refs = gradientRefs(container);
    expect(refs).toHaveLength(4);
    expect(new Set(refs).size).toBe(4);
    for (const id of refs) {
      expect(id).toMatch(/^[a-zA-Z0-9_-]+$/);
      // By id alone: selector engines disagree on camelCase SVG type selectors.
      expect(container.querySelector(`[id="${id}"]`)?.tagName.toLowerCase()).toBe('lineargradient');
    }
  });

  it('shares the mark with the static OcMark, whose gradients stay unique too', () => {
    const { container } = render(
      <>
        <OcMark />
        <OcLogo />
      </>,
    );
    const refs = gradientRefs(container);
    expect(refs).toHaveLength(4);
    expect(new Set(refs).size).toBe(4);
    const mark = container.querySelector('svg')!;
    expect(mark).toHaveAttribute('aria-hidden', 'true');
    expect(mark).not.toHaveAttribute('width');
  });

  it('keeps the handoff paint order: C arc, ring, core, then the two end nodes', () => {
    const { container } = render(<OcLogo />);
    const shapes = Array.from(container.querySelectorAll('svg > path, svg > circle'));
    expect(shapes.map((el) => el.tagName.toLowerCase())).toEqual(['path', 'circle', 'circle', 'circle', 'circle']);
    expect(shapes[0]).toHaveAttribute('d', 'M 113.8 28.2 A 28 28 0 1 0 113.8 67.8');
  });
});

describe('OcLogo motion', () => {
  let waapi: ReturnType<typeof installWaapiStub>;

  beforeEach(() => {
    waapi = installWaapiStub();
    resetLogoIntroForTests();
  });

  afterEach(() => {
    waapi.restore();
  });

  it('plays the intro once per page load, on the mark and the wordmark', () => {
    const first = render(<OcLogo intro />);
    const svg = first.container.querySelector('svg')!;
    const wordmark = first.container.querySelector('svg + span');
    const targets = waapi.calls.map((c) => c.target);
    // Ring, C arc, two nodes, wordmark.
    expect(waapi.calls).toHaveLength(5);
    expect(targets).toContain(wordmark);
    expect(targets.every((t) => svg.contains(t) || t === wordmark)).toBe(true);
    const draw = waapi.calls.find((c) => c.target.tagName.toLowerCase() === 'path');
    expect(draw?.keyframes).toEqual([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }]);

    render(<OcLogo intro />);
    expect(waapi.calls).toHaveLength(5);
  });

  it('does not animate without intro', () => {
    render(<OcLogo />);
    expect(waapi.calls).toHaveLength(0);
  });

  it('pulses the nodes and spins the ring when the nonce changes', () => {
    const { rerender } = render(<OcLogo pulseNonce={0} />);
    expect(waapi.calls).toHaveLength(0);
    rerender(<OcLogo pulseNonce={1} />);
    expect(waapi.calls).toHaveLength(3);
    expect(waapi.calls.every((c) => c.options.fill === 'none')).toBe(true);
    rerender(<OcLogo pulseNonce={1} />);
    expect(waapi.calls).toHaveLength(3);
    rerender(<OcLogo pulseNonce={2} />);
    expect(waapi.calls).toHaveLength(6);
  });
});
