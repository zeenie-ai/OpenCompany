/**
 * The node panel's header actions: Run, Save and Cancel. Colour marks state
 * or role, so none of them takes an action intent's colour; Save is the
 * panel's one strong (`invert`) button and the others are neutral outlines.
 */
import { screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../test/providers';

const panel = vi.hoisted(() => ({
  hasUnsavedChanges: false,
  status: null as { status: string } | null,
}));

vi.mock('../../hooks/useParameterPanel', () => ({
  useParameterPanel: () => ({
    selectedNode: { id: 'node-1', type: 'httpRequest', data: { label: 'HTTP Request' } },
    nodeDefinition: { name: 'httpRequest', displayName: 'HTTP Request', icon: '', uiHints: {} },
    parameters: {},
    hasUnsavedChanges: panel.hasUnsavedChanges,
    handleParameterChange: vi.fn(),
    handleSave: vi.fn(),
    handleCancel: vi.fn(),
    isLoading: false,
  }),
}));
vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    executeNode: vi.fn(),
    getNodeParameters: vi.fn(),
    clearNodeStatus: vi.fn(),
    cancelEventWait: vi.fn(),
    getNodeStatus: () => panel.status,
    workflowControlStatuses: {},
    workflowLock: { locked: false, workflow_id: null, locked_at: null, reason: null },
  }),
}));
vi.mock('../../services/executionService', () => ({
  ExecutionService: { isNodeTypeSupported: () => true },
}));
vi.mock('../parameterPanel/ParameterPanelLayout', () => ({ default: () => <div data-testid="layout" /> }));
vi.mock('../../assets/icons', () => ({ NodeIcon: () => null }));

import ParameterPanel from '../../ParameterPanel';

const COLOUR = /action-|node-|success|warning|\binfo\b|primary/;

describe('The node panel’s header', () => {
  beforeEach(() => {
    panel.hasUnsavedChanges = false;
    panel.status = null;
  });

  it('keeps Run and Cancel neutral, with Save as its one strong button', () => {
    panel.hasUnsavedChanges = true;
    render(<ParameterPanel />);
    const run = screen.getByRole('button', { name: 'Run' });
    const save = screen.getByRole('button', { name: 'Save' });
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(save).toHaveAttribute('data-variant', 'invert');
    expect(save).toBeEnabled();
    for (const button of [run, cancel]) {
      expect(button).toHaveAttribute('data-variant', 'quiet');
      expect(button).toHaveClass('border-border-default', 'text-fg-default');
    }
    for (const button of [run, save, cancel]) {
      expect(button.className).not.toMatch(COLOUR);
      expect(button).not.toHaveClass('action-btn');
      // The toolbar's shape, and a lucide icon in place of the inline drawing.
      expect(button).toHaveClass('rounded-md', 'px-3.5', 'font-semibold');
      expect(button.querySelector('svg.lucide')).not.toBeNull();
    }
  });

  it('greys Save until there is something to save', () => {
    render(<ParameterPanel />);
    const save = screen.getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();
    expect(save).toHaveClass('disabled:bg-border-default', 'disabled:text-fg-faint');
  });

  it('reads Stop on a node that is waiting, still neutral', () => {
    panel.status = { status: 'waiting' };
    render(<ParameterPanel />);
    const stop = screen.getByRole('button', { name: 'Stop' });
    expect(stop).toHaveAttribute('data-variant', 'quiet');
    expect(stop.className).not.toMatch(COLOUR);
  });
});
