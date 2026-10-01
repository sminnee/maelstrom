import { useContext } from 'react';
import { useRun, useSay } from '../api/agents';
import type { Attachment } from '../api/attachments';
import { AgentStreamsContext } from '../live/useAgentStream';
import type { AgentId } from '../protocol/ids';

/**
 * What a `MessageInput` needs to reach an agent: `onSend` and `onRun`.
 *
 * The message shows in the transcript at once and is taken back when the
 * server refuses it, so every surface that sends does both.
 */
export function useSendMessage(agentId: AgentId) {
  const say = useSay();
  const run = useRun();
  const streams = useContext(AgentStreamsContext);
  if (!streams) throw new Error('useSendMessage outside LiveProvider');
  return {
    onSend: (text: string, attachments: Attachment[]) => {
      const removeLocal = text ? streams.sendLocal(agentId, text) : undefined;
      return say.mutateAsync({ agentId, text, attachments }).catch((err) => {
        removeLocal?.();
        throw err;
      });
    },
    onRun: (command: string) => run.mutateAsync({ agentId, command }),
  };
}
