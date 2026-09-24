import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
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
});
