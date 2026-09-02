import type { Command, Reply } from './commands';
import type { EventFrame } from './events';
import type { AgentId, Seq } from './ids';

/**
 * One duplex channel of typed frames. Events describe the world and are
 * replayable; replies are correlated to a command and never enter the log.
 */
export interface Backend {
  /** Resolves once the snapshot (or the replay from `resumeFrom`) has been delivered. */
  connect(opts?: { resumeFrom?: Seq }): Promise<void>;
  subscribe(listener: (frame: EventFrame) => void): () => void;
  /** Rejects only on transport failure. A refused command resolves with `ok: false`. */
  command<C extends Command>(cmd: C): Promise<Reply<C>>;
  close(): void;
}

/** A beat the debug drawer can inject at an agent's cursor. */
export type ForcedBeat =
  | { kind: 'ask'; agentId: AgentId }
  | { kind: 'permission'; agentId: AgentId }
  | { kind: 'plan'; agentId: AgentId }
  | { kind: 'finish'; agentId: AgentId }
  | { kind: 'exit'; agentId: AgentId; exitCode: number };

export interface SimState {
  playing: boolean;
  speed: number;
  tick: number;
}

export interface SimControls {
  play(): void;
  pause(): void;
  step(n?: number): void;
  setSpeed(x: number): void;
  force(f: ForcedBeat): void;
  /** Called after every tick and every play/pause/speed change. Returns the unsubscribe. */
  subscribe(listener: () => void): () => void;
  readonly state: SimState;
}

/** The fake only. The UI feature-detects `sim` and shows the FAKE chip. */
export interface DebugBackend extends Backend {
  sim: SimControls;
}

export function isDebugBackend(backend: Backend): backend is DebugBackend {
  return 'sim' in backend && typeof (backend as DebugBackend).sim?.step === 'function';
}
