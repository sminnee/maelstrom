import { useCallback, useState } from 'react';
import type { Command } from '../protocol/commands';
import { useBackend } from './backendContext';

/**
 * Send a command and keep its last refusal for the UI to show. Every panel
 * that sends commands goes through this, so a refusal reads the same
 * everywhere.
 */
export function useCommand(): {
  send: (cmd: Command) => Promise<boolean>;
  error: string | null;
} {
  const backend = useBackend();
  const [error, setError] = useState<string | null>(null);
  const send = useCallback(
    async (cmd: Command) => {
      setError(null);
      const reply = await backend.command(cmd);
      if (!reply.ok) setError(`${reply.error.code}: ${reply.error.message}`);
      return reply.ok;
    },
    [backend],
  );
  return { send, error };
}
