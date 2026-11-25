import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const mocks = vi.hoisted(() => ({
  pauseWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
  getWorkflowControlStatus: vi.fn(),
  /** The host's way of saying what happened (Home: the pill toast; the editor: a toast). */
  notify: vi.fn(),
}));
vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => mocks,
}));
// Take over says nothing through Home's toast itself: every message goes to
// `notify` (checked after each test).
vi.mock('../../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { normalizeWorkflowControlStatus, type WorkflowControlStatus } from '@/contexts/WebSocketContext';
import { parseEmployee } from '../../data/schemas';
import { useHomeStore } from '../../state/homeStore';
import { pillToast } from '../../ui/pillToast';
import { useTakeOver } from '../takeover';

const status = (state: string, revision: number) => normalizeWorkflowControlStatus({ generation: 1, state, revision, root_execution_id: 'w1-root' }, 'w1');
const pauseAnswer = (state: 'paused' | 'pausing', revision: number) => ({
  ...status(state, revision), pause_started_revision: state === 'pausing' ? revision : revision - 1,
});

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
  const hook = renderHook(({ employee }) => useTakeOver(employee, 'browser', () => screen, mocks.notify), { initialProps: { employee: maya() } });
  return { ...hook, giveBack, screen };
}

beforeEach(() => {
  vi.clearAllMocks();
  useHomeStore.setState({ takeovers: {}, takeoverBusy: {} });
  mocks.pauseWorkflow.mockReset().mockResolvedValue(pauseAnswer('paused', 5));
  mocks.resumeWorkflow.mockReset().mockResolvedValue(status('running', 6));
  mocks.getWorkflowControlStatus.mockReset();
});

afterEach(() => {
  expect(pillToast).not.toHaveBeenCalled();
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
      const { result } = renderHook(() => useTakeOver(employee, 'browser', () => screen, mocks.notify));
      await act(async () => result.current.takeOver());
      expect(result.current.active).toBe(true);
      mocks.notify.mockClear();

      await act(async () => result.current.handBack());

      // The revision the Stop answered (paused at 5), not the summary's older 4.
      expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 5, 'w1-root');
      expect(giveBack).toHaveBeenCalledOnce();
      expect(result.current).toMatchObject({ active: false, busy: false });
      expect(useHomeStore.getState().takeovers).toEqual({});
      if (error === 'workflow_control_superseded') {
        expect(mocks.notify).not.toHaveBeenCalled();
      } else {
        // The error only: a Resume that failed never says they were handed back.
        expect(mocks.notify.mock.calls).toEqual([['That did not work. Try again.', 'error']]);
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
    expect(mocks.pauseWorkflow).toHaveBeenCalledWith('w1', 4, 'w1-root');
    expect(result.current).toMatchObject({ active: true, screen: 'browser', busy: false });
    expect(useHomeStore.getState().takeovers.w1).toEqual({ screen: 'browser', giveBack, stop: stop.promise });
    expect(useHomeStore.getState().takeovers.w1.stop).toBe(stop.promise);
    expect(mocks.notify).toHaveBeenCalledWith('You have control — Maya will wait', 'success');

    await act(async () => {
      stop.resolve(pauseAnswer('paused', 6));
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
    if (message) expect(mocks.notify).toHaveBeenLastCalledWith(message, 'error');
    else expect(mocks.notify).not.toHaveBeenCalledWith(expect.anything(), 'error');
  });

  it('says a screen that refuses as an error, and stops no one', async () => {
    const screen = { claim: vi.fn(async () => null) };
    const { result } = renderHook(() => useTakeOver(maya(), 'browser', () => screen, mocks.notify));

    await act(async () => result.current.takeOver());

    expect(screen.claim).toHaveBeenCalledOnce();
    expect(mocks.notify.mock.calls).toEqual([['Couldn’t take the screen. Try again.', 'error']]);
    expect(mocks.pauseWorkflow).not.toHaveBeenCalled();
    expect(result.current).toMatchObject({ active: false, screen: null, busy: false });
    expect(useHomeStore.getState().takeovers).toEqual({});
  });

  it('claims the screen once, however often Take over is pressed while it is being taken', async () => {
    const claim = deferred<(() => void) | null>();
    const screen = { claim: vi.fn(() => claim.promise) };
    const { result } = renderHook(() => useTakeOver(maya(), 'browser', () => screen, mocks.notify));
    let taking!: Promise<void>;
    await act(async () => {
      taking = result.current.takeOver();
    });
    expect(result.current).toMatchObject({ active: false, busy: true });

    await act(async () => result.current.takeOver());
    expect(screen.claim).toHaveBeenCalledOnce();

    await act(async () => {
      claim.resolve(vi.fn());
      await taking;
    });
    expect(result.current).toMatchObject({ active: true, busy: false });
    expect(mocks.pauseWorkflow).toHaveBeenCalledOnce();
    expect(mocks.notify.mock.calls).toEqual([['You have control — Maya will wait', 'success']]);
  });

  it('takes nothing on the Canvas tab, which has no screen', async () => {
    const { result } = renderHook(() => useTakeOver(maya(), 'board', () => null, mocks.notify));

    await act(async () => result.current.takeOver());

    expect(result.current).toMatchObject({ active: false, screen: null, busy: false });
    expect(mocks.pauseWorkflow).not.toHaveBeenCalled();
    expect(mocks.notify).not.toHaveBeenCalled();
    expect(useHomeStore.getState().takeovers).toEqual({});
  });
});

