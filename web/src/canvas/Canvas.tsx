import { useCallback, useEffect, useMemo, useState } from 'react';
import { Background, ReactFlow, useReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { deriveGraph, type GraphNode } from '../selectors/graph';
import { focusedTaskId } from '../selectors/tabs';
import { useAppStore } from '../store/store';
import { layoutSwimlanes } from './layout';
import { GroupNode, type GroupFlowNode } from './GroupNode';
import { CARD_WIDTH, NodeCard } from './NodeCard';
import { TaskNode, type TaskFlowNode } from './TaskNode';
import styles from './Canvas.module.css';

const nodeTypes = { task: TaskNode, group: GroupNode };

/** Below this zoom the card is hard to read, so expanding eases in to 1. */
const LEGIBLE_ZOOM = 0.75;
/** Roughly half a typical card's height: the card's real height is only known once it renders. */
const CARD_CENTRE_Y = 140;

export function Canvas() {
  const world = useAppStore((s) => s.world);
  const groupBy = useAppStore((s) => s.ui.groupBy);
  const filters = useAppStore((s) => s.ui.filters);
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const expandedTaskId = useAppStore((s) => s.ui.expandedTaskId);
  const expandNode = useAppStore((s) => s.expandNode);
  const collapseNode = useAppStore((s) => s.collapseNode);
  const { getZoom, setCenter } = useReactFlow();
  const focused = focusedTaskId(world, tabs, activeTabKey);

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
        data: { node, focused: node.id === focused, expanded: node.id === expandedTaskId },
      };
    });
    const flowEdges: Edge[] = graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'smoothstep',
    }));
    return {
      nodes: [...groupNodes, ...taskNodes] as Node[],
      edges: flowEdges,
      byId,
      positions,
    };
  }, [world, groupBy, filters, focused, expandedTaskId]);

  // The card stays mounted through its collapse animation, then leaves.
  const [shownTaskId, setShownTaskId] = useState<string | null>(null);
  if (expandedTaskId && expandedTaskId !== shownTaskId) setShownTaskId(expandedTaskId);
  const onClosed = useCallback(() => setShownTaskId(null), []);

  useEffect(() => {
    const at = expandedTaskId ? positions[expandedTaskId] : undefined;
    if (!at || getZoom() >= LEGIBLE_ZOOM) return;
    void setCenter(at.x + CARD_WIDTH / 2, at.y + CARD_CENTRE_Y, { zoom: 1, duration: 300 });
    // Only on expand: a later relayout must not move the viewport.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedTaskId]);

  // A filter that hides the expanded node collapses it, so a later click reopens it.
  useEffect(() => {
    if (expandedTaskId && !byId[expandedTaskId]) collapseNode();
  }, [expandedTaskId, byId, collapseNode]);

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      if (node.type === 'task') expandNode(node.id);
    },
    [expandNode],
  );

  const shown = shownTaskId ? byId[shownTaskId] : undefined;
  const shownAt = shownTaskId ? positions[shownTaskId] : undefined;

  return (
    <div className={styles.canvas} data-testid="canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.2}
        nodesConnectable={false}
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
            open={shown.id === expandedTaskId}
            onClosed={onClosed}
          />
        )}
      </ReactFlow>
    </div>
  );
}
