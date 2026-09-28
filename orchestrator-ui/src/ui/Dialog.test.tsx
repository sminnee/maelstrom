import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Dialog } from './Dialog';

/** The modal shell's one way out: `cancel`, which Escape and the backdrop both raise. */
describe('the dialog shell', () => {
  function open(onClose: () => void) {
    render(
      <Dialog label="Test" onClose={onClose}>
        <p>Content</p>
      </Dialog>,
    );
    return screen.getByRole('dialog', { name: 'Test' });
  }

  it('holds open on a press on a child drawn outside the box, as a combo box offer is', () => {
    const onClose = vi.fn();
    open(onClose);
    // Pins the old bug: a pointer handler that read this press as a backdrop
    // press. jsdom reports every rect as 0×0, so any point is outside the box.
    const child = screen.getByText('Content');
    fireEvent.mouseDown(child, { clientX: 20, clientY: 20 });
    fireEvent.click(child, { clientX: 20, clientY: 20 });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('leaves the backdrop to the browser, which closes by light dismiss', () => {
    // jsdom has no light dismiss, so the attribute is what can be observed.
    expect(open(vi.fn())).toHaveAttribute('closedby', 'any');
  });

  it('closes on cancel, which Escape and a backdrop click both raise', () => {
    const onClose = vi.fn();
    const box = open(onClose);
    fireEvent(box, new Event('cancel', { bubbles: false, cancelable: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
