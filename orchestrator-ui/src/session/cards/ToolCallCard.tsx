import type { ToolCallItem } from '../../protocol/transcript';
import { classifyToolCall, toolCallTitle } from '../toolCards';
import { BashCard } from './BashCard';
import { EditCard } from './EditCard';
import styles from './cards.module.css';

/** One tool call, folded for every kind. The summary line names the tool, its title and its status. */
export function ToolCallCard({ item }: { item: ToolCallItem }) {
  const kind = classifyToolCall(item);
  return (
    <details className={styles.tool} data-tool-kind={kind} data-status={item.status}>
      <summary className={styles.toolHead}>
        <span className={styles.toolName}>{item.tool}</span>
        <span className={styles.toolTitle}>{toolCallTitle(item)}</span>
        <span className={styles.status}>{item.status}</span>
      </summary>
      <ToolBody item={item} kind={kind} />
    </details>
  );
}

/** A tool's input drawn the way its card draws it: the command, the diff, the content. */
export function ToolInput({ tool, input }: { tool: string; input: Record<string, unknown> }) {
  const item: ToolCallItem = {
    id: '',
    ts: '',
    type: 'tool_call',
    toolUseId: '',
    tool,
    input,
    status: 'pending',
  };
  return <ToolBody item={item} kind={classifyToolCall(item)} />;
}

function ToolBody({
  item,
  kind,
}: {
  item: ToolCallItem;
  kind: ReturnType<typeof classifyToolCall>;
}) {
  const s = (v: unknown) => (typeof v === 'string' ? v : '');
  switch (kind) {
    case 'bash':
      return <BashCard command={s(item.input.command)} output={item.output} status={item.status} />;
    case 'edit':
      return (
        <div>
          <EditCard oldString={s(item.input.old_string)} newString={s(item.input.new_string)} />
          <Failure item={item} />
        </div>
      );
    case 'write':
      return (
        <div>
          <pre className={styles.code}>{s(item.input.content)}</pre>
          <Failure item={item} />
        </div>
      );
    case 'read':
      return item.output ? <pre className={styles.code}>{item.output}</pre> : null;
    default:
      return (
        <div>
          <pre className={styles.code}>{JSON.stringify(item.input, null, 2)}</pre>
          {item.output && <pre className={styles.code}>{item.output}</pre>}
        </div>
      );
  }
}

/** The tool result when the call did not succeed; a success needs no echo. */
function Failure({ item }: { item: ToolCallItem }) {
  if (item.status !== 'error' && item.status !== 'denied') return null;
  return (
    <pre className={styles.output} data-error>
      {item.output}
    </pre>
  );
}
