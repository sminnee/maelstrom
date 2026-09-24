import { Component, type ReactNode } from 'react';

/**
 * Keeps one failed card from taking the app down.
 *
 * A transcript draws whatever an agent wrote, and a card that throws while
 * rendering unmounts the whole React tree — the operator loses the board, not
 * just the row. This catches the throw and leaves a line in its place, so the
 * rest of the transcript still reads.
 */
export class CardBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div data-testid="card-error" role="status">
          This event could not be drawn.
        </div>
      );
    }
    return this.props.children;
  }
}
