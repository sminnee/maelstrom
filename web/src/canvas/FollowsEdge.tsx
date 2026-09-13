import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type EdgeProps } from '@xyflow/react';
import { useState } from 'react';
import { useUpdateTask } from '../api/tasks';
import type { TaskId } from '../protocol/ids';
import { AppButton } from '../ui/AppButton';
import { followsAfterDisconnect } from './connect';
import styles from './FollowsEdge.module.css';

/** What `Canvas` hands each wire: the whole list the cut has to rewrite. */
export interface FollowsEdgeData {
  targetFollows: TaskId[];
  [key: string]: unknown;
}

/**
 * A follows wire, with the button that cuts it.
 *
 * The only custom edge type on the canvas. React Flow draws an edge as bare
 * SVG, which cannot hold a button, so the button goes through
 * `EdgeLabelRenderer` into a div layer above the graph.
 *
 * The target's whole `follows` arrives in `data`. Reading it here instead
 * would risk cutting one wire against a list that had not loaded, which writes
 * an empty list and takes every other wire with it.
 */
export function FollowsEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  style,
  data,
}: EdgeProps) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });
  const [hovered, setHovered] = useState(false);
  const update = useUpdateTask();
  const follows = (data as FollowsEdgeData | undefined)?.targetFollows ?? [];

  const cut = () =>
    update.mutateAsync({
      taskId: target as TaskId,
      fields: { follows: followsAfterDisconnect(follows, source as TaskId) },
    });

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {/* An invisible wide twin of the wire, so the pointer can find it. */}
      <path
        d={path}
        className={styles.hitArea}
        onPointerEnter={() => setHovered(true)}
        onPointerLeave={() => setHovered(false)}
      />
      <EdgeLabelRenderer>
        <AppButton
          variant="quiet"
          className={`${styles.cut} nodrag nopan`}
          data-visible={hovered || undefined}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          aria-label={`Remove the wire from ${source} to ${target}`}
          onPointerEnter={() => setHovered(true)}
          onPointerLeave={() => setHovered(false)}
          onClick={cut}
        >
          ×
        </AppButton>
      </EdgeLabelRenderer>
    </>
  );
}
