import type { Command, CommandError } from './commands';
import type { AgentState } from './entities';
import type { World } from './events';

const WAIT_FOR_COMMAND: Partial<Record<Command['type'], AgentState[]>> = {
  'agent.approve': ['awaiting-permission', 'awaiting-plan-review'],
  'agent.deny': ['awaiting-permission', 'awaiting-plan-review'],
  'agent.answer': ['awaiting-question'],
};

/**
 * Whether the world can take this command. `null` means yes. The codes mirror
 * the daemon's own refusals, so a real backend answers the same way.
 */
export function validateCommand(world: World, cmd: Command): CommandError | null {
  switch (cmd.type) {
    case 'agent.approve':
    case 'agent.deny':
    case 'agent.answer': {
      const agent = world.agents[cmd.agentId];
      if (!agent) return err('unknown_id', `No agent ${cmd.agentId}`);
      if (agent.state === 'exited') return err('agent_exited', `Agent ${agent.id} has exited`);
      if (!agent.pendingRequestId) return err('not_waiting', `Agent ${agent.id} is not waiting`);
      if (agent.pendingRequestId !== cmd.requestId) {
        return err('stale_request', `Request ${cmd.requestId} is no longer pending`);
      }
      const allowed = WAIT_FOR_COMMAND[cmd.type] ?? [];
      if (!allowed.includes(agent.state)) {
        return err(
          'wrong_wait_kind',
          `Agent ${agent.id} is ${agent.state}, not ${allowed.join('/')}`,
        );
      }
      if (cmd.type === 'agent.deny' && !cmd.reason.trim())
        return err('invalid', 'A reason is required');
      if (cmd.type === 'agent.answer' && Object.keys(cmd.answers).length === 0) {
        return err('invalid', 'No answers given');
      }
      return null;
    }
    case 'agent.say':
    case 'agent.stop': {
      const agent = world.agents[cmd.agentId];
      if (!agent) return err('unknown_id', `No agent ${cmd.agentId}`);
      if (agent.state === 'exited') return err('agent_exited', `Agent ${agent.id} has exited`);
      if (cmd.type === 'agent.say' && !cmd.text.trim()) return err('invalid', 'Message is empty');
      return null;
    }
    case 'agent.launch': {
      const task = world.tasks[cmd.taskId];
      if (!task) return err('unknown_id', `No task ${cmd.taskId}`);
      if (!task.actionable) return err('invalid', `Task ${task.id} is not actionable`);
      return null;
    }
    case 'document.approve':
    case 'document.requestChanges':
    case 'comment.add': {
      const doc = world.documents[cmd.documentId];
      if (!doc) return err('unknown_id', `No document ${cmd.documentId}`);
      if (doc.version !== cmd.version) {
        return err('stale_version', `Document is at v${doc.version}, not v${cmd.version}`);
      }
      if (cmd.type === 'comment.add' && !cmd.body.trim()) return err('invalid', 'Comment is empty');
      if (cmd.type !== 'comment.add' && doc.status !== 'awaiting-review') {
        return err('invalid', `Document is ${doc.status}, not awaiting review`);
      }
      if (cmd.type === 'document.requestChanges' && !cmd.summary.trim()) {
        const unresolved = Object.values(world.comments).some(
          (c) => c.documentId === doc.id && c.version === doc.version && !c.resolved,
        );
        if (!unresolved) return err('invalid', 'Say what should change, or leave a comment');
      }
      return null;
    }
    case 'comment.resolve': {
      const comment = world.comments[cmd.commentId];
      if (!comment) return err('unknown_id', `No comment ${cmd.commentId}`);
      if (comment.resolved) return err('invalid', `Comment ${cmd.commentId} is resolved already`);
      return null;
    }
    case 'task.create':
    case 'shaping.start': {
      if (!world.projects[cmd.project]) return err('unknown_id', `No project ${cmd.project}`);
      const text = cmd.type === 'task.create' ? cmd.draft : cmd.brief;
      if (!text.trim()) return err('invalid', 'Nothing to create');
      return null;
    }
  }
}

function err(code: CommandError['code'], message: string): CommandError {
  return { code, message };
}
