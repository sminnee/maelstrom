import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Background,
  ReactFlow,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useUpdateTask } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import type { TaskId } from '../protocol/ids';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { deriveGraph, type GraphNode } from '../selectors/graph';
import { focusedTaskId } from '../selectors/tabs';
import { useShowing } from '../layout/useShowing';
import { useAppStore } from '../store/store';
import { layoutSwimlanes, type WorktreeBox } from './layout';
import { GroupNode, type GroupFlowNode } from './GroupNode';
import { canConnect, followsAfterConnect } from './connect';
import { FollowsEdge } from './FollowsEdge';
import { reduceEdges } from './reduce';
import { CARD_WIDTH, NodeCard } from './NodeCard';
import { WORKTREE_CARD_WIDTH, WorktreeCard } from './WorktreeCard';
import { TaskNode, type TaskFlowNode } from './TaskNode';
import { WorktreeBoxNode, type WorktreeBoxFlowNode } from './WorktreeBoxNode';
import { ZonesNode, type ZonesFlowNode } from './ZonesNode';
import styles from './Canvas.module.css';

const nodeTypes = {
  task: TaskNode,
  group: GroupNode,
  zones: ZonesNode,
  worktreeBox: WorktreeBoxNode,
};
const edgeTypes = { follows: FollowsEdge };

/** The strip of zone labels sits above the first lane. */
const ZONES_HEIGHT = 24;

/** Below this zoom the card is hard to read, so expanding eases in to 1. */
const LEGIBLE_ZOOM = 0.75;
/** Roughly half a typical card's height: the card's real height is only known once it renders. */
const CARD_CENTRE_Y = 140;

/** What the canvas has grown into a card: a node, or a worktree. One at a time. */
interface Expanded {
  kind: 'node' | 'worktree';
  id: string;
}

