import { createContext, useContext, useEffect } from 'react';
import type { AgentId } from '../protocol/ids';
import { useAppStore } from '../store/store';
import type { AgentStreams } from './agentStreams';
import { emptyTranscript, type TranscriptState } from './transcriptReducer';

export const AgentStreamsContext = createContext<AgentStreams | null>(null);

const NONE = emptyTranscript('connecting');

/**
 * An agent's transcript, kept live for as long as the caller is mounted. With
 * `null` it holds nothing and opens nothing.
 */
export function useAgentStream(agentId: AgentId | null): TranscriptState {
  const streams = useContext(AgentStreamsContext);
  if (!streams) throw new Error('useAgentStream outside LiveProvider');
  useEffect(() => {
    if (agentId === null) return;
    return streams.acquire(agentId);
  }, [streams, agentId]);
  return useAppStore((s) => (agentId === null ? NONE : (s.transcripts[agentId] ?? NONE)));
}
