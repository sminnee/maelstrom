import { describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient } from './http';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('createApiClient', () => {
  it('GETs under the base URL and returns the parsed body', async () => {
    const fetch = vi.fn().mockResolvedValue(jsonResponse(200, { projects: [] }));
    const api = createApiClient({ baseUrl: 'http://host', fetch });
    await expect(api.get('/api/projects')).resolves.toEqual({ projects: [] });
    expect(fetch).toHaveBeenCalledWith('http://host/api/projects', expect.anything());
  });

  it('POSTs a JSON body', async () => {
    const fetch = vi.fn().mockResolvedValue(jsonResponse(200, {}));
    const api = createApiClient({ fetch });
    await api.post('/api/agents/ag1/say', { text: 'hi' });
    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{"text":"hi"}');
    expect(new Headers(init.headers).get('content-type')).toBe('application/json');
  });

  it("throws the server's code and message as an ApiError", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(409, { error: { code: 'stale_request', message: 'Request x is stale' } }),
      );
    const api = createApiClient({ fetch });
    const err = await api.post('/api/agents/ag1/approve', {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      status: 409,
      code: 'stale_request',
      message: 'Request x is stale',
    });
  });

  it('reads a code it does not know as invalid', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(jsonResponse(418, { error: { code: 'teapot', message: 'short' } }));
    const api = createApiClient({ fetch });
    const err = await api.get('/api/tasks').catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 418, code: 'invalid', message: 'short' });
  });

  it('turns a non-JSON body into an invalid ApiError', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('<html>', { status: 502 }));
    const api = createApiClient({ fetch });
    const err = await api.get('/api/tasks').catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 502, code: 'invalid' });
  });

  it('turns a thrown fetch into a transport ApiError', async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    const api = createApiClient({ fetch });
    const err = await api.get('/api/tasks').catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 0, code: 'transport', message: 'Failed to fetch' });
  });

  it('aborts a request that outlives its timeout with the code timeout', async () => {
    vi.useFakeTimers();
    try {
      const fetch: typeof globalThis.fetch = (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(init.signal?.reason));
        });
      const api = createApiClient({ fetch, defaultTimeoutMs: 50 });
      const pending = api.get('/api/tasks').catch((e: unknown) => e);
      await vi.advanceTimersByTimeAsync(60);
      expect(await pending).toMatchObject({ code: 'timeout' });
    } finally {
      vi.useRealTimers();
    }
  });
});
