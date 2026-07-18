/**
 * Tests for GetStartedChecklist — the floating Get Started card.
 *
 * The hook contract is exhaustively tested in
 * client/src/hooks/__tests__/useGetStarted.test.ts. This suite full-replace
 * mocks the hook and locks the rendering contract:
 *   - renders null when not visible
 *   - header math ("N of M complete")
 *   - incomplete actionable items with a provided action render as buttons
 *     firing exactly that action; completed rows are non-clickable
 *   - collapse chevron -> pill ("Get started · N/M"); pill click re-expands
 *   - all-complete -> "You're all set!" + Done button dismisses
 *   - X dismisses (with toast)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// --- mocks (hoisted; closures dereference state at call time) ---------------

const dismissMock = vi.fn();
const restoreMock = vi.fn();

const hookState = {
  visible: true,
  items: [] as Array<{ id: string; completed: boolean }>,
  completedCount: 0,
  totalCount: 5,
};

vi.mock('../../../hooks/useGetStarted', () => ({
  useGetStarted: () => ({
    visible: hookState.visible,
    items: hookState.items,
    completedCount: hookState.completedCount,
    totalCount: hookState.totalCount,
    dismiss: dismissMock,
    restore: restoreMock,
  }),
}));

const toastInfoMock = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    info: (...args: unknown[]) => toastInfoMock(...args),
    error: vi.fn(),
    success: vi.fn(),
    message: vi.fn(),
    warning: vi.fn(),
  },
}));

import GetStartedChecklist from '../GetStartedChecklist';
import { GET_STARTED_ITEMS, type GetStartedItemId } from '../getStartedItems';

// --- helpers -----------------------------------------------------------------

function setItems(completedIds: GetStartedItemId[]) {
  hookState.items = GET_STARTED_ITEMS.map((item) => ({
    id: item.id,
    completed: completedIds.includes(item.id),
  }));
  hookState.completedCount = completedIds.length;
  hookState.totalCount = GET_STARTED_ITEMS.length;
}

beforeEach(() => {
  vi.clearAllMocks();
  hookState.visible = true;
  setItems(['setup', 'add-key']); // default: 2 of 5 complete
});

// ---------------------------------------------------------------------------

describe('GetStartedChecklist', () => {
  it('renders nothing when not visible', () => {
    hookState.visible = false;
    const { container } = render(<GetStartedChecklist />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the header with completion math', () => {
    render(<GetStartedChecklist />);
    expect(screen.getByText('Get started')).toBeInTheDocument();
    expect(screen.getByText('2 of 5 complete')).toBeInTheDocument();
    // All five item labels render.
    for (const item of GET_STARTED_ITEMS) {
      expect(screen.getByText(item.label)).toBeInTheDocument();
    }
  });

  it('fires the matching action for an incomplete actionable item', () => {
    const chatAction = vi.fn();
    const buildAction = vi.fn();
    render(
      <GetStartedChecklist
        actions={{ 'chat-example': chatAction, 'build-workflow': buildAction }}
      />,
    );

    fireEvent.click(
      screen.getByRole('button', { name: /Chat with your AI Assistant/ }),
    );
    expect(chatAction).toHaveBeenCalledTimes(1);
    expect(buildAction).not.toHaveBeenCalled();
  });

  it('renders a completed item as a non-clickable row even when an action is provided', () => {
    setItems(['setup', 'chat-example']);
    const chatAction = vi.fn();
    render(<GetStartedChecklist actions={{ 'chat-example': chatAction }} />);

    const label = screen.getByText('Chat with your AI Assistant');
    expect(label.closest('button')).toBeNull();
  });

  it('renders an incomplete actionable item without a provided action as a plain row', () => {
    render(<GetStartedChecklist />); // no actions prop at all
    const label = screen.getByText('Add your AI key');
    expect(label.closest('button')).toBeNull();
  });

  it('collapses to a pill showing "Get started · N/M" and re-expands on pill click', () => {
    render(<GetStartedChecklist />);

    fireEvent.click(screen.getByLabelText('Collapse checklist'));

    // Collapsed view is a single pill button.
    expect(screen.queryByText('2 of 5 complete')).not.toBeInTheDocument();
    const pill = screen.getByRole('button');
    expect(pill).toHaveTextContent('Get started · 2/5');

    fireEvent.click(pill);
    expect(screen.getByText('2 of 5 complete')).toBeInTheDocument();
  });

  it('shows "You\'re all set!" and a Done button that dismisses when everything is complete', () => {
    setItems(['setup', 'add-key', 'chat-example', 'build-workflow', 'try-theme']);
    render(<GetStartedChecklist />);

    expect(screen.getByText("You're all set!")).toBeInTheDocument();
    expect(screen.getByText('5 of 5 complete')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Done/ }));
    expect(dismissMock).toHaveBeenCalledTimes(1);
  });

  it('does not show the Done affordance while incomplete', () => {
    render(<GetStartedChecklist />);
    expect(screen.queryByText("You're all set!")).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Done/ })).not.toBeInTheDocument();
  });

  it('X dismisses the checklist and toasts', () => {
    render(<GetStartedChecklist />);
    fireEvent.click(screen.getByLabelText('Dismiss checklist'));
    expect(dismissMock).toHaveBeenCalledTimes(1);
    expect(toastInfoMock).toHaveBeenCalledTimes(1);
  });
});
