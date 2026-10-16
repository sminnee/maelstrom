import { createElement, type ReactElement } from 'react';
import {
  Archive,
  Ban,
  ArrowLeft,
  ArrowRight,
  Bot,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  CircleDot,
  CirclePlay,
  ExternalLink,
  Eye,
  FoldVertical,
  Folder,
  FolderMinus,
  FolderOpen,
  FolderX,
  GitMerge,
  ListFilter,
  Link2,
  Link2Off,
  MessageSquarePlus,
  OctagonX,
  PanelRight,
  Pencil,
  Play,
  Plus,
  Power,
  PowerOff,
  RefreshCw,
  Rocket,
  RotateCcw,
  RotateCw,
  Save,
  SendHorizontal,
  Sparkles,
  Square,
  SquareTerminal,
  Trash2,
  Undo2,
  X,
  type LucideIcon,
} from 'lucide-react';

/**
 * The app's icon vocabulary: one verb, one icon. A call site names the verb
 * and never imports lucide. See DESIGN.md § Icons for the hand-drawn exceptions.
 */
const ICONS = {
  approve: Check,
  archive: Archive,
  back: ArrowLeft,
  close: X,
  closeWorktree: FolderX,
  comment: MessageSquarePlus,
  compact: FoldVertical,
  delete: Trash2,
  decline: X,
  deny: X,
  dismiss: X,
  disclose: ChevronDown,
  edit: Pencil,
  envRestart: RotateCw,
  envStart: Power,
  envStop: PowerOff,
  external: ExternalLink,
  filter: ListFilter,
  folder: Folder,
  folderOpen: FolderOpen,
  freeAgent: Bot,
  launch: Rocket,
  link: Link2,
  merge: GitMerge,
  new: Plus,
  next: ArrowRight,
  openInPanel: PanelRight,
  pageNext: ChevronRight,
  pagePrevious: ChevronLeft,
  refresh: RotateCw,
  removeWorktree: FolderMinus,
  reopen: Undo2,
  resolve: CheckCheck,
  resume: CirclePlay,
  retry: RotateCcw,
  save: Save,
  send: SendHorizontal,
  start: Play,
  stop: Square,
  suggest: Sparkles,
  sync: RefreshCw,
  terminal: SquareTerminal,
  terminate: OctagonX,
  unlink: Link2Off,
  view: Eye,
} satisfies Record<string, LucideIcon>;

export type ActionVerb = keyof typeof ICONS;

/** The icon for `verb`. `className` sizes an icon outside a button, e.g. in a link. */
export function actionIcon(verb: ActionVerb, className?: string): ReactElement {
  return createElement(ICONS[verb], { className });
}

/**
 * The glyph of a **Step**'s state in the console. A state, not a verb, so it
 * has its own map; lucide stays imported here alone.
 */
const STEP_ICONS = {
  pending: Circle,
  running: CircleDot,
  done: Check,
  refused: Ban,
  failed: X,
} satisfies Record<string, LucideIcon>;

export function stepIcon(state: keyof typeof STEP_ICONS, className?: string): ReactElement {
  return createElement(STEP_ICONS[state], { className });
}
