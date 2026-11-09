import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  pauseWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
  pillToast: vi.fn(),
}));
vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => mocks,
}));
vi.mock('../../ui/pillToast', () => ({ pillToast: mocks.pillToast }));

import { normalizeWorkflowControlStatus, type WorkflowControlStatus } from '@/contexts/WebSocketContext';
import { parseEmployee } from '../../data/schemas';
import { useHomeStore } from '../../state/homeStore';
import { useTakeOver } from '../takeover';

const status = (state: string, revision: number) => normalizeWorkflowControlStatus({ generation: 1, state, revision }, 'w1');

/** Maya as her summary shows her: running at revision 4 unless told otherwise. */
const maya = (state = 'running', revision = 4) =>
  parseEmployee({
    workflow_id: 'w1', name: 'Maya', role: 'Receptionist', status: state === 'running' ? 'working' : 'paused',
    control: status(state, revision),
  })!;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** The hook over a screen whose claim succeeds; `rerender` swaps Maya's summary. */
function takeOverHook() {
  const giveBack = vi.fn();
  const screen = { claim: vi.fn(async () => giveBack) };
  const hook = renderHook(({ employee }) => useTakeOver(employee, () => screen), { initialProps: { employee: maya() } });
  return { ...hook, giveBack, screen };
}

beforeEach(() => {
  vi.clearAllMocks();
  useHomeStore.setState({ takeovers: {} });
  mocks.pauseWorkflow.mockReset().mockResolvedValue(status('paused', 5));
  mocks.resumeWorkflow.mockReset().mockResolvedValue(status('running', 6));
});

describe('Hand back lifecycle recovery', () => {
  it.each(['workflow_control_superseded', 'transport'])(
    'releases the screen after Resume ends with %s', async (error) => {
      const employee = parseEmployee({
        workflow_id: 'w1', name: 'Maya', role: 'Receptionist', status: 'working',
        control: normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 4 }, 'w1'),
      })!;
      const giveBack = vi.fn();
      const screen = { claim: vi.fn(async () => giveBack) };
      mocks.resumeWorkflow.mockRejectedValue(new Error(error));
      const { result } = renderHook(() => useTakeOver(employee, () => screen));
      await act(async () => result.current.takeOver());
      expect(result.current.active).toBe(true);
      mocks.pillToast.mockClear();

      await act(async () => result.current.handBack());

      // The revision the Stop answered (paused at 5), not the summary's older 4.
      expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 5);
      expect(giveBack).toHaveBeenCalledOnce();
      expect(result.current).toMatchObject({ active: false, busy: false });
      expect(useHomeStore.getState().takeovers).toEqual({});
      if (error === 'workflow_control_superseded') {
        expect(mocks.pillToast).not.toHaveBeenCalled();
      } else {
        expect(mocks.pillToast).toHaveBeenCalledWith('That did not work. Try again.', { tone: 'error' });
      }
    },
  );
});

describe('Take over', () => {
  it('shows the owner in control before the Stop answers', async () => {
    const stop = deferred<WorkflowControlStatus>();
    mocks.pauseWorkflow.mockReturnValue(stop.promise);
    const { result, giveBack, screen } = takeOverHook();

    let taking!: Promise<void>;
    await act(async () => {
      taking = result.current.takeOver();
    });

    expect(screen.claim.mock.invocationCallOrder[0]).toBeLessThan(mocks.pauseWorkflow.mock.invocationCallOrder[0]);
    expect(mocks.pauseWorkflow).toHaveBeenCalledWith('w1', 4);
    expect(result.current).toMatchObject({ active: true, busy: false });
    expect(useHomeStore.getState().takeovers).toEqual({ w1: { stopped: true, giveBack } });
    expect(mocks.pillToast).toHaveBeenCalledWith('You have control — Maya will wait');

    await act(async () => {
      stop.resolve(status('paused', 6));
      await taking;
    });
    expect(result.current.active).toBe(true);
    expect(giveBack).not.toHaveBeenCalled();
  });

  it.each([
    { error: 'control_revision_conflict', message: 'That changed a moment ago. Try again.' },
    { error: 'workflow_control_superseded', message: null },
  ])('gives the screen back and clears the takeover when the Stop ends with $error', async ({ error, message }) => {
    mocks.pauseWorkflow.mockRejectedValue(new Error(error));
    const { result, giveBack } = takeOverHook();

    await act(async () => result.current.takeOver());

    expect(giveBack).toHaveBeenCalledOnce();
    expect(result.current).toMatchObject({ active: false, busy: false });
    expect(useHomeStore.getState().takeovers).toEqual({});
    if (message) expect(mocks.pillToast).toHaveBeenLastCalledWith(message, { tone: 'error' });
    else expect(mocks.pillToast).not.toHaveBeenCalledWith(expect.anything(), { tone: 'error' });
  });
});

