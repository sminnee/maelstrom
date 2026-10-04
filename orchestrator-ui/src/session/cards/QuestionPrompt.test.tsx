import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Question, QuestionItem } from '../../protocol/transcript';
import { makeQuestionItem } from '../../fake/fixtures';
import { QuestionPrompt } from './QuestionPrompt';

const EXPORT: Question = {
  question: 'Should the export stream rows or build the file first?',
  header: 'Export',
  multiSelect: false,
  options: [
    { label: 'Stream', description: 'Constant memory; no total row count up front.' },
    { label: 'Build first', description: 'Simpler; needs the whole file in memory.' },
  ],
};

const COLUMNS: Question = {
  question: 'Which columns should the export include?',
  header: 'Columns',
  multiSelect: true,
  options: [
    { label: 'Id', description: 'The order id.' },
    { label: 'Customer', description: 'The customer name.' },
    { label: 'Total', description: 'The order total.' },
    { label: 'Status', description: 'Where the order is in fulfilment.' },
  ],
};

const item = (questions: Question[], answers?: Record<string, string>): QuestionItem =>
  makeQuestionItem({ questions, answers });

describe('QuestionPrompt', () => {
  it('shows each option with its description and sends the chosen one on Answer', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn();
    render(<QuestionPrompt item={item([EXPORT])} onAnswer={onAnswer} />);
    expect(screen.getByText('Constant memory; no total row count up front.')).toBeInTheDocument();
    const answer = screen.getByRole('button', { name: 'Answer' });
    expect(answer).toBeDisabled();
    await user.click(screen.getByRole('radio', { name: /Build first/ }));
    expect(onAnswer).not.toHaveBeenCalled();
    await user.click(answer);
    expect(onAnswer).toHaveBeenCalledWith({ [EXPORT.question]: 'Build first' });
  });

  it('a multi-select toggles its options and joins them with a comma', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn();
    render(<QuestionPrompt item={item([COLUMNS])} onAnswer={onAnswer} />);
    await user.click(screen.getByRole('checkbox', { name: /Id/ }));
    await user.click(screen.getByRole('checkbox', { name: /Customer/ }));
    await user.click(screen.getByRole('checkbox', { name: /Total/ }));
    await user.click(screen.getByRole('checkbox', { name: /Customer/ }));
    await user.click(screen.getByRole('button', { name: 'Answer' }));
    expect(onAnswer).toHaveBeenCalledWith({ [COLUMNS.question]: 'Id, Total' });
  });

  it('a multi-select keeps its options when Other is added', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn();
    render(<QuestionPrompt item={item([COLUMNS])} onAnswer={onAnswer} />);
    await user.click(screen.getByRole('checkbox', { name: /Id/ }));
    await user.type(screen.getByRole('textbox', { name: 'Other' }), 'Region');
    await user.click(screen.getByRole('button', { name: 'Answer' }));
    expect(onAnswer).toHaveBeenCalledWith({ [COLUMNS.question]: 'Id, Region' });
  });

  it('Other sends the typed text', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn();
    render(<QuestionPrompt item={item([EXPORT])} onAnswer={onAnswer} />);
    await user.type(screen.getByRole('textbox', { name: 'Other' }), 'Stream, but cap at 10k rows');
    await user.click(screen.getByRole('button', { name: 'Answer' }));
    expect(onAnswer).toHaveBeenCalledWith({ [EXPORT.question]: 'Stream, but cap at 10k rows' });
  });

  it('two questions step through and back, and one Answer sends both keyed by question text', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn();
    render(<QuestionPrompt item={item([COLUMNS, EXPORT])} onAnswer={onAnswer} />);
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Answer' })).toBeNull();
    await user.click(screen.getByRole('checkbox', { name: /Status/ }));
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText('2 of 2')).toBeInTheDocument();
    expect(screen.getByText('Columns')).toBeInTheDocument();
    expect(
      screen.getByText('Status', { selector: '[data-testid="step-answer"]' }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /Status/ })).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Next' }));
    await user.click(screen.getByRole('radio', { name: /Stream/ }));
    await user.click(screen.getByRole('button', { name: 'Answer' }));
    expect(onAnswer).toHaveBeenCalledWith({
      [COLUMNS.question]: 'Status',
      [EXPORT.question]: 'Stream',
    });
  });

  it('a digit key picks that option while the prompt has focus', async () => {
    const user = userEvent.setup();
    const onAnswer = vi.fn();
    render(<QuestionPrompt item={item([EXPORT])} onAnswer={onAnswer} />);
    screen.getByRole('radio', { name: /Stream/ }).focus();
    await user.keyboard('2');
    expect(screen.getByRole('radio', { name: /Build first/ })).toBeChecked();
    await user.click(screen.getByRole('button', { name: 'Answer' }));
    expect(onAnswer).toHaveBeenCalledWith({ [EXPORT.question]: 'Build first' });
  });

  it('an answered item shows each header with its answer and no controls', () => {
    render(
      <QuestionPrompt
        item={item([COLUMNS, EXPORT], {
          [COLUMNS.question]: 'Id, Total',
          [EXPORT.question]: 'Stream',
        })}
      />,
    );
    expect(screen.getByText('Id, Total')).toBeInTheDocument();
    expect(screen.getByText('Stream')).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByRole('radio')).toBeNull();
  });

  it('Decline calls its handler on any step, and the menu holds Decline & stop', async () => {
    const user = userEvent.setup();
    const onDecline = vi.fn(() => Promise.resolve());
    const onDeclineAndStop = vi.fn(() => Promise.resolve());
    render(
      <QuestionPrompt
        item={item([COLUMNS, EXPORT])}
        onAnswer={vi.fn()}
        onDecline={onDecline}
        onDeclineAndStop={onDeclineAndStop}
      />,
    );
    // Nothing is chosen yet: a decline needs no answer.
    await user.click(screen.getByRole('button', { name: 'Decline' }));
    expect(onDecline).toHaveBeenCalledOnce();
    await user.click(screen.getByRole('checkbox', { name: /Id/ }));
    await user.click(screen.getByRole('button', { name: 'Next' }));
    await user.click(screen.getByRole('button', { name: 'Decline' }));
    expect(onDecline).toHaveBeenCalledTimes(2);
    expect(onDeclineAndStop).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'More ways to decline' }));
    await user.click(screen.getByRole('menuitem', { name: 'Decline & stop' }));
    expect(onDeclineAndStop).toHaveBeenCalledOnce();
    expect(onDecline).toHaveBeenCalledTimes(2);
  });

  it('offers no decline without a handler', () => {
    render(<QuestionPrompt item={item([EXPORT])} onAnswer={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Decline' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'More ways to decline' })).toBeNull();
  });

  it('a declined item shows the question as declined, with no controls', () => {
    render(
      <QuestionPrompt
        item={{ ...item([EXPORT]), declined: true, reason: 'The user declined to answer.' }}
        onAnswer={vi.fn()}
        onDecline={vi.fn()}
      />,
    );
    const declined = screen.getByTestId('question-declined');
    expect(declined).toHaveTextContent(EXPORT.question);
    // The button's own reason says nothing the word does not.
    expect(declined).toHaveTextContent(/Declined$/);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByRole('radio')).toBeNull();
  });

  it('a question the daemon denied says why, because the operator did not decline it', () => {
    render(
      <QuestionPrompt
        item={{ ...item([EXPORT]), declined: true, reason: 'Interrupted by user' }}
      />,
    );
    expect(screen.getByTestId('question-declined')).toHaveTextContent(
      'Declined · Interrupted by user',
    );
  });

  it('offers nothing for a question nothing answered, even with a handler', () => {
    render(<QuestionPrompt item={{ ...item([EXPORT]), stale: true }} onAnswer={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Answer' })).toBeNull();
    expect(screen.queryAllByRole('radio')).toHaveLength(0);
    expect(screen.getByText('no longer pending')).toBeInTheDocument();
  });
});
