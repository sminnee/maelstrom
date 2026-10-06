import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SplitChip } from './SplitChip';

describe('SplitChip', () => {
  it('shows both halves at rest: neither is held back for a pointer', () => {
    render(
      <SplitChip label="5h" title="5-hour limit: 7% used">
        {'7%'}
      </SplitChip>,
    );
    const chip = screen.getByTitle('5-hour limit: 7% used');
    expect(chip).toHaveTextContent('5h');
    expect(chip).toHaveTextContent('7%');
  });

  it('names itself by its title, so the reading has one spoken form', () => {
    render(
      <SplitChip label="week" title="7-day limit: 24% used">
        {'24%'}
      </SplitChip>,
    );
    expect(screen.getByLabelText('7-day limit: 24% used')).toBeInTheDocument();
  });

  it('carries the tone the stylesheet colours the value with', () => {
    render(
      <SplitChip label="5h" title="t" tone="bad">
        {'96%'}
      </SplitChip>,
    );
    // `css: false` in `vite.config.ts`: the colour is the stylesheet's, and
    // what the suite can hold is which one the chip asks for.
    expect(screen.getByTitle('t')).toHaveAttribute('data-tone', 'bad');
  });

  it('drops an alarming tone when the reading is stale', () => {
    // The guarantee is the component's, not the caller's: `stale` with a loud
    // tone must not shout about a number the chip cannot vouch for.
    render(
      <SplitChip label="5h" title="t" tone="bad" stale>
        {'96%'}
      </SplitChip>,
    );
    expect(screen.getByTitle('t')).toHaveAttribute('data-tone', 'quiet');
  });

  it('defaults to the neutral tone: a reading is news only when it is high', () => {
    render(
      <SplitChip label="5h" title="t">
        {'7%'}
      </SplitChip>,
    );
    expect(screen.getByTitle('t')).toHaveAttribute('data-tone', 'neutral');
  });

  it('says when it is stale, so a number it cannot stand behind reads as one', () => {
    render(
      <SplitChip label="5h" title="t" stale>
        {'7%'}
      </SplitChip>,
    );
    expect(screen.getByTitle('t')).toHaveAttribute('data-stale', 'true');
  });

  it('is not stale by default', () => {
    render(
      <SplitChip label="5h" title="t">
        {'7%'}
      </SplitChip>,
    );
    expect(screen.getByTitle('t')).not.toHaveAttribute('data-stale');
  });

  it('is a button named by its title when it takes a click', async () => {
    const onClick = vi.fn();
    render(
      <SplitChip label="agents" title="4 of 6 agents working" onClick={onClick}>
        {'4/6'}
      </SplitChip>,
    );
    await userEvent.click(screen.getByRole('button', { name: '4 of 6 agents working' }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('is a reading, not a control, without a click', () => {
    render(
      <SplitChip label="agents" title="t">
        {'4/6'}
      </SplitChip>,
    );
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByRole('img', { name: 't' })).toBeInTheDocument();
  });
});
