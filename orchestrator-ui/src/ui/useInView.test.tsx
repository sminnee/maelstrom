import { useRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { useInView } from './useInView';

/** The observer's callback, as the browser would call it. */
let report: ((entries: { isIntersecting: boolean }[]) => void) | null = null;

class ObserverStub {
  constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
    report = callback;
  }
  observe() {}
  disconnect() {}
}

function Probe() {
  const ref = useRef<HTMLDivElement>(null);
  return <div ref={ref}>{useInView(ref) ? 'seen' : 'unseen'}</div>;
}

describe('useInView', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    report = null;
  });

  it('is true once the element comes near the screen, and stays true', () => {
    vi.stubGlobal('IntersectionObserver', ObserverStub);
    render(<Probe />);
    expect(screen.getByText('unseen')).toBeInTheDocument();

    act(() => report!([{ isIntersecting: true }]));
    expect(screen.getByText('seen')).toBeInTheDocument();

    act(() => report?.([{ isIntersecting: false }]));
    expect(screen.getByText('seen')).toBeInTheDocument();
  });

  it('is true from the start where the browser has no observer', () => {
    vi.stubGlobal('IntersectionObserver', undefined);
    render(<Probe />);
    expect(screen.getByText('seen')).toBeInTheDocument();
  });
});
