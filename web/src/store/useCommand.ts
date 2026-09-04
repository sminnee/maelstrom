import { useCallback } from 'react';
import type { Command, ErrorCode, ResultFor } from '../protocol/commands';
import { useBackend } from './backendContext';

/** A refusal, or a transport failure, as one error the control that sent the command shows. */
export class CommandError extends Error {
  readonly code: ErrorCode | 'transport';
  constructor(code: ErrorCode | 'transport', message: string) {
    super(message);
    this.name = 'CommandError';
    this.code = code;
  }
}

/**
 * Send a command. The promise resolves with the result, or rejects with a
 * `CommandError`: a refusal carries the server's code, a dropped or
 * reconnecting socket the code `transport`. The control that sent it owns
 * the pending and error states, so nothing here keeps them.
 */
export function useCommand(): {
  send: <C extends Command>(cmd: C) => Promise<ResultFor<C>>;
} {
  const backend = useBackend();
  const send = useCallback(
    async <C extends Command>(cmd: C): Promise<ResultFor<C>> => {
      let reply;
      try {
        reply = await backend.command(cmd);
      } catch (err) {
        throw new CommandError('transport', err instanceof Error ? err.message : String(err));
      }
      if (!reply.ok) throw new CommandError(reply.error.code, reply.error.message);
      return reply.result;
    },
    [backend],
  );
  return { send };
}
