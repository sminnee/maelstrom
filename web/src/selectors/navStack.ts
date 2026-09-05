import type { AgentId, DocumentId } from '../protocol/ids';

/**
 * One screen in the narrow layout's stack. The wide layout has no stack: a
 * node expands on the canvas and a session or a document opens as a panel tab.
 */
export type MobileScreen =
  | { kind: 'detail'; nodeId: string }
  | { kind: 'session'; agentId: AgentId }
  | { kind: 'document'; documentId: DocumentId };

/** What makes two screens the same screen. */
function keyOf(screen: MobileScreen): string {
  switch (screen.kind) {
    case 'detail':
      return `detail:${screen.nodeId}`;
    case 'session':
      return `session:${screen.agentId}`;
    case 'document':
      return `document:${screen.documentId}`;
  }
}

/**
 * Push a screen, unless the stack already holds it.
 *
 * A screen already in the stack is returned to rather than pushed again, so
 * the depth is bounded by the screens on offer and back never walks a loop.
 * A tap on what is already on top does nothing.
 */
export function pushScreen(stack: MobileScreen[], screen: MobileScreen): MobileScreen[] {
  const at = stack.findIndex((s) => keyOf(s) === keyOf(screen));
  if (at !== -1) return stack.slice(0, at + 1);
  return [...stack, screen];
}

/** Drop the top screen. An empty stack is already at the deck, so it stays. */
export function popScreen(stack: MobileScreen[]): MobileScreen[] {
  return stack.slice(0, -1);
}
