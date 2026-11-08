import { describe, expect, it, vi } from 'vitest';
import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  browserSupportsWebAuthn,
  startAuthentication,
  startRegistration,
} from '@simplewebauthn/browser';
import { PENDING_POLL_MS } from './api/auth';
import { renderApp } from './test/renderApp';

// The browser half of a ceremony: the authenticator itself. The server half
// is proven against a software authenticator in test_orchestrator_auth.py.
vi.mock('@simplewebauthn/browser', () => ({
  browserSupportsWebAuthn: vi.fn(() => true),
  startRegistration: vi.fn(async () => ({ id: 'cred1' })),
  startAuthentication: vi.fn(async () => ({ id: 'cred1' })),
}));

describe('a browser with no sign-in', () => {
  it('shows the Sign-in screen and opens no stream', async () => {
    const { server } = await renderApp({ scenario: 'signed-out' });

    expect(await screen.findByTestId('sign-in')).toBeInTheDocument();
    expect(screen.queryByTestId('top-bar')).toBeNull();
    expect(server.sources).toHaveLength(0);
    expect(server.requests.map((r) => r.path)).toEqual(['/api/auth/status']);
  });

  it('signs in with a passkey and opens the app', async () => {
    const user = userEvent.setup();
    const { server } = await renderApp({ scenario: 'signed-out' });

    await user.click(await screen.findByRole('button', { name: 'Sign in with a passkey' }));

    expect(await screen.findByTestId('top-bar')).toBeInTheDocument();
    expect(startAuthentication).toHaveBeenCalledWith({
      optionsJSON: { challenge: 'fake-challenge', rpId: 'localhost' },
    });
    expect(server.requests.some((r) => r.path === '/api/auth/login')).toBe(true);
  });

  it('shows the verify command for a new passkey, and opens the app once the host verifies it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      const { server } = await renderApp({ scenario: 'signed-out' });

      const name = await screen.findByLabelText('Name of this device');
      await user.clear(name);
      await user.type(name, 'Work laptop');
      await user.click(screen.getByRole('button', { name: 'Create a passkey' }));

      expect(await screen.findByTestId('verify-command')).toHaveTextContent(
        'mael auth verify K7QF-2M9D',
      );
      expect(screen.getByTestId('sign-in')).toHaveTextContent('Work laptop');
      expect(startRegistration).toHaveBeenCalled();

      server.world.auth = { state: 'signed_in', label: 'Work laptop' };
      await act(async () => {
        vi.advanceTimersByTime(PENDING_POLL_MS);
      });
      expect(await screen.findByTestId('top-bar')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('a browser that cannot use a passkey', () => {
  it('says to open the app over HTTPS or at localhost', async () => {
    vi.mocked(browserSupportsWebAuthn).mockReturnValueOnce(false);
    await renderApp({ scenario: 'signed-out' });

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Open the app over HTTPS, or at localhost on the host.',
    );
    expect(screen.queryByRole('button', { name: 'Create a passkey' })).toBeNull();
  });
});

describe('a server that cannot say who is signed in', () => {
  it('shows the app, so its banner says what is wrong', async () => {
    await renderApp({
      refuse: [{ route: /GET \/api\/auth\/status/, error: { status: 502, code: 'invalid' } }],
    });

    expect(screen.getByTestId('top-bar')).toBeInTheDocument();
    expect(screen.queryByTestId('sign-in')).toBeNull();
  });
});

describe('a signed-in browser', () => {
  it.each([
    { why: 'a revoke', auth: { state: 'anonymous' }, status: 401, code: 'auth_required' },
    {
      why: 'a passkey back to pending',
      auth: { state: 'pending', code: 'K7QF-2M9D', label: 'Laptop' },
      status: 403,
      code: 'passkey_pending',
    },
  ] as const)('goes to the Sign-in screen after $why', async ({ auth, status, code }) => {
    const { server } = await renderApp();

    // Each read is refused, and the status says why.
    server.world.auth = auth;
    server.refuse(/GET \/api\/tasks/, { status, code });
    await act(async () => {
      server.change({ kind: 'task', ids: [] });
    });

    expect(await screen.findByTestId('sign-in')).toBeInTheDocument();
  });

  it('signs out from the Welcome dialog', async () => {
    const user = userEvent.setup();
    await renderApp();

    await user.click(screen.getByRole('button', { name: 'About maelstrom' }));
    await user.click(await screen.findByRole('button', { name: 'Sign out' }));

    expect(await screen.findByTestId('sign-in')).toBeInTheDocument();
  });
});
