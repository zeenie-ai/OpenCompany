/**
 * OnboardingWizard behavioural tests.
 *
 * The wizard's state machine lives in useOnboarding (tested separately in
 * src/hooks/__tests__/useOnboarding.steps.test.ts). Here the hook is fully
 * mocked so each test pins the wizard at a known step and asserts:
 *   - the 4-step rail renders every step title
 *   - step dispatch by index (ConnectAI stub at index 2, with
 *     onOpenCredentials passed through)
 *   - footer nav wiring: Back hidden on step 0, Next -> nextStep,
 *     final "Open AI Assistant" -> complete() then onFinish() (in order),
 *     "Skip for now" / modal close -> skip() and NEVER onFinish()
 *   - visibility gate: renders nothing unless isVisible && hasChecked
 *     && !isLoading
 *
 * Step children that consume live hooks (catalogue / node groups) are
 * stubbed; the pure steps (Welcome, TryIt) render for real.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';
import { renderWithProviders } from '../../../test/providers';

// --- Mocks (declared BEFORE importing the wizard -- vi.mock is hoisted) -----

const nextStep = vi.fn();
const prevStep = vi.fn();
const skip = vi.fn();
const complete = vi.fn();

interface HookState {
  isVisible: boolean;
  currentStep: number;
  isCompleted: boolean;
  isLoading: boolean;
  hasChecked: boolean;
  totalSteps: number;
}

let hookState: HookState;

// Full-module replace (the importActual+spread variant is documented broken
// under React 19 for context-backed modules; full replace is the canonical
// pattern -- see CredentialsModal.test.tsx).
vi.mock('../../../hooks/useOnboarding', () => ({
  useOnboarding: () => ({
    ...hookState,
    nextStep,
    prevStep,
    skip,
    complete,
  }),
}));

// Stub the step children that consume live hooks (useCatalogueQuery /
// useNodeGroups). They have their own dedicated suites.
vi.mock('../steps/ConnectAIStep', () => ({
  default: ({ onOpenCredentials }: { onOpenCredentials: () => void }) => (
    <button type="button" data-testid="connect-ai-stub" onClick={onOpenCredentials}>
      ConnectAIStep stub
    </button>
  ),
}));

vi.mock('../steps/HowItWorksStep', () => ({
  default: () => <div data-testid="how-it-works-stub" />,
}));

import OnboardingWizard from '../OnboardingWizard';

const RAIL_LABELS = ['Welcome', 'How it works', 'Connect your AI', 'Try it'];

const renderWizard = (
  props: Partial<ComponentProps<typeof OnboardingWizard>> = {},
) =>
  renderWithProviders(
    <OnboardingWizard onOpenCredentials={props.onOpenCredentials ?? vi.fn()} {...props} />,
  );

beforeEach(() => {
  vi.clearAllMocks();
  hookState = {
    isVisible: true,
    currentStep: 0,
    isCompleted: false,
    isLoading: false,
    hasChecked: true,
    totalSteps: 4,
  };
});

describe('OnboardingWizard progress rail', () => {
  it('renders all four step rail labels', () => {
    renderWizard();
    for (const label of RAIL_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});

describe('OnboardingWizard step dispatch', () => {
  it('renders the ConnectAI step at index 2 and passes onOpenCredentials through', async () => {
    hookState.currentStep = 2;
    const onOpenCredentials = vi.fn();
    renderWizard({ onOpenCredentials });

    const stub = screen.getByTestId('connect-ai-stub');
    expect(stub).toBeInTheDocument();
    // Steps for other indices are not mounted.
    expect(screen.queryByTestId('how-it-works-stub')).not.toBeInTheDocument();

    await userEvent.click(stub);
    expect(onOpenCredentials).toHaveBeenCalledTimes(1);
  });

  it('renders the HowItWorks step at index 1', () => {
    hookState.currentStep = 1;
    renderWizard();
    expect(screen.getByTestId('how-it-works-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('connect-ai-stub')).not.toBeInTheDocument();
  });
});

describe('OnboardingWizard footer navigation', () => {
  it('hides the Back button on the first step', () => {
    renderWizard();
    expect(screen.queryByRole('button', { name: /back/i })).not.toBeInTheDocument();
  });

  it('shows Back from step 1 onward and clicking it calls prevStep', async () => {
    hookState.currentStep = 1;
    renderWizard();
    const back = screen.getByRole('button', { name: /back/i });
    await userEvent.click(back);
    expect(prevStep).toHaveBeenCalledTimes(1);
  });

  it('clicking Next calls nextStep', async () => {
    renderWizard();
    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(nextStep).toHaveBeenCalledTimes(1);
  });

  it('shows "Open AI Assistant" instead of Next on the last step', () => {
    hookState.currentStep = 3;
    renderWizard();
    expect(screen.getByRole('button', { name: 'Open AI Assistant' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /next/i })).not.toBeInTheDocument();
  });

  it('final button calls complete then onFinish, each exactly once, complete first', async () => {
    hookState.currentStep = 3;
    const onFinish = vi.fn();
    renderWizard({ onFinish });

    await userEvent.click(screen.getByRole('button', { name: 'Open AI Assistant' }));

    expect(complete).toHaveBeenCalledTimes(1);
    expect(onFinish).toHaveBeenCalledTimes(1);
    // complete() must run before onFinish() (handleFinish contract).
    expect(complete.mock.invocationCallOrder[0]).toBeLessThan(
      onFinish.mock.invocationCallOrder[0],
    );
    // Skipping paths untouched.
    expect(skip).not.toHaveBeenCalled();
  });

  it('final button does not throw when onFinish is omitted', async () => {
    hookState.currentStep = 3;
    renderWizard(); // no onFinish prop
    await userEvent.click(screen.getByRole('button', { name: 'Open AI Assistant' }));
    expect(complete).toHaveBeenCalledTimes(1);
  });
});

describe('OnboardingWizard skip paths never call onFinish', () => {
  it('"Skip for now" calls skip and never onFinish/complete', async () => {
    const onFinish = vi.fn();
    renderWizard({ onFinish });

    await userEvent.click(screen.getByRole('button', { name: /skip for now/i }));

    expect(skip).toHaveBeenCalledTimes(1);
    expect(onFinish).not.toHaveBeenCalled();
    expect(complete).not.toHaveBeenCalled();
  });

  it('modal close (X) calls skip and never onFinish/complete', async () => {
    const onFinish = vi.fn();
    renderWizard({ onFinish });

    // Modal's DialogClose carries aria-label="Close". Radix may fire both the
    // explicit onClick and onOpenChange(false), so assert >= 1 call rather
    // than an exact count -- the contract under test is "skip, not finish".
    await userEvent.click(screen.getByRole('button', { name: /close/i }));

    expect(skip.mock.calls.length).toBeGreaterThanOrEqual(1);
    expect(onFinish).not.toHaveBeenCalled();
    expect(complete).not.toHaveBeenCalled();
  });
});

describe('OnboardingWizard visibility gate', () => {
  it('renders nothing when isVisible is false', () => {
    hookState.isVisible = false;
    const { container } = renderWizard();
    expect(container).toBeEmptyDOMElement();
    // Portal content absent too.
    expect(screen.queryByText('Welcome Guide')).not.toBeInTheDocument();
  });

  it('renders nothing before the settings check completes (hasChecked false)', () => {
    hookState.hasChecked = false;
    hookState.isLoading = true;
    const { container } = renderWizard();
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText('Welcome Guide')).not.toBeInTheDocument();
  });

  it('renders nothing while isLoading is true', () => {
    hookState.isLoading = true;
    const { container } = renderWizard();
    expect(container).toBeEmptyDOMElement();
  });
});