describe('No one to take over from', () => {
  it('takes nothing while no employee is known, as for the editor’s unsaved workflow', async () => {
    const screen = { claim: vi.fn(async () => vi.fn()) };
    const { result } = renderHook(() => useTakeOver(null, 'browser', () => screen, mocks.notify));
    expect(result.current).toMatchObject({ active: false, screen: null, busy: false });

    await act(async () => result.current.takeOver());
    await act(async () => result.current.handBack());

    expect(screen.claim).not.toHaveBeenCalled();
    expect(mocks.pauseWorkflow).not.toHaveBeenCalled();
    expect(mocks.resumeWorkflow).not.toHaveBeenCalled();
    expect(mocks.notify).not.toHaveBeenCalled();
    expect(useHomeStore.getState().takeovers).toEqual({});
  });

  it('reports nothing taken while the owner has another employee’s screen', async () => {
    const giveBack = vi.fn();
    useHomeStore.setState({ takeovers: { w1: { screen: 'browser', giveBack } }, takeoverBusy: { w1: true } });
    const { result } = renderHook(() => useTakeOver(null, 'browser', () => ({ claim: vi.fn(async () => null) }), mocks.notify));
    expect(result.current).toMatchObject({ active: false, screen: null, busy: false });

    await act(async () => result.current.handBack());

    // Maya's screen stays taken: only her own Workspace hands it back.
    expect(giveBack).not.toHaveBeenCalled();
    expect(useHomeStore.getState().takeovers).toHaveProperty('w1');
    expect(mocks.notify).not.toHaveBeenCalled();
  });
});

describe('One dock, several employees', () => {
  it('never holds one employee’s Take over while another’s Hand back waits for a drain', async () => {
    const stop = deferred<WorkflowControlStatus>();
    mocks.pauseWorkflow.mockReturnValue(stop.promise);
    const screen = { claim: vi.fn(async () => vi.fn()) };
    const leo = parseEmployee({
      workflow_id: 'w2', name: 'Leo', role: 'Researcher', status: 'working',
      control: normalizeWorkflowControlStatus({ generation: 1, state: 'running', revision: 9, root_execution_id: 'w2-root' }, 'w2'),
    })!;
    // The dock's one hook instance follows whichever employee it shows.
    const { result, rerender } = renderHook(({ employee }) => useTakeOver(employee, 'browser', () => screen, mocks.notify), {
      initialProps: { employee: maya() },
    });
    let taking!: Promise<void>;
    await act(async () => {
      taking = result.current.takeOver();
    });
    let handing!: Promise<void>;
    await act(async () => {
      handing = result.current.handBack();
    });
    expect(result.current.busy).toBe(true);

    // Maya's Hand back waits for her Stop's drain; Leo is free meanwhile.
    rerender({ employee: leo });
    expect(result.current.busy).toBe(false);
    await act(async () => {
      void result.current.takeOver();
    });
    expect(result.current.active).toBe(true);
    expect(mocks.pauseWorkflow).toHaveBeenLastCalledWith('w2', 9, 'w2-root');

    await act(async () => {
      stop.resolve(pauseAnswer('paused', 6));
      await Promise.all([taking, handing]);
    });
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 6, 'w1-root');
  });
});

