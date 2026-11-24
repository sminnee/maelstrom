import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  startAuthentication,
  startRegistration,
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from '@simplewebauthn/browser';
import { useApi } from './ApiProvider';
import { keys } from './keys';

/**
 * Where this browser stands with the server. `pending` is a **Pending passkey**:
 * it signed up, and waits for `mael auth verify <code>` on the host.
 */
export type AuthStatus =
  | { state: 'anonymous' }
  | { state: 'pending'; code: string; label: string }
  | { state: 'signed_in'; label: string };

/** How often a pending passkey asks whether the host has verified it. */
export const PENDING_POLL_MS = 2000;

/** The browser's **Sign-in**, read again every {@link PENDING_POLL_MS} while it is pending. */
export function useAuthStatus() {
  const api = useApi();
  return useQuery({
    queryKey: keys.auth(),
    queryFn: ({ signal }) => api.get<AuthStatus>('/api/auth/status', { signal }),
    refetchInterval: (query) => (query.state.data?.state === 'pending' ? PENDING_POLL_MS : false),
  });
}

/** Create a passkey on this device and send it to the server. It comes back pending. */
export function useRegisterPasskey() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (label: string) => {
      const optionsJSON = await api.post<PublicKeyCredentialCreationOptionsJSON>(
        '/api/auth/register/options',
      );
      const credential = await startRegistration({ optionsJSON });
      return api.post<AuthStatus>('/api/auth/register', { label, credential });
    },
    onSuccess: (status) => queryClient.setQueryData(keys.auth(), status),
  });
}

/** Sign in with a passkey this device holds. */
export function useSignIn() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const optionsJSON =
        await api.post<PublicKeyCredentialRequestOptionsJSON>('/api/auth/login/options');
      const credential = await startAuthentication({ optionsJSON });
      return api.post<AuthStatus>('/api/auth/login', { credential });
    },
    onSuccess: (status) => queryClient.setQueryData(keys.auth(), status),
  });
}

/** End this browser's sign-in. Every other query is dropped with it. */
export function useSignOut() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AuthStatus>('/api/auth/logout'),
    onSuccess: (status) => {
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== keys.auth()[0] });
      queryClient.setQueryData(keys.auth(), status);
    },
  });
}
