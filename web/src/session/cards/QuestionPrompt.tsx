import { useId, useState } from 'react';
import type { Question, QuestionItem } from '../../protocol/transcript';
import styles from './cards.module.css';

const OTHER = '__other__';

interface Draft {
  /** Chosen option labels per question, or [OTHER] when the free text is the answer. */
  chosen: Record<string, string[]>;
  /** The free text per question. */
  other: Record<string, string>;
}

/** The answer a draft holds for `q`: the chosen labels, then the Other text when chosen. */
function answerFor(draft: Draft, q: Question): string {
  const chosen = draft.chosen[q.question] ?? [];
  const labels = chosen.filter((l) => l !== OTHER);
  const other = chosen.includes(OTHER) ? (draft.other[q.question] ?? '').trim() : '';
  return [...labels, other].filter(Boolean).join(', ');
}

/**
 * The agent's questions in AskUserQuestion's shape: one question at a time,
 * each option with its description, an Other row, and one submit. Nothing
 * sends on click; the last question's Answer sends every answer keyed by
 * question text, as the daemon files them, because the daemon resolves the
 * request on the first answer it gets.
 */
export function QuestionPrompt({
  item,
  onAnswer,
}: {
  item: QuestionItem;
  onAnswer?: (answers: Record<string, string>) => void;
}) {
  const [draft, setDraft] = useState<Draft>({ chosen: {}, other: {} });
  const [step, setStep] = useState(0);
  const groupId = useId();
  const answered = item.answers !== undefined && Object.keys(item.answers).length > 0;

  if (answered) {
    return (
      <div className={styles.prompt} data-answered>
        <div className={styles.qhead}>Answered</div>
        {item.questions.map((q) => (
          <div key={q.question} className={styles.answeredRow}>
            <span className={styles.stepHeader}>{q.header || 'Question'}</span>
            <span className={styles.questionText}>{q.question}</span>
            <span className={styles.answer}>{item.answers?.[q.question] ?? '(no answer)'}</span>
          </div>
        ))}
      </div>
    );
  }

  const questions = item.questions;
  const current = questions[step];
  if (!current) return null;
  const last = step === questions.length - 1;
  const complete = answerFor(draft, current) !== '';
  const chosen = draft.chosen[current.question] ?? [];

  // A multi-select keeps Other beside the chosen options; a single choice replaces them.
  const toggle = (label: string) =>
    current.multiSelect
      ? chosen.includes(label)
        ? chosen.filter((l) => l !== label)
        : [...chosen, label]
      : [label];
  const choose = (label: string) =>
    setDraft({ ...draft, chosen: { ...draft.chosen, [current.question]: toggle(label) } });
  const withOther = () =>
    current.multiSelect ? (chosen.includes(OTHER) ? chosen : [...chosen, OTHER]) : [OTHER];
  const chooseOther = () =>
    setDraft({ ...draft, chosen: { ...draft.chosen, [current.question]: withOther() } });
  const typeOther = (text: string) =>
    setDraft({
      chosen: { ...draft.chosen, [current.question]: withOther() },
      other: { ...draft.other, [current.question]: text },
    });

  const submit = () => {
    if (!complete) return;
    if (!last) {
      setStep(step + 1);
      return;
    }
    onAnswer?.(Object.fromEntries(questions.map((q) => [q.question, answerFor(draft, q)])));
  };

  // Digit keys pick an option, except while the Other text takes the typing.
  const onKeyDown = (e: React.KeyboardEvent) => {
    const target = e.target as HTMLElement;
    if (target.tagName === 'TEXTAREA' || (target as HTMLInputElement).type === 'text') return;
    const index = Number(e.key);
    if (!Number.isInteger(index) || index < 1 || index > 9) return;
    const option = current.options[index - 1];
    if (!option) return;
    e.preventDefault();
    choose(option.label);
  };

  const otherChosen = chosen.includes(OTHER);
  const inputType = current.multiSelect ? 'checkbox' : 'radio';
  const labelId = `${groupId}-q`;
  const otherId = `${groupId}-other`;

  return (
    <div className={styles.prompt} data-testid="question-prompt" onKeyDown={onKeyDown}>
      {questions.length > 1 && (
        <div className={styles.steps}>
          {questions.map((q, i) => {
            const done = i < step;
            return (
              <button
                key={q.question}
                type="button"
                className={styles.stepChip}
                data-current={i === step || undefined}
                data-answered={done || undefined}
                aria-current={i === step ? 'step' : undefined}
                disabled={i > step}
                onClick={() => setStep(i)}
              >
                <span className={styles.stepHeader}>{q.header || `Question ${i + 1}`}</span>
                {done && (
                  <span className={styles.stepAnswer} data-testid="step-answer">
                    {answerFor(draft, q)}
                  </span>
                )}
              </button>
            );
          })}
          <span className={styles.stepCount}>
            {step + 1} of {questions.length}
          </span>
        </div>
      )}
      <div className={styles.qhead}>
        {current.header || 'Question'}
        {current.multiSelect ? ' · choose any' : ''}
      </div>
      <div id={labelId} className={styles.questionText}>
        {current.question}
      </div>
      <div
        role={current.multiSelect ? 'group' : 'radiogroup'}
        aria-labelledby={labelId}
        className={styles.optionList}
      >
        {current.options.map((o, i) => (
          <label
            key={o.label}
            className={styles.option}
            data-chosen={chosen.includes(o.label) || undefined}
          >
            <input
              type={inputType}
              name={current.multiSelect ? undefined : groupId}
              checked={chosen.includes(o.label)}
              disabled={!onAnswer}
              onChange={() => choose(o.label)}
            />
            <span className={styles.optionKey}>{i + 1}</span>
            <span className={styles.optionBody}>
              <span className={styles.optionLabel}>{o.label}</span>
              {o.description && <span className={styles.optionDesc}>{o.description}</span>}
            </span>
          </label>
        ))}
        <div className={styles.option} data-chosen={otherChosen || undefined}>
          <input
            id={otherId}
            type={inputType}
            name={current.multiSelect ? undefined : groupId}
            checked={otherChosen}
            disabled={!onAnswer}
            onChange={chooseOther}
          />
          <span className={styles.optionKey} />
          <span className={styles.optionBody}>
            <label htmlFor={otherId} className={styles.optionLabel}>
              Other
            </label>
            <input
              type="text"
              className={styles.otherInput}
              aria-label="Other"
              placeholder="Type your own answer"
              value={draft.other[current.question] ?? ''}
              disabled={!onAnswer}
              onFocus={chooseOther}
              onChange={(e) => typeOther(e.target.value)}
            />
          </span>
        </div>
      </div>
      <div className={styles.options}>
        {step > 0 && (
          <button type="button" className={styles.quiet} onClick={() => setStep(step - 1)}>
            Back
          </button>
        )}
        <button
          type="button"
          className={styles.primary}
          disabled={!onAnswer || !complete}
          onClick={submit}
        >
          {last ? 'Answer' : 'Next'}
        </button>
      </div>
    </div>
  );
}
