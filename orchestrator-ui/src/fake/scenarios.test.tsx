import { act, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AttentionKind } from '../protocol/attention';
import type { DocumentKind, DocumentStatus } from '../protocol/documents';
import type {
  AgentState,
  EnvStateName,
  FileDiff,
  Phase,
  PrState,
  TaskStatus,
} from '../protocol/entities';
import { phaseForCommand } from '../protocol/phase';
import { progressOf, type DriftKind, type NodeState } from '../protocol/progress';
import type { ToolCallStatus, TranscriptItem } from '../protocol/transcript';
import { agentsByTask } from '../selectors/graph';
import { renderApp } from '../test/renderApp';
import { openFromParams } from './deepLink';
import { SCENARIOS, screenOf, type ScenarioName } from './scenarios';

/**
 * The coverage gate: each closed set of the protocol, as a record. See
 * docs/dev/orchestrator-ui.md, "What the tests cover".
 */
const all = <K extends string>(members: Record<K, true>) => Object.keys(members) as K[];

const AGENT_STATES = all<AgentState>({
  idle: true,
  processing: true,
  delegating: true,
  background: true,
  'awaiting-permission': true,
  'awaiting-question': true,
  'awaiting-plan-review': true,
  exited: true,
});
const TASK_STATUSES = all<TaskStatus>({
  todo: true,
  'in-progress': true,
  blocked: true,
  done: true,
  cancelled: true,
  template: true,
});
const NODE_STATES = all<NodeState>({
  queued: true,
  ready: true,
  working: true,
  'needs-attention': true,
  idle: true,
  unanswered: true,
  stopped: true,
  finalising: true,
  done: true,
  cancelled: true,
  exited: true,
});
const DRIFTS = all<DriftKind>({ finished: true, 'never-ran': true, 'orphan-session': true });
const PHASES = all<Phase>({ shape: true, plan: true, build: true, land: true });
const PR_STATES = all<PrState>({
  merged: true,
  'ci-failed': true,
  'ci-running': true,
  conflict: true,
  'checks-unreadable': true,
  unknown: true,
  ready: true,
});
const ENV_STATES = all<EnvStateName>({ running: true, partial: true, stopped: true });
const DOCUMENT_KINDS = all<DocumentKind>({
  plan: true,
  tasks: true,
  pr: true,
  review: true,
  verification: true,
  other: true,
});
const DOCUMENT_STATUSES = all<DocumentStatus>({
  draft: true,
  'awaiting-review': true,
  approved: true,
  'changes-requested': true,
  superseded: true,
  stale: true,
});
const ATTENTION_KINDS = all<AttentionKind>({
  question: true,
  permission: true,
  plan_review: true,
  document_review: true,
  agent_exited: true,
  ci_failed: true,
  task_blocked: true,
});
const ITEM_TYPES = all<TranscriptItem['type']>({
  message: true,
  tool_call: true,
  question: true,
  permission_request: true,
  plan_review: true,
  turn_result: true,
  compact: true,
  compact_summary: true,
  system: true,
  error: true,
  gap: true,
  skill: true,
  task_notification: true,
  shell: true,
  milestone: true,
  raw_event: true,
});
const TOOL_STATUSES = all<ToolCallStatus>({
  pending: true,
  running: true,
  done: true,
  error: true,
  denied: true,
});
const DIFF_STATUSES = all<FileDiff['status']>({
  added: true,
  modified: true,
  deleted: true,
  renamed: true,
});

const NAMES = Object.keys(SCENARIOS) as ScenarioName[];

/** Every value the scenarios show, by set. Derived values come from the production readers. */
function shown() {
  const seen = {
    agentState: new Set<string>(),
    taskStatus: new Set<string>(),
    nodeState: new Set<string>(),
    drift: new Set<string>(),
    phase: new Set<string>(),
    prState: new Set<string>(),
    envState: new Set<string>(),
    documentKind: new Set<string>(),
    documentStatus: new Set<string>(),
    attentionKind: new Set<string>(),
    itemType: new Set<string>(),
    toolStatus: new Set<string>(),
    diffStatus: new Set<string>(),
  };
  for (const name of NAMES) {
    const { world, transcripts } = SCENARIOS[name].build();
    const attention = Object.values(world.attention);
    const agents = Object.values(world.agents);
    const byTask = agentsByTask(world);
    for (const agent of agents) seen.agentState.add(agent.state);
    for (const task of Object.values(world.tasks)) {
      seen.taskStatus.add(task.status);
      const phase = phaseForCommand(task.command);
      if (phase) seen.phase.add(phase);
      const progress = progressOf(task, byTask.get(task.id), attention);
      seen.nodeState.add(progress.state);
      if (progress.drift) seen.drift.add(progress.drift);
    }
    for (const worktree of Object.values(world.worktrees)) {
      if (worktree.prState) seen.prState.add(worktree.prState);
      if (worktree.env) seen.envState.add(worktree.env.state);
    }
    for (const document of Object.values(world.documents)) {
      seen.documentKind.add(document.kind);
      seen.documentStatus.add(document.status);
    }
    for (const item of attention) seen.attentionKind.add(item.kind);
    for (const transcript of Object.values(transcripts)) {
      for (const item of transcript.items) {
        seen.itemType.add(item.type);
        if (item.type === 'tool_call') seen.toolStatus.add(item.status);
      }
    }
    for (const entry of Object.values(world.changes)) {
      for (const files of Object.values(entry.diffs)) {
        for (const file of files) seen.diffStatus.add(file.status);
      }
    }
  }
  return seen;
}

describe('the scenario catalogue', () => {
  const seen = shown();
  const missing = (members: string[], set: Set<string>) => members.filter((m) => !set.has(m));

  it.each([
    ['agent state', AGENT_STATES, seen.agentState],
    ['task status', TASK_STATUSES, seen.taskStatus],
    ['node state', NODE_STATES, seen.nodeState],
    ['drift', DRIFTS, seen.drift],
    ['phase', PHASES, seen.phase],
    ['PR state', PR_STATES, seen.prState],
    ['env state', ENV_STATES, seen.envState],
    ['document kind', DOCUMENT_KINDS, seen.documentKind],
    ['document status', DOCUMENT_STATUSES, seen.documentStatus],
    ['attention kind', ATTENTION_KINDS, seen.attentionKind],
    ['transcript item type', ITEM_TYPES, seen.itemType],
    ['tool-call status', TOOL_STATUSES, seen.toolStatus],
    ['diff status', DIFF_STATUSES, seen.diffStatus],
  ])('shows every %s', (_set, members, set) => {
    expect(missing(members, set)).toEqual([]);
  });

  it.each(NAMES.flatMap((name) => [[name, 'narrow'] as const, [name, 'medium'] as const]))(
    'draws %s and its screen on the %s layout with no error',
    async (name, viewport) => {
      const errors = vi.spyOn(console, 'error');
      const { container, queryClient } = await renderApp({ scenario: name, viewport });
      // The screen the scenario exists to show: a session, a diff, a detail.
      act(() => openFromParams(new URLSearchParams(screenOf(name))));
      await waitFor(() => expect(queryClient.isFetching()).toBe(0));
      expect(container).not.toBeEmptyDOMElement();
      expect(errors).not.toHaveBeenCalled();
    },
  );
});
