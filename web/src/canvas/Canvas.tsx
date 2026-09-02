import { useCallback, useMemo } from 'react';
import { Background, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { deriveGraph } from '../selectors/graph';
import { focusedTaskId, summaryTab } from '../selectors/tabs';
import { useAppStore } from '../store/store';
import { layoutSwimlanes } from './layout';
import { GroupNode, type GroupFlowNode } from './GroupNode';
import { TaskNode, type TaskFlowNode } from './TaskNode';
import styles from './Canvas.module.css';

const nodeTypes = { task: TaskNode, group: GroupNode };

export function Canvas() {
  const world = useAppStore((s) => s.world);
  const groupBy = useAppStore((s) => s.ui.groupBy);
  const filters = useAppStore((s) => s.ui.filters);
  const tabs = useAppStore((s) => s.ui.tabs);
  const activeTabKey = useAppStore((s) => s.ui.activeTabKey);
  const openTab = useAppStore((s) => s.openTab);
  const focused = focusedTaskId(world, tabs, activeTabKey);

  const { nodes, edges } = useMemo(() => {
    const graph = deriveGraph(world, { groupBy, filters });
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
    const taskNodes: TaskFlowNode[] = graph.nodes.map((node) => ({
      id: node.id,
      type: 'task',
      parentId: `group:${node.groupId}`,
      position: layout.nodes[node.id]!,
      width: layout.nodeSize.width,
      height: layout.nodeSize.height,
      draggable: false,
      data: { node, focused: node.id === focused },
    }));
    const flowEdges: Edge[] = graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'smoothstep',
    }));
    return { nodes: [...groupNodes, ...taskNodes] as Node[], edges: flowEdges };
  }, [world, groupBy, filters, focused]);

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      if (node.type === 'task') openTab(summaryTab(node.id));
    },
    [openTab],
  );

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
        elementsSelectable
      >
        <Background gap={24} color="var(--border)" />
      </ReactFlow>
    </div>
  );
}
