import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { ReactFlowProvider } from '@xyflow/react';
import type { ReactNode } from 'react';
import { ApiProvider } from '../api/ApiProvider';
import { createFakeServer } from '../test/fakeServer';
import { TaskNode } from './TaskNode';
import type { GraphNode } from '../selectors/graph';
import { byState, idle, stopped, stoppedByPhase } from './taskNode.fixture';

export default { title: 'Canvas / TaskNode' };

/**
 * The node needs two contexts and no live server. `Handle` reads React Flow's,
 * and `useDocuments` runs on every node — so it takes the suite's own fake,
 * which answers that route with a real `ApiClient`. A hand-rolled stub cast to
 * the interface would go on typechecking after the node called a route it does
 * not implement, and fail in the browser instead.
 */
const { api } = createFakeServer();

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});

function Board({ children }: { children: ReactNode }) {
  return (
    <ApiProvider api={api} queryClient={queryClient}>
      <ReactFlowProvider>
        <div
          style={{
            padding: 'var(--space-5)',
            fontFamily: 'var(--font)',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--space-5)',
            alignItems: 'flex-start',
          }}
        >
          {children}
        </div>
      </ReactFlowProvider>
    </ApiProvider>
  );
}

function Label({ children }: { children: ReactNode }) {
  return (
    <span
      style={{
        fontSize: 'var(--text-2xs)',
        letterSpacing: 'var(--tracking-micro)',
        textTransform: 'uppercase',
        color: 'var(--fg-faint)',
      }}
    >
      {children}
    </span>
  );
}

/**
 * The node as the board draws it: unselected, undragged, not expanded.
 *
 * React Flow passes a node's own props; the component reads only `data`, so
 * the rest carry the values every resting node on the board has. They are
 * spelled out rather than cast away, so a change to the node's props shows up
 * here as a type error instead of a blank story.
 */
function Node({ node }: { node: GraphNode }) {
  return (
    <TaskNode
      id={node.id}
      type="task"
      data={{ node, focused: false, expanded: false }}
      dragging={false}
      selected={false}
      isConnectable={false}
      zIndex={0}
      positionAbsoluteX={0}
      positionAbsoluteY={0}
      deletable={false}
      selectable={false}
      draggable={false}
    />
  );
}

function Cell({ label, node }: { label: string; node: GraphNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      <Label>{label}</Label>
      <Node node={node} />
    </div>
  );
}

/**
 * Every state at once. The set only reads as a ladder side by side: this is
 * where you check that stopped is clearly not the same thing as done.
 */
export const AllStates: Story = () => (
  <Board>
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
        gap: 'var(--space-5)',
        width: '100%',
      }}
    >
      {byState.map(({ state, label, node }) => (
        <Cell key={state} label={`${state} · ${label}`} node={node} />
      ))}
    </div>
  </Board>
);

/**
 * The pair the split exists for. A process that is up and waiting at a prompt,
 * against one that has ended and can be resumed.
 */
export const IdleVsStopped: Story = () => (
  <Board>
    <div style={{ display: 'flex', gap: 'var(--space-5)', alignItems: 'flex-start' }}>
      <Cell label={`idle · ${idle.label}`} node={idle.node} />
      <Cell label={`stopped · ${stopped.label}`} node={stopped.node} />
    </div>
  </Board>
);

/**
 * A stopped node in each phase. The phase bar drains to `--phase-dormant`, so
 * this is where you check it still names its phase after draining.
 */
export const StoppedAcrossPhases: Story = () => (
  <Board>
    <div style={{ display: 'flex', gap: 'var(--space-5)', alignItems: 'flex-start' }}>
      {stoppedByPhase.map(({ phase, node }) => (
        <Cell key={phase} label={phase} node={node} />
      ))}
    </div>
  </Board>
);
