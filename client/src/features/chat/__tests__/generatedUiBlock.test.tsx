/**
 * A generated interface's dev line (development builds only): its size and
 * an Inspect chip on one row; the chip shows the spec and the patches that
 * build it, and says whether it is open.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { specToPatches } from '@/lib/jsonRender';
import { prepareChatSpec } from '../genui/prepare';
import { GeneratedUiBlock } from '../turns/GeneratedUiBlock';

const SPEC = JSON.parse(readFileSync(join(__dirname, '..', '__fixtures__', 'saturday-booking.spec.json'), 'utf-8'));
const ACTIONS = { ask: vi.fn(), event: vi.fn() };

describe('GeneratedUiBlock dev line', () => {
  it('counts elements and patches beside the Inspect chip', async () => {
    const prepared = prepareChatSpec(SPEC)!;
    const elements = Object.keys(prepared.elements).length;
    const patches = specToPatches(prepared).length;
    render(
      <GeneratedUiBlock
        part={{ partId: 'ui_1', spec: SPEC, state: null, stateRevision: 0, patches: 0 }}
        live={false}
        actions={ACTIONS}
      />,
    );
    const count = screen.getByText(`${elements} elements · ${patches} patches`);
    const chip = screen.getByRole('button', { name: 'Inspect' });
    expect(chip.parentElement).toBe(count.parentElement);
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('tab', { name: 'patches.jsonl' })).toBeInTheDocument();
    fireEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByRole('tab', { name: 'patches.jsonl' })).not.toBeInTheDocument();
  });
});
