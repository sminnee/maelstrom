import { useState, type DragEvent } from 'react';
import { useSetStatus } from '../api/tasks';
import { useWorld } from '../api/useWorld';
import { useUnitOperation } from '../console/useConsole';
import { ZONES, type Zone } from '../protocol/progress';
import { dropStatus, emptyZoneWords, zoneLabel } from '../selectors/deck';
import { showsProject } from '../selectors/filters';
import type { GraphNode } from '../selectors/graph';
import { nodeUnit } from '../selectors/operations';
import { cardOf } from '../nav/useCard';
import { toHref, withLoc } from '../nav/location';
import { useLoc } from '../nav/useNav';
import { actionIcon } from '../ui/actionIcons';
import { AppButton } from '../ui/AppButton';
import { DeckRow } from './DeckRow';
import { useDeck } from './useDeck';
import listStyles from './DeckList.module.css';
import styles from './DeskKanban.module.css';

/** The drag's data type. The value is the node id. */
const DRAG_TYPE = 'application/x-mael-node';

/**
 * The desk in kanban mode: one column of cards per zone, in board order. With
 * the deck's lanes, one lane per project crosses the four columns. `dropStatus`
 * rules each drop.
 */
export function DeskKanban() {
  const { world, status, errors, retry } = useWorld();
  const deck = useDeck();
  const loc = useLoc();
  const setStatus = useSetStatus();
  const showProject = showsProject(loc.filters);
  // The node in the drag. `dataTransfer` cannot be read during a drag over, only on the drop.
  const [dragged, setDragged] = useState<GraphNode | null>(null);
  const [over, setOver] = useState<{ zone: Zone; lane: string | null } | null>(null);
  // A refused write says so over the board, as a refused rewire does on the canvas.
  const [dropError, setDropError] = useState<string | null>(null);

  if (status === 'loading') {
    return (
      <div className={listStyles.frame} data-testid="deck-loading">
        Loading the world…
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div className={listStyles.frame} role="alert">
        <div>Could not load the world: {errors[0]?.message ?? 'unknown error'}</div>
        <AppButton icon={actionIcon('retry')} onClick={retry}>
          Retry
        </AppButton>
      </div>
    );
  }

  /**
   * Whether a drop of the dragged card on the cell of `zone` in `lane` sets a
   * status. A drop cannot move a task to another project, so a lane's cell
   * takes only its own lane's cards. `lane` is null on the board with no lanes.
   */
  const target = (zone: Zone, lane: string | null) =>
    dragged && (lane === null || dragged.groupId === lane)
      ? dropStatus(dragged, zone, world.tasks)
      : null;
  const endDrag = () => {
    setDragged(null);
    setOver(null);
  };

  const dropCell = (zone: Zone, lane: string | null) => {
    const accepts = target(zone, lane) !== null;
    return {
      'data-zone': zone,
      'data-lane': lane ?? undefined,
      'data-drop': (accepts && over?.zone === zone && over.lane === lane) || undefined,
      onDragOver: (e: DragEvent) => {
        if (!accepts) return;
        // Cancelling the drag over is what makes the cell a drop target.
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        setOver({ zone, lane });
      },
      onDragLeave: (e: DragEvent) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(null);
      },
      onDrop: (e: DragEvent) => {
        e.preventDefault();
        const node = dragged;
        const next = target(zone, lane);
        endDrag();
        if (!node?.task || !next) return;
        setDropError(null);
        setStatus
          .mutateAsync({ taskId: node.task.id, status: next })
          .catch((err: unknown) => setDropError(err instanceof Error ? err.message : String(err)));
      },
    };
  };

  const cards = (rows: GraphNode[], named: boolean) =>
    rows.map((node) => (
      <KanbanCard
        key={node.id}
        node={node}
        showProject={named}
        to={toHref(withLoc(loc, { card: cardOf(node) }))}
        onDragStart={() => setDragged(node)}
        onDragEnd={endDrag}
      />
    ));

  const zoneHead = (zone: Zone) => (
    <h2 className={styles.head}>
      <span className="nowrap">{zoneLabel(zone)}</span>
      <span className={listStyles.count} data-testid="zone-count">
        {deck.counts[zone]}
      </span>
    </h2>
  );

  const error = dropError && (
    <div className={styles.dropError} role="alert">
      {dropError}
      <AppButton icon={actionIcon('dismiss')} variant="quiet" onClick={() => setDropError(null)}>
        Dismiss
      </AppButton>
    </div>
  );

  if (deck.lanes.length > 0) {
    return (
      // The laned board scrolls, so the error sits in a frame round it that does not.
      <div className={styles.frame} data-testid="desk-kanban">
        {error}
        <div className={`${styles.board} ${styles.laned}`}>
          {ZONES.map((zone) => (
            <div key={zone} className={styles.zoneHead} data-zone={zone}>
              {zoneHead(zone)}
            </div>
          ))}
          {deck.lanes.map((lane) => (
            <div
              key={lane.id}
              role="group"
              aria-label={lane.label}
              className={styles.lane}
              data-lane={lane.id}
            >
              <h3 className={styles.laneHead}>
                <span className="nowrap">{lane.label}</span>
                <span className={listStyles.count} data-testid="lane-count">
                  {lane.count}
                </span>
              </h3>
              {ZONES.map((zone) => (
                <section
                  key={zone}
                  className={`${styles.column} ${styles.cell}`}
                  aria-label={`${zoneLabel(zone)}, ${lane.label}`}
                  {...dropCell(zone, lane.id)}
                >
                  {/* A card in a lane does not name its project: the lane does. */}
                  {cards(lane.zones[zone], false)}
                </section>
              ))}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className={styles.board} data-testid="desk-kanban">
      {error}
      {ZONES.map((zone) => {
        const rows = deck.zones[zone];
        return (
          <section
            key={zone}
            className={styles.column}
            aria-label={zoneLabel(zone)}
            {...dropCell(zone, null)}
          >
            {zoneHead(zone)}
            <div className={styles.cards}>
              {rows.length === 0 ? (
                <p className={listStyles.empty}>{emptyZoneWords(zone)}</p>
              ) : (
                cards(rows, showProject)
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

/** One card on the board: a deck row that drags while no operation runs on its unit. */
function KanbanCard({
  node,
  showProject,
  to,
  onDragStart,
  onDragEnd,
}: {
  node: GraphNode;
  showProject: boolean;
  to: string;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  const op = useUnitOperation(nodeUnit(node));
  const draggable = node.kind === 'task' && op?.state !== 'running';
  return (
    <DeckRow
      node={node}
      showProject={showProject}
      to={to}
      drag={
        draggable
          ? {
              onDragStart: (e: DragEvent) => {
                e.dataTransfer.setData(DRAG_TYPE, node.id);
                e.dataTransfer.effectAllowed = 'move';
                onDragStart();
              },
              onDragEnd,
            }
          : undefined
      }
    />
  );
}
