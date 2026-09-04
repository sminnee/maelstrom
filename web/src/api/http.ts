import { errorCode, type ErrorCode } from './types';

/**
 * One failed request, whatever failed it. `status` is 0 when no reply came:
 * the code is then `transport` (fetch threw) or `timeout`.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode;
  /**
   * What the refusal said beyond its code and message. A create whose launch
   * failed names the task it wrote here, so the caller can tell that from
   * "nothing was written" and not retry the write.
   */
  readonly detail: Record<string, unknown>;
  constructor(
    status: number,
    code: ErrorCode,
    message: string,
    detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/**
 * How long a call that opens a worktree may take. The host provisions one
 * before it starts an agent, and inference shells out to `claude -p`, so the
 * three calls that can do either wait far longer than a read.
 */
export const SLOW_CALL_TIMEOUT_MS = 120_000;

export interface RequestOptions {
  /** Overrides the client's default for one call. */
  timeoutMs?: number;
}

export interface ApiClient {
  get<T>(path: string, opts?: RequestOptions): Promise<T>;
  post<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T>;
  patch<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T>;
  delete<T>(path: string, opts?: RequestOptions): Promise<T>;
}

export interface ApiClientOptions {
  /** Prefixed to every path. Empty means same-origin, which the dev proxy serves. */
  baseUrl?: string;
  fetch?: typeof fetch;
  defaultTimeoutMs?: number;
}

/**
 * The orchestrator server's JSON API over `fetch`. Every failure is an
 * `ApiError`: the server's `{error: {code, message}}` when it answered,
 * `invalid` when it answered something that is not JSON, `transport` when
 * fetch threw, `timeout` when the deadline passed first.
 */
export function createApiClient(opts: ApiClientOptions = {}): ApiClient {
  const baseUrl = opts.baseUrl ?? '';
  const fetchImpl = opts.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const defaultTimeoutMs = opts.defaultTimeoutMs ?? 15_000;

  async function request<T>(
    method: string,
    path: string,
    body: unknown,
    options: RequestOptions,
  ): Promise<T> {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort(new Error('Timed out'));
    }, options.timeoutMs ?? defaultTimeoutMs);
    const init: RequestInit = { method, signal: controller.signal };
    if (body !== undefined) {
      init.body = JSON.stringify(body);
      init.headers = { 'Content-Type': 'application/json' };
    }
    let response: Response;
    let text: string;
    try {
      response = await fetchImpl(baseUrl + path, init);
      // The deadline covers the body too: a server that sends headers and
      // then stalls must not hold the query open forever.
      text = await response.text();
    } catch (err) {
      if (timedOut) throw new ApiError(0, 'timeout', `${method} ${path} timed out`);
      throw new ApiError(0, 'transport', err instanceof Error ? err.message : String(err));
    } finally {
      clearTimeout(timer);
    }
    let parsed: unknown = undefined;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        throw new ApiError(response.status, 'invalid', `${method} ${path}: not JSON`);
      }
    }
    if (!response.ok) {
      const error = (parsed as { error?: Record<string, unknown> } | undefined)?.error;
      const { code, message, ...detail } = error ?? {};
      throw new ApiError(
        response.status,
        errorCode(code),
        typeof message === 'string' ? message : `${method} ${path}: ${response.status}`,
        detail,
      );
    }
    return parsed as T;
  }

  return {
    get: (path, opts = {}) => request('GET', path, undefined, opts),
    post: (path, body = {}, opts = {}) => request('POST', path, body, opts),
    patch: (path, body = {}, opts = {}) => request('PATCH', path, body, opts),
    delete: (path, opts = {}) => request('DELETE', path, undefined, opts),
  };
}

/**
 * What a button says after a failed request. A route this server does not
 * serve says so; anything else says it failed, and the message is the title.
 */
export function describeError(err: unknown): string {
  return err instanceof ApiError && err.code === 'not_implemented'
    ? 'Not implemented yet'
    : 'Failed';
}
