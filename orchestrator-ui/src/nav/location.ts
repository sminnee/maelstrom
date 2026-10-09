import { generatePath, matchRoutes, type Params } from 'react-router';
import type { TaskId } from '../protocol/ids';
import type { Zone } from '../protocol/progress';
import { TASK_STATUSES, type TaskStatus } from '../protocol/entities';
import { AGENT_STATUS_LABELS, noFilters, type Filters } from '../selectors/filters';
import { LIVE_STATUSES, noListFilters, type ListFilters } from '../selectors/taskList';
import { noWorktreeFilters, type WorktreeFilters } from '../selectors/worktrees';
import { changesTab, devEnvTab, documentTab, sessionTab } from '../selectors/tabs';
import type { PanelTab, View } from '../store/uiSlice';

/**
 * The card the desk has open: a task's, a free agent's, or a worktree's. The narrow layout
 * draws it as the detail screen.
 */
export interface Card {
  kind: 'task' | 'agent' | 'worktree';
  id: string;
}

/** The **Location**, parsed from the URL. See docs/dev/orchestrator-ui.md, "The URL". */
export interface Loc {
  view: View;
  /** Only on the desk: a card is drawn on the canvas, and the deck list stands for it. */
  card: Card | null;
  /** The active panel tab. The narrow layout draws it as a screen over the card or the view. */
  panel: PanelTab | null;
  filters: Filters;
  listFilters: ListFilters;
  worktreeFilters: WorktreeFilters;
  /** The deck list's zone. Narrow layout only. */
  zone: Zone;
  /** The task the editor is open on. */
  edit: TaskId | null;
  /** Whether the new-work form is open. */
  newWork: boolean;
}

/** A change to a location. Filters merge into the current ones. */
export type LocPatch = Partial<Omit<Loc, 'filters' | 'listFilters' | 'worktreeFilters'>> & {
  filters?: Partial<Filters>;
  listFilters?: Partial<ListFilters>;
  worktreeFilters?: Partial<WorktreeFilters>;
};

const DEFAULT_ZONE: Zone = 'running';
const ZONES: readonly Zone[] = ['done', 'running', 'notStarted'];
/** The `status` value for every status: an empty selection. */
const EVERY_STATUS = 'all';

/** A screen: the view a path draws, and the kind of card it opens. */
export interface Screen {
  path: string;
  view: View;
  card: Card['kind'] | null;
}

/**
 * The screens, one route each: `routes.tsx` declares them, React Router matches them, and
 * `toHref` fills them. A task id can hold `/`, so the task path ends in a splat.
 */
export const SCREENS: readonly Screen[] = [
  { path: '/desk', view: 'canvas', card: null },
  { path: '/desk/task/*', view: 'canvas', card: 'task' },
  { path: '/desk/agent/:id', view: 'canvas', card: 'agent' },
  { path: '/desk/worktree/:id', view: 'canvas', card: 'worktree' },
  { path: '/tasks', view: 'list', card: null },
  { path: '/worktrees', view: 'worktrees', card: null },
  { path: '/comms', view: 'comms', card: null },
];

/** The location the app opens on: the desk, with nothing open and no filter. */
export function defaultLoc(): Loc {
  return {
    view: 'canvas',
    card: null,
    panel: null,
    filters: noFilters(),
    listFilters: noListFilters(),
    worktreeFilters: noWorktreeFilters(),
    zone: DEFAULT_ZONE,
    edit: null,
    newWork: false,
  };
}

/** `loc` with `patch` laid over it. A card is on the desk, so opening one moves there. */
export function withLoc(loc: Loc, patch: LocPatch): Loc {
  const next: Loc = {
    ...loc,
    ...patch,
    filters: { ...loc.filters, ...patch.filters },
    listFilters: { ...loc.listFilters, ...patch.listFilters },
    worktreeFilters: { ...loc.worktreeFilters, ...patch.worktreeFilters },
  };
  if (patch.card) next.view = 'canvas';
  if (next.view !== 'canvas') next.card = null;
  return next;
}

/** A segment of a path or a value of the search. `/` and `,` read better left as they are. */
const encode = (s: string) => encodeURIComponent(s).replace(/%2F/gi, '/').replace(/%2C/gi, ',');

function panelValue(tab: PanelTab): string {
  switch (tab.kind) {
    case 'session':
      return `session/${tab.agentId}`;
    case 'document':
      return `document/${tab.documentId}`;
    case 'changes':
      return `changes/${tab.worktreeId}`;
    case 'devenv':
      return `devenv/${tab.worktreeId}/${tab.service}`;
  }
}

