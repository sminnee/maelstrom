/**
 * The acts a decision offers, and the request each sends. `DecideRow` offers
 * them; `DecisionCard` and `SessionTab` send them.
 */

/** `remember` — see `CONTEXT.md`, "Remembered approval". */
export type Decision = 'approve' | 'remember' | 'deny' | 'ask';

/** The deny reason an Ask sends. See `CONTEXT.md`, "Ask". */
export const askReason = (question: string) =>
  'The user has a question about the plan. This is not a request to change it. ' +
  'Answer the question in chat. Do not edit the plan file or call ExitPlanMode again ' +
  `until the user asks you to.\n\nQuestion: ${question}`;

/**
 * What a decision sends to the agent: a deny with its reason, or an approve.
 * Every caller maps through here, so a new decision cannot fall through to approve.
 */
export function decisionCall(
  decision: Decision,
  reason: string,
): { act: 'deny'; reason: string } | { act: 'approve'; remember: boolean } {
  switch (decision) {
    case 'deny':
      return { act: 'deny', reason };
    case 'ask':
      return { act: 'deny', reason: askReason(reason) };
    case 'approve':
      return { act: 'approve', remember: false };
    case 'remember':
      return { act: 'approve', remember: true };
  }
}
