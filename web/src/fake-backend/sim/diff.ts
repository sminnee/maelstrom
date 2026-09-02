/** A unified diff of one Edit's old and new text, as the fake's tool result. */
export function unifiedDiff(path: string, oldText: string, newText: string): string {
  const before = oldText.split('\n');
  const after = newText.split('\n');
  const lines = [
    `--- a/${path}`,
    `+++ b/${path}`,
    `@@ -1,${before.length} +1,${after.length} @@`,
    ...before.map((l) => `-${l}`),
    ...after.map((l) => `+${l}`),
  ];
  return lines.join('\n');
}
