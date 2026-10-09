/**
 * Home's pill toast springs in and fades out through the `pill-toaster`
 * class (index.css), which the shared sonner wrapper must keep beside its
 * own `toaster group`.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { toast } from 'sonner';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { PillToaster, pillToast } from '../ui/pillToast';

afterEach(() => {
  toast.dismiss();
  cleanup();
});

describe('PillToaster', () => {
  it('marks its list for the pill motion and keeps the shared classes', async () => {
    render(
      <ThemeProvider>
        <PillToaster />
      </ThemeProvider>,
    );
    await act(async () => {
      pillToast('Maya was deleted');
    });
    await waitFor(() => expect(document.querySelector('[data-sonner-toaster]')).not.toBeNull());
    expect(document.querySelector('[data-sonner-toaster]')).toHaveClass('pill-toaster', 'toaster', 'group');
  });
});