function parsePanel(value: string | null): PanelTab | null {
  if (!value) return null;
  const sep = value.indexOf('/');
  if (sep < 0) return null;
  const kind = value.slice(0, sep);
  const rest = value.slice(sep + 1);
  if (!rest) return null;
  switch (kind) {
    case 'session':
      return sessionTab(rest);
    case 'document':
      return documentTab(rest);
    case 'changes':
      return changesTab(rest);
    case 'devenv': {
      // The service is the last segment: a service name holds no `/`.
      const at = rest.lastIndexOf('/');
      return at > 0 && at < rest.length - 1
        ? devEnvTab(rest.slice(0, at), rest.slice(at + 1))
        : null;
    }
    default:
      return null;
  }
}

const sameSet = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((x) => b.includes(x));

function parseStatuses(value: string | null): TaskStatus[] {
  if (value === null) return [...LIVE_STATUSES];
  if (value === EVERY_STATUS) return [];
  const statuses = value
    .split(',')
    .filter((s): s is TaskStatus => (TASK_STATUSES as readonly string[]).includes(s));
  // An empty pick is every status, so a value that names none is the default, not that.
  return statuses.length > 0 ? statuses : [...LIVE_STATUSES];
}

function statusValue(statuses: TaskStatus[]): string | null {
  if (sameSet(statuses, LIVE_STATUSES)) return null;
  return statuses.length === 0 ? EVERY_STATUS : statuses.join(',');
}

/** The routes of the screens, each with its screen as its handle. */
export const screenRoutes = () => SCREENS.map((screen) => ({ path: screen.path, handle: screen }));

/** Whether a route handle is a screen: the route matched is one of the app's screens. */
export const isScreen = (handle: unknown): handle is Screen =>
  typeof handle === 'object' && handle !== null && 'view' in handle && 'path' in handle;

/** The location of a matched screen, its route params and the search. */
export function locAt(screen: Screen, params: Params, search: string): Loc {
  const id = screen.card === 'task' ? params['*'] : params.id;
  const q = new URLSearchParams(search);
  const agents = q.get('agents');
  const zone = q.get('zone') as Zone | null;
  return {
    view: screen.view,
    // `/desk/task` matches the splat with nothing in it: the desk, with no card.
    card: screen.card && id ? { kind: screen.card, id } : null,
    panel: parsePanel(q.get('panel')),
    filters: {
      project: q.get('project') || null,
      branch: q.get('branch') || null,
      agentStatus:
        agents && agents in AGENT_STATUS_LABELS ? (agents as Filters['agentStatus']) : 'all',
      text: q.get('q') ?? '',
      comm: q.get('comm') || null,
    },
    listFilters: { statuses: parseStatuses(q.get('status')) },
    worktreeFilters: { showClosed: q.get('closed') === '1' },
    zone: zone && ZONES.includes(zone) ? zone : DEFAULT_ZONE,
    edit: q.get('edit') || null,
    newWork: q.get('new') === '1',
  };
}

/** The location a URL names, or `null` for a path the app has no screen for. */
export function parseLocation(pathname: string, search: string): Loc | null {
  const match = matchRoutes(screenRoutes(), pathname)?.at(-1);
  return match ? locAt(match.route.handle, match.params, search) : null;
}

/** The URL of a location: its path and search, every default left out. */
export function toHref(loc: Loc): string {
  const card = loc.view === 'canvas' ? loc.card : null;
  const screen = SCREENS.find((s) => s.view === loc.view && s.card === (card?.kind ?? null))!;
  // `generatePath` encodes a param but not the splat, so the task id's segments are encoded here.
  const path = generatePath(screen.path, {
    id: card?.id,
    '*': card?.id.split('/').map(encodeURIComponent).join('/'),
  });
  const params: [string, string | null][] = [
    ['panel', loc.panel && panelValue(loc.panel)],
    ['project', loc.filters.project],
    ['branch', loc.filters.branch],
    [
      'agents',
      loc.filters.agentStatus && loc.filters.agentStatus !== 'all' ? loc.filters.agentStatus : null,
    ],
    ['status', statusValue(loc.listFilters.statuses)],
    ['q', loc.filters.text || null],
    ['comm', loc.filters.comm],
    ['closed', loc.worktreeFilters.showClosed ? '1' : null],
    ['zone', loc.zone !== DEFAULT_ZONE ? loc.zone : null],
    ['edit', loc.edit],
    ['new', loc.newWork ? '1' : null],
  ];
  const search = params
    .filter((p): p is [string, string] => p[1] !== null)
    .map(([k, v]) => `${k}=${encode(v)}`)
    .join('&');
  return search ? `${path}?${search}` : path;
}

/** The node a card stands for, else `null`: a worktree's card is no node's. */
export const cardNodeId = (card: Card | null): string | null =>
  card && card.kind !== 'worktree' ? card.id : null;

/** The worktree a card stands for, else `null`. */
export const cardWorktreeId = (card: Card | null): string | null =>
  card?.kind === 'worktree' ? card.id : null;
