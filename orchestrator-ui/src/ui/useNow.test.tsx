import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';

import { useNow } from './useNow';

/** A component that shows nothing but the clock it was given. */
function Clock({ label }: { label: string }) {
  const now = useNow();
  return <span data-testid={label}>{now}</span>;
}

describe('useNow', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('advances every age on screen from one timer', () => {
    vi.setSystemTime(1_000_000);
    const { getByTestId } = render(
      <>
        <Clock label="a" />
        <Clock label="b" />
      </>,
    );
    expect(getByTestId('a').textContent).toBe('1000000');
    // One interval, not one per component: a single 30s advance moves both.
    expect(vi.getTimerCount()).toBe(1);

    // Advancing the timers advances the fake clock with them.
    act(() => void vi.advanceTimersByTime(30_000));
    expect(getByTestId('a').textContent).toBe('1030000');
    expect(getByTestId('b').textContent).toBe('1030000');
  });

  it('stops the timer when the last age leaves the screen', () => {
    const first = render(<Clock label="a" />);
    const second = render(<Clock label="b" />);
    expect(vi.getTimerCount()).toBe(1);
    // One subscriber left, so the timer must keep running.
    first.unmount();
    expect(vi.getTimerCount()).toBe(1);
    // None left: a timer that kept waking would run for a closed page.
    second.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('reads the clock as an age arrives, not at module load', () => {
    // Without the re-seed the stored time is as old as the last tick, so an
    // age opens wrong and corrects itself 30s later.
    vi.setSystemTime(5_000_000);
    const { getByTestId } = render(<Clock label="a" />);
    expect(getByTestId('a').textContent).toBe('5000000');
  });

  it('reads the clock for a late age too, not only the first', () => {
    // A permanently mounted reader holds the timer open, so a card mounting
    // later is never the first subscriber. Re-seeding only for the first one
    // would hand that card a time up to a whole tick old, and an age that
    // rounds down reads a whole unit short: "3h" for four hours.
    vi.setSystemTime(1_000_000);
    render(<Clock label="held" />);
    // The precondition the case rests on: the timer is already open, so the
    // late reader is not the first subscriber. Without this the test would
    // quietly decay into the one above if `held` ever stopped holding it.
    expect(vi.getTimerCount()).toBe(1);
    vi.setSystemTime(1_020_000);
    const late = render(<Clock label="late" />);
    expect(late.getByTestId('late').textContent).toBe('1020000');
  });
});
