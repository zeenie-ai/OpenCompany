import { act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../test/providers';

const { storeState, wsMock, apiKeyMock, dynamicParameterMock } = vi.hoisted(() => ({
  storeState: { selectedNode: null as any },
  wsMock: {
    getNodeParameters: vi.fn(),
    sendRequest: vi.fn(),
  },
  apiKeyMock: {
    getStoredApiKey: vi.fn(),
    hasStoredKey: vi.fn(),
    getStoredModels: vi.fn(),
    getProviderDefaults: vi.fn(),
  },
  dynamicParameterMock: {
    updateParameterOptions: vi.fn(),
    subscribe: vi.fn(() => vi.fn()),
    getParameterOptions: vi.fn(() => null),
    createModelOptions: vi.fn((models: string[]) => models),
  },
}));

vi.mock('../../store/useAppStore', () => ({
  useAppStore: <T,>(selector: (state: typeof storeState) => T): T => selector(storeState),
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsMock,
}));

vi.mock('../../hooks/useApiKeys', () => ({
  useApiKeys: () => apiKeyMock,
}));

vi.mock('../../lib/nodeSpec', () => ({
  isNodeInBackendGroup: () => false,
  resolveNodeDescription: () => null,
}));

vi.mock('../../services/dynamicParameterService', () => ({
  default: dynamicParameterMock,
}));

import ParameterRenderer from '../ParameterRenderer';


function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}


describe('ParameterRenderer request isolation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    storeState.selectedNode = {
      id: 'node-a',
      type: 'httpRequest',
      data: { nodeType: 'httpRequest' },
    };
    wsMock.getNodeParameters.mockResolvedValue({ parameters: {} });
  });

  it('cannot apply options from a node that was deselected before its request resolved', async () => {
    const pendingA = deferred<{ options: Array<{ value: string; label: string }> }>();
    const pendingB = deferred<{ options: Array<{ value: string; label: string }> }>();
    wsMock.sendRequest.mockImplementation((_type: string, payload: any) => (
      payload.params.node_id === 'node-a' ? pendingA.promise : pendingB.promise
    ));

    const onChangeA = vi.fn();
    const onChangeB = vi.fn();
    const parameter: any = {
      displayName: 'Choice',
      name: 'choice',
      type: 'options',
      default: '',
      options: [],
      typeOptions: { loadOptionsMethod: 'load_choices' },
    };
    const stableParameters = {};

    const view = render(
      <ParameterRenderer
        parameter={parameter}
        value=""
        allParameters={stableParameters}
        onChange={onChangeA}
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(wsMock.sendRequest).toHaveBeenCalledWith(
      'load_options',
      expect.objectContaining({
        params: expect.objectContaining({ node_id: 'node-a' }),
      }),
    );

    storeState.selectedNode = {
      id: 'node-b',
      type: 'httpRequest',
      data: { nodeType: 'httpRequest' },
    };
    view.rerender(
      <ParameterRenderer
        parameter={parameter}
        value=""
        allParameters={stableParameters}
        onChange={onChangeB}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      pendingA.resolve({ options: [{ value: 'from-a', label: 'A' }] });
      await pendingA.promise;
    });
    expect(onChangeA).not.toHaveBeenCalled();
    expect(onChangeB).not.toHaveBeenCalled();
    expect(dynamicParameterMock.updateParameterOptions).not.toHaveBeenCalledWith(
      'node-b',
      'choice',
      expect.arrayContaining([expect.objectContaining({ value: 'from-a' })]),
    );

    await act(async () => {
      pendingB.resolve({ options: [{ value: 'from-b', label: 'B' }] });
      await pendingB.promise;
    });
    expect(onChangeB).toHaveBeenCalledWith('from-b');
    expect(dynamicParameterMock.updateParameterOptions).toHaveBeenCalledWith(
      'node-b',
      'choice',
      [expect.objectContaining({ value: 'from-b' })],
    );
  });
});