/** What a refusal says, in the server's own words where it gave any. */
function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function Canvas() {
  const { world, status, errors, retry } = useWorld();
  // A refused rewire has no wire to draw on, so the refusal says so over the
  // board. Local state and `role="alert"`, as `StatusPicker` does it.
  const [rewireError, setRewireError] = useState<string | null>(null);
  const filters = useAppStore((s) => s.ui.filters);
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const expandedNodeId = useAppStore((s) => s.ui.expandedNodeId);
  const expandedWorktreeId = useAppStore((s) => s.ui.expandedWorktreeId);
  const expandNode = useAppStore((s) => s.expandNode);
  const collapseCard = useAppStore((s) => s.collapseCard);
  const { getZoom, setCenter } = useReactFlow();
  const updateTask = useUpdateTask();
  const panelShowing = useShowing().includes('tabs');
  // A panel off screen shows no tab, so no node is marked as its source.
  const focused = panelShowing ? focusedTaskId(world, tabs, activeTabKey) : null;

  const { nodes, edges, byId, positions, boxes } = useMemo(() => {
    const graph = deriveGraph(world, { filters });
    const layout = layoutSwimlanes(graph);
    const groupNodes: GroupFlowNode[] = graph.groups.map((group) => {
      const box = layout.groups[group.id]!;
      return {
        id: `group:${group.id}`,
        type: 'group',
        position: { x: box.x, y: box.y },
        width: box.width,
        height: box.height,
        draggable: false,
        selectable: false,
        data: { group },
      };
    });
    const zonesNode: ZonesFlowNode = {
      id: 'zones',
      type: 'zones',
      position: { x: 0, y: -ZONES_HEIGHT },
      width: layout.boardWidth,
      height: ZONES_HEIGHT,
      draggable: false,
      selectable: false,
      data: { zones: layout.zones },
    };
    // Each drawn box, with its corner on the board: where its card opens.
    const boxes: Record<string, { box: WorktreeBox; at: { x: number; y: number } }> = {};
    const boxNodes: WorktreeBoxFlowNode[] = graph.groups.flatMap((group) =>
      (layout.worktreeBoxes[group.id] ?? []).map((box) => {
        const lane = layout.groups[group.id]!;
        boxes[box.worktree.id] = { box, at: { x: lane.x + box.x, y: lane.y + box.y } };
        return boxNode(group.id, box);
      }),
    );
    function boxNode(groupId: string, box: WorktreeBox): WorktreeBoxFlowNode {
      return {
        id: `worktree-box:${groupId}:${box.worktree.id}`,
        type: 'worktreeBox',
        parentId: `group:${groupId}`,
        position: { x: box.x, y: box.y },
        width: box.width,
        height: box.height,
        draggable: false,
        selectable: false,
        data: { box },
      };
    }
    const positions: Record<string, { x: number; y: number }> = {};
    const byId: Record<string, GraphNode> = {};
    const taskNodes: TaskFlowNode[] = graph.nodes.map((node) => {
      const box = layout.groups[node.groupId]!;
      const local = layout.nodes[node.id]!;
      positions[node.id] = { x: box.x + local.x, y: box.y + local.y };
      byId[node.id] = node;
      return {
        id: node.id,
        type: 'task',
        parentId: `group:${node.groupId}`,
        position: local,
        width: layout.nodeSize.width,
        height: layout.nodeSize.height,
        draggable: false,
        data: { node, focused: node.id === focused, expanded: node.id === expandedNodeId },
      };
    });
    // Drawn edges only. `layoutSwimlanes` above took the full set, so hiding a
    // redundant wire moves no column and changes nothing on disk.
    const drawn = reduceEdges(
      graph.edges,
      graph.nodes.map((node) => ({ id: node.id, status: node.task?.status })),
    );
    // The edge carries the target's whole `follows`, so cutting one wire can
    // rewrite the list without re-reading it: a stale read here would clear
    // every other wire on that task.
    const flowEdges: Edge[] = drawn.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'follows',
      data: { targetFollows: byId[e.target]?.task?.follows ?? [] },
    }));
    return {
      nodes: [zonesNode, ...groupNodes, ...boxNodes, ...taskNodes] as Node[],
      edges: flowEdges,
      byId,
      positions,
      boxes,
    };
  }, [world, filters, focused, expandedNodeId]);

  const expanded: Expanded | null = expandedNodeId
    ? { kind: 'node', id: expandedNodeId }
    : expandedWorktreeId
      ? { kind: 'worktree', id: expandedWorktreeId }
      : null;
  // The card stays mounted through its collapse animation, then leaves.
  const [shown, setShown] = useState<Expanded | null>(null);
  if (expanded && (expanded.kind !== shown?.kind || expanded.id !== shown.id)) setShown(expanded);
  const onClosed = useCallback(() => setShown(null), []);
  // A collapsed card whose node or box is gone has nothing to draw, so it
  // plays no collapse to end on. Forget it, or it flashes when the box returns.
  if (!expanded && shown && !(shown.kind === 'node' ? byId[shown.id] : boxes[shown.id])) {
    setShown(null);
  }

  useEffect(() => {
    const at = expandedNodeId
      ? positions[expandedNodeId]
      : expandedWorktreeId
        ? boxes[expandedWorktreeId]?.at
        : undefined;
    if (!at || getZoom() >= LEGIBLE_ZOOM) return;
    const width = expandedNodeId ? CARD_WIDTH : WORKTREE_CARD_WIDTH;
    void setCenter(at.x + width / 2, at.y + CARD_CENTRE_Y, { zoom: 1, duration: 300 });
    // Only on expand: a later relayout must not move the viewport.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedNodeId, expandedWorktreeId]);

  // A filter that hides the expanded node collapses it, so a later click
  // reopens it. A worktree whose box no longer draws collapses the same way:
  // it closed, or a filter now hides the empty boxes.
  // Not while the world loads: nothing draws yet, so nothing has left.
  useEffect(() => {
    if (status !== 'ready') return;
    const gone =
      (expandedNodeId && !byId[expandedNodeId]) ||
      (expandedWorktreeId && !boxes[expandedWorktreeId]);
    if (!gone) return;
    collapseCard();
  }, [status, expandedNodeId, expandedWorktreeId, byId, boxes, collapseCard]);

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      if (node.type === 'task') expandNode(node.id);
    },
    [expandNode],
  );

  // Direction is followed -> follower, the way `deriveGraph` builds an edge,
  // so a drag writes the *target's* follows.
  const onConnect = useCallback(
    (connection: Connection) => {
      if (!canConnect(connection)) return;
      const target = byId[connection.target]?.task;
      if (!target) return;
      setRewireError(null);
      updateTask
        .mutateAsync({
          taskId: target.id,
          fields: { follows: followsAfterConnect(target.follows, connection.source as TaskId) },
        })
        // The refusal has nowhere to draw on a wire that never appeared, so it
        // says so where a canvas error already goes rather than going unheard.
        .catch((err: unknown) => setRewireError(errorText(err)));
    },
    [byId, updateTask],
  );

  const shownNode = shown?.kind === 'node' ? byId[shown.id] : undefined;
  const shownNodeAt = shownNode ? positions[shownNode.id] : undefined;
  const shownBox = shown?.kind === 'worktree' ? boxes[shown.id] : undefined;

  // No nodes without lanes: the canvas waits for every table it draws from.
  if (status === 'loading') {
    return (
      <div className={styles.frame} data-testid="canvas-loading">
        Loading the world…
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div className={styles.frame} role="alert" data-testid="canvas-error">
        <div>Could not load the world: {errors[0]?.message ?? 'unknown error'}</div>
        <AppButton icon={actionIcon('retry')} onClick={retry}>
          Retry
        </AppButton>
      </div>
    );
  }

  return (
    <div className={styles.canvas} data-testid="canvas">
      {rewireError && (
        <div className={styles.rewireError} role="alert" data-testid="rewire-error">
          {rewireError}
          <AppButton
            icon={actionIcon('dismiss')}
            variant="quiet"
            onClick={() => setRewireError(null)}
          >
            Dismiss
          </AppButton>
        </div>
      )}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.2}
        edgeTypes={edgeTypes}
        nodesConnectable
        onConnect={onConnect}
        isValidConnection={canConnect}
        onNodeClick={onNodeClick}
        onPaneClick={collapseCard}
        elementsSelectable
      >
        <Background gap={24} color="var(--border)" />
        {shownNode && shownNodeAt && (
          <NodeCard
            key={shownNode.id}
            node={shownNode}
            position={shownNodeAt}
            open={shownNode.id === expandedNodeId}
            onClosed={onClosed}
          />
        )}
        {shownBox && (
          <WorktreeCard
            key={shownBox.box.worktree.id}
            worktree={shownBox.box.worktree}
            position={shownBox.at}
            open={shownBox.box.worktree.id === expandedWorktreeId}
            onClosed={onClosed}
          />
        )}
      </ReactFlow>
    </div>
  );
}
