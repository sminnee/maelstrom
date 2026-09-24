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
import { AppButton } from '../ui/AppButton';
import { deriveGraph, type GraphNode } from '../selectors/graph';
import { focusedTaskId } from '../selectors/tabs';
import { useAppStore } from '../store/store';
import { layoutSwimlanes } from './layout';
import { GroupNode, type GroupFlowNode } from './GroupNode';
import { canConnect, followsAfterConnect } from './connect';
import { FollowsEdge } from './FollowsEdge';
import { reduceEdges } from './reduce';
import { CARD_WIDTH, NodeCard } from './NodeCard';
import { TaskNode, type TaskFlowNode } from './TaskNode';
import { ZonesNode, type ZonesFlowNode } from './ZonesNode';
import styles from './Canvas.module.css';

const nodeTypes = { task: TaskNode, group: GroupNode, zones: ZonesNode };
const edgeTypes = { follows: FollowsEdge };

/** The strip of zone labels sits above the first lane. */
const ZONES_HEIGHT = 20;

/** Below this zoom the card is hard to read, so expanding eases in to 1. */
const LEGIBLE_ZOOM = 0.75;
/** Roughly half a typical card's height: the card's real height is only known once it renders. */
const CARD_CENTRE_Y = 140;

/** What a refusal says, in the server's own words where it gave any. */
function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function Canvas() {
  const { world, status, errors, retry } = useWorld();
  // A refused rewire has no wire to draw on, so the refusal says so over the
  // board. Local state and `role="alert"`, as `StatusPicker` does it.
  const [rewireError, setRewireError] = useState<string | null>(null);
  const groupBy = useAppStore((s) => s.ui.groupBy);
  const filters = useAppStore((s) => s.ui.filters);
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const expandedNodeId = useAppStore((s) => s.ui.expandedNodeId);
  const expandNode = useAppStore((s) => s.expandNode);
  const collapseNode = useAppStore((s) => s.collapseNode);
  const { getZoom, setCenter } = useReactFlow();
  const updateTask = useUpdateTask();
  const panelOpen = useAppStore((s) => s.ui.panelOpen);
  // A collapsed panel shows no tab, so no node is marked as its source.
  const focused = panelOpen ? focusedTaskId(world, tabs, activeTabKey) : null;

  const { nodes, edges, byId, positions } = useMemo(() => {
    const graph = deriveGraph(world, { groupBy, filters });
    const layout = layoutSwimlanes(graph);
    // Group by none draws no lane: its nodes sit at absolute positions
    // instead of inside a parent.
    const lanes = graph.groups.filter((g) => g.kind !== 'none');
    const laneIds = new Set(lanes.map((g) => g.id));
    const groupNodes: GroupFlowNode[] = lanes.map((group) => {
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
        ...(laneIds.has(node.groupId) ? { parentId: `group:${node.groupId}` } : {}),
        position: laneIds.has(node.groupId) ? local : positions[node.id]!,
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
      nodes: [zonesNode, ...groupNodes, ...taskNodes] as Node[],
      edges: flowEdges,
      byId,
      positions,
    };
  }, [world, groupBy, filters, focused, expandedNodeId]);

  // The card stays mounted through its collapse animation, then leaves.
  const [shownTaskId, setShownTaskId] = useState<string | null>(null);
  if (expandedNodeId && expandedNodeId !== shownTaskId) setShownTaskId(expandedNodeId);
  const onClosed = useCallback(() => setShownTaskId(null), []);

  useEffect(() => {
    const at = expandedNodeId ? positions[expandedNodeId] : undefined;
    if (!at || getZoom() >= LEGIBLE_ZOOM) return;
    void setCenter(at.x + CARD_WIDTH / 2, at.y + CARD_CENTRE_Y, { zoom: 1, duration: 300 });
    // Only on expand: a later relayout must not move the viewport.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedNodeId]);

  // A filter that hides the expanded node collapses it, so a later click reopens it.
  useEffect(() => {
    if (expandedNodeId && !byId[expandedNodeId]) collapseNode();
  }, [expandedNodeId, byId, collapseNode]);

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

  const shown = shownTaskId ? byId[shownTaskId] : undefined;
  const shownAt = shownTaskId ? positions[shownTaskId] : undefined;

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
        <AppButton onClick={retry}>Retry</AppButton>
      </div>
    );
  }

  return (
    <div className={styles.canvas} data-testid="canvas">
      {rewireError && (
        <div className={styles.rewireError} role="alert" data-testid="rewire-error">
          {rewireError}
          <AppButton variant="quiet" onClick={() => setRewireError(null)}>
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
        onPaneClick={collapseNode}
        elementsSelectable
      >
        <Background gap={24} color="var(--border)" />
        {shown && shownAt && (
          <NodeCard
            key={shown.id}
            node={shown}
            position={shownAt}
            open={shown.id === expandedNodeId}
            onClosed={onClosed}
          />
        )}
      </ReactFlow>
    </div>
  );
}
