/**
 * The Disconnect confirmation gives focus back to the button that opened it:
 * it has no trigger of its own for Radix to return to.
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { ConfirmDisconnect } from '../ConfirmDisconnect';

function Host({ onConfirm }: { onConfirm: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Disconnect
      </button>
      <ConfirmDisconnect name="OpenAI" open={open} onOpenChange={setOpen} onConfirm={onConfirm} />
    </>
  );
}

describe('ConfirmDisconnect', () => {
  it('gives focus back to its opener when the owner keeps it', async () => {
    const onConfirm = vi.fn();
    render(<Host onConfirm={onConfirm} />);
    const opener = screen.getByRole('button', { name: 'Disconnect' });
    opener.focus();
    fireEvent.click(opener);
    const keep = await screen.findByRole('button', { name: 'Keep it' });
    await waitFor(() => expect(keep).toHaveFocus());
    fireEvent.click(keep);
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