describe('Taking the screen again', () => {
  /** The hook over a screen that grants two claims, each with its own way back. */
  function twoClaims() {
    const first = vi.fn();
    const second = vi.fn();
    const screen = { claim: vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second) };
    const { result } = renderHook(() => useTakeOver(maya(), 'browser', () => screen, mocks.notify));
    return { result, first, second, screen };
  }

  it('sends no second Stop, and Hand back gives back the screen taken last', async () => {
    const { result, first, second, screen } = twoClaims();
    await act(async () => result.current.takeOver());
    mocks.notify.mockClear();

    // The screen let go of the owner (the window lost focus): they take it again.
    await act(async () => result.current.takeOver());

    expect(screen.claim).toHaveBeenCalledTimes(2);
    expect(mocks.pauseWorkflow).toHaveBeenCalledOnce();
    expect(mocks.notify).not.toHaveBeenCalled();
    expect(useHomeStore.getState().takeovers.w1).toMatchObject({ screen: 'browser', giveBack: second });

    await act(async () => result.current.handBack());
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 5, 'w1-root');
  });

  it('gives back the screen taken last when the Stop fails', async () => {
    const stop = deferred<WorkflowControlStatus>();
    mocks.pauseWorkflow.mockReturnValue(stop.promise);
    const { result, first, second } = twoClaims();
    let taking!: Promise<void>;
    await act(async () => {
      taking = result.current.takeOver();
    });
    await act(async () => result.current.takeOver());

    await act(async () => {
      stop.reject(new Error('control_revision_conflict'));
      await taking;
    });

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledOnce();
    expect(result.current.active).toBe(false);
    expect(useHomeStore.getState().takeovers).toEqual({});
    expect(mocks.notify).toHaveBeenLastCalledWith('That changed a moment ago. Try again.', 'error');
  });
});

describe('Hand back', () => {
  it('keeps an independently paused workflow stopped when Take over joined its pause', async () => {
    mocks.pauseWorkflow.mockResolvedValue(status('paused', 6));
    const { result, giveBack } = takeOverHook();
    await act(async () => result.current.takeOver());
    await act(async () => result.current.handBack());
    expect(giveBack).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).not.toHaveBeenCalled();
  });

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
      stop.resolve(pauseAnswer('paused', 6));
      await Promise.all([taking, handing]);
    });
    expect(mocks.pauseWorkflow).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 6, 'w1-root');
    expect(result.current).toMatchObject({ active: false, busy: false });
    expect(mocks.notify).toHaveBeenLastCalledWith('Handed back to Maya', 'success');
  });

  it('observes the checkpoint before resuming the paused generation', async () => {
    // A Stop answers within 10 s even while the drain goes on.
    mocks.pauseWorkflow.mockResolvedValueOnce(pauseAnswer('pausing', 5));
    mocks.getWorkflowControlStatus.mockResolvedValueOnce(status('paused', 6));
    const { result, giveBack } = takeOverHook();
    await act(async () => result.current.takeOver());

    await act(async () => result.current.handBack());

    expect(mocks.pauseWorkflow).toHaveBeenCalledOnce();
    expect(giveBack.mock.invocationCallOrder[0]).toBeLessThan(mocks.getWorkflowControlStatus.mock.invocationCallOrder[0]);
    expect(mocks.resumeWorkflow).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 6, 'w1-root');
  });

  it('waits through a pending checkpoint without issuing another Stop', async () => {
    // Given back, a phone task goes on with its goal, and the drain with it.
    mocks.pauseWorkflow.mockResolvedValueOnce(pauseAnswer('pausing', 5));
    mocks.getWorkflowControlStatus
      .mockResolvedValueOnce(status('pausing', 5))
      .mockResolvedValueOnce(status('paused', 6));
    const { result } = takeOverHook();
    await act(async () => result.current.takeOver());

    await act(async () => result.current.handBack());

    expect(mocks.pauseWorkflow).toHaveBeenCalledOnce();
    expect(mocks.getWorkflowControlStatus).toHaveBeenCalledTimes(2);
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 6, 'w1-root');
  });

  it('from paused, resumes with the newest revision and sends no Stop', async () => {
    mocks.pauseWorkflow.mockResolvedValueOnce(pauseAnswer('pausing', 5));
    const { result, rerender } = takeOverHook();
    await act(async () => result.current.takeOver());
    // The drain ended on its own, and the summary has caught up.
    rerender({ employee: maya('paused', 6) });

    await act(async () => result.current.handBack());

    expect(mocks.pauseWorkflow).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).toHaveBeenCalledWith('w1', 6, 'w1-root');
  });

  it('releases the screen without resuming when its Stop fails during Hand back', async () => {
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

    expect(giveBack).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).not.toHaveBeenCalled();
    expect(mocks.notify).toHaveBeenLastCalledWith('That did not work. Try again.', 'error');
  });

  it.each([
    { ...status('paused', 9) },
    { ...status('paused', 6), generation: 2, root_execution_id: 'new-root' },
  ])('leaves a newer pause alone on Hand back', async (newerPause) => {
    mocks.pauseWorkflow.mockResolvedValueOnce(pauseAnswer('pausing', 5));
    const { result, rerender, giveBack } = takeOverHook();
    await act(async () => result.current.takeOver());
    rerender({ employee: { ...maya(), control: newerPause } });
    await act(async () => result.current.handBack());
    expect(giveBack).toHaveBeenCalledOnce();
    expect(mocks.pauseWorkflow).toHaveBeenCalledOnce();
    expect(mocks.resumeWorkflow).not.toHaveBeenCalled();
  });
});