describe('Hand back', () => {
  it('works while the Stop is still draining, then resumes from its answer', async () => {
    const stop = deferred<WorkflowControlStatus>();
    mocks.pauseWorkflow.mockReturnValue(stop.promise);
    const { result, giveBack } = takeOverHook();
    let taking!: Promise<void>;
    await act(async () => {
      taking = result.current.takeOver();
    });

    let handing!: Promise<void>;
    await act(async () => {
      handing = result.current.handBack();
    });
    // The screen first: the call the employee waits in can return, and the drain end.
    expect(giveBack).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).not.toHaveBeenCalled();
    expect(result.current).toMatchObject({ active: false, busy: true });

    await act(async () => {
      stop.resolve(status('paused', 6));
      await Promise.all([taking, handing]);
    });
    expect(mocks.pauseWorkflow).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 6);
    expect(result.current).toMatchObject({ active: false, busy: false });
    expect(mocks.pillToast).toHaveBeenLastCalledWith('Handed back to Maya');
  });

  it('from pausing, sends the Stop once more and resumes with the revision it answers', async () => {
    // A Stop answers within 10 s even while the drain goes on.
    mocks.pauseWorkflow.mockResolvedValueOnce(status('pausing', 5)).mockResolvedValueOnce(status('paused', 6));
    const { result, giveBack } = takeOverHook();
    await act(async () => result.current.takeOver());

    await act(async () => result.current.handBack());

    expect(mocks.pauseWorkflow.mock.calls).toEqual([['w1', 4], ['w1', 5]]);
    expect(giveBack.mock.invocationCallOrder[0]).toBeLessThan(mocks.pauseWorkflow.mock.invocationCallOrder[1]);
    expect(mocks.resumeWorkflow).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 6);
  });

  it('sends the Stop again for as long as it answers pausing', async () => {
    // Given back, a phone task goes on with its goal, and the drain with it.
    mocks.pauseWorkflow
      .mockResolvedValueOnce(status('pausing', 5))
      .mockResolvedValueOnce(status('pausing', 5))
      .mockResolvedValueOnce(status('paused', 6));
    const { result } = takeOverHook();
    await act(async () => result.current.takeOver());

    await act(async () => result.current.handBack());

    expect(mocks.pauseWorkflow.mock.calls).toEqual([['w1', 4], ['w1', 5], ['w1', 5]]);
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 6);
  });

  it('from paused, resumes with the newest revision and sends no Stop', async () => {
    mocks.pauseWorkflow.mockResolvedValueOnce(status('pausing', 5));
    const { result, rerender } = takeOverHook();
    await act(async () => result.current.takeOver());
    // The drain ended on its own, and the summary has caught up.
    rerender({ employee: maya('paused', 6) });

    await act(async () => result.current.handBack());

    expect(mocks.pauseWorkflow).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 6);
  });

  it('stays quiet when the Stop fails after Hand back', async () => {
    const stop = deferred<WorkflowControlStatus>();
    mocks.pauseWorkflow.mockReturnValue(stop.promise);
    const { result, giveBack } = takeOverHook();
    let taking!: Promise<void>;
    await act(async () => {
      taking = result.current.takeOver();
    });
    let handing!: Promise<void>;
    await act(async () => {
      handing = result.current.handBack();
    });

    await act(async () => {
      stop.reject(new Error('transport'));
      await Promise.all([taking, handing]);
    });

    // The screen given back once and no error: Hand back goes on from the summary.
    expect(giveBack).toHaveBeenCalledOnce();
    expect(mocks.pillToast).not.toHaveBeenCalledWith(expect.anything(), { tone: 'error' });
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 4);
    expect(mocks.pillToast).toHaveBeenLastCalledWith('Handed back to Maya');
  });
});
