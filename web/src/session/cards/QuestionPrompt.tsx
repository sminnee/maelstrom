import { useState } from 'react';
import type { Question, QuestionItem } from '../../protocol/transcript';
import styles from './cards.module.css';

/**
 * The agent's questions, answered inline. One single-choice question sends on
 * the click; several questions, or a multi-select, collect a draft and send it
 * whole, because the daemon resolves the request on the first answer it gets.
 */
export function QuestionPrompt({
  item,
  onAnswer,
}: {
  item: QuestionItem;
  onAnswer?: (answers: Record<string, string>) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string[]>>({});
  const answered = item.answers !== undefined && Object.keys(item.answers).length > 0;
  const first = item.questions[0];
  const sendsOnClick = item.questions.length === 1 && first !== undefined && !first.multiSelect;
  const complete = item.questions.every((q) => (draft[q.question]?.length ?? 0) > 0);

  const choose = (q: Question, label: string) => {
    if (sendsOnClick) {
      onAnswer?.({ [q.question]: label });
      return;
    }
    const current = draft[q.question] ?? [];
    const next = q.multiSelect
      ? current.includes(label)
        ? current.filter((l) => l !== label)
        : [...current, label]
      : [label];
    setDraft({ ...draft, [q.question]: next });
  };
  const submit = () =>
    onAnswer?.(
      Object.fromEntries(
        item.questions.map((q) => [q.question, (draft[q.question] ?? []).join(', ')]),
      ),
    );

  return (
    <div className={styles.prompt} data-answered={answered || undefined}>
      {item.questions.map((q) => (
        <div key={q.question} className={styles.question}>
          <div className={styles.qhead}>
            {q.header || 'Question'}
            {q.multiSelect ? ' · choose any' : ''}
          </div>
          <div>{q.question}</div>
          {answered ? (
            <div className={styles.answer}>→ {item.answers?.[q.question] ?? '(no answer)'}</div>
          ) : (
            <div className={styles.options}>
              {q.options.map((o) => (
                <button
                  key={o.label}
                  type="button"
                  title={o.description}
                  disabled={!onAnswer}
                  aria-pressed={
                    sendsOnClick ? undefined : (draft[q.question] ?? []).includes(o.label)
                  }
                  onClick={() => choose(q, o.label)}
                >
                  {o.label}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
      {!answered && !sendsOnClick && (
        <div className={styles.options}>
          <button type="button" disabled={!onAnswer || !complete} onClick={submit}>
            Answer
          </button>
        </div>
      )}
    </div>
  );
}
