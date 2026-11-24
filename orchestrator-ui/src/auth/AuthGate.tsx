import { useEffect, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAuthStatus } from '../api/auth';
import { ApiError } from '../api/http';
import { keys } from '../api/keys';
import { SignIn } from './SignIn';

/**
 * Draws `children` only for a signed-in browser, else the **Sign-in** screen.
 * Nothing under it mounts before then, so no stream opens without a sign-in.
 *
 * A refusal of any query for want of a sign-in reads the status again: a
 * sign-in can end while the app is open, as a revoke on the host does.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const status = useAuthStatus();
  const queryClient = useQueryClient();
  // Set while the status cannot be read. A refetch of a query with no data
  // goes back to `pending`, and the app must not unmount for it: its stream's
  // reset is one such refetch, so it would unmount and remount for ever.
  const [unread, setUnread] = useState(false);
  if (status.isError && !status.data && !unread) setUnread(true);
  if (status.data && unread) setUnread(false);
  useEffect(
    () =>
      queryClient.getQueryCache().subscribe((event) => {
        if (event.type !== 'updated' || event.action.type !== 'error') return;
        if (event.query.queryKey[0] === keys.auth()[0]) return;
        const err = event.action.error;
        if (err instanceof ApiError && (err.status === 401 || err.code === 'passkey_pending')) {
          void queryClient.invalidateQueries({ queryKey: keys.auth() });
        }
      }),
    [queryClient],
  );
  if (!status.data) {
    // A server that cannot be reached shows the app, whose banner says so.
    return status.isError || unread ? <>{children}</> : null;
  }
  if (status.data.state !== 'signed_in') return <SignIn status={status.data} />;
  return <>{children}</>;
}
