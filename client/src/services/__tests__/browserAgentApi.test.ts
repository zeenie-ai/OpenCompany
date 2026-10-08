import { afterEach, describe, expect, it, vi } from 'vitest';
import { createBrowserAgent } from '../browserAgentApi';

afterEach(() => vi.unstubAllGlobals());

describe('saved Browser Agent creation', () => {
  it('retries a lost response with the same mutation identity and tool selection', async () => {
    const result = { node_ids: { agent: '7:browser_agent:1' }, operations: [] };
    const fetch = vi.fn().mockRejectedValueOnce(new TypeError('Lost response'))
      .mockResolvedValueOnce(new Response(JSON.stringify(result)));
    vi.stubGlobal('fetch', fetch);
    expect(await createBrowserAgent({ workflow_id: '7', browser_node_id: '7:browser:1' })).toEqual(result);
    expect(fetch).toHaveBeenCalledTimes(2);
    const first = fetch.mock.calls[0][1];
    expect(fetch.mock.calls[1][1].body).toBe(first.body);
    expect(first.credentials).toBe('include');
    expect(JSON.parse(first.body)).toMatchObject({ workflow_id: '7', browser_node_id: '7:browser:1', mutation_id: expect.any(String) });
  });

  it('surfaces a server rejection without submitting a second mutation', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Browser access denied' }), { status: 404 }));
    vi.stubGlobal('fetch', fetch);
    await expect(createBrowserAgent({ workflow_id: '7' })).rejects.toThrow('Browser access denied');
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
