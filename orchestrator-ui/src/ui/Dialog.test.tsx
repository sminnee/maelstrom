import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Dialog, DialogFooter } from './Dialog';

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

  // jsdom has no `closedBy`, so the fallback runs here.
  describe('without light dismiss', () => {
    function boxed(onClose: () => void) {
      const box = open(onClose);
      vi.spyOn(box, 'getBoundingClientRect').mockReturnValue(
        DOMRect.fromRect({ x: 100, y: 0, width: 200, height: 400 }),
      );
      return box;
    }

    /** A press and a release, as the browser targets them, then the click. */
    function click(
      box: HTMLElement,
      pressOn: HTMLElement,
      at: { clientX: number; clientY: number },
    ) {
      fireEvent.pointerDown(pressOn, at);
      fireEvent.click(box, at);
    }

    it('closes on a click on the backdrop', () => {
      const onClose = vi.fn();
      // A backdrop click targets the dialog itself, at a point outside its box.
      const box = boxed(onClose);
      click(box, box, { clientX: 20, clientY: 20 });
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('holds open on a click in the box’s own padding', () => {
      const onClose = vi.fn();
      const box = boxed(onClose);
      click(box, box, { clientX: 150, clientY: 20 });
      expect(onClose).not.toHaveBeenCalled();
    });

    it('holds open on a drag from inside the box to the backdrop', () => {
      const onClose = vi.fn();
      const box = boxed(onClose);
      fireEvent.pointerDown(screen.getByText('Content'), { clientX: 150, clientY: 20 });
      fireEvent.click(box, { clientX: 20, clientY: 20 });
      expect(onClose).not.toHaveBeenCalled();
    });
  });

  it('leaves a backdrop click to the browser where it has light dismiss', () => {
    Object.defineProperty(HTMLDialogElement.prototype, 'closedBy', {
      value: 'any',
      configurable: true,
    });
    try {
      const onClose = vi.fn();
      const box = open(onClose);
      fireEvent.pointerDown(box, { clientX: 20, clientY: 20 });
      fireEvent.click(box, { clientX: 20, clientY: 20 });
      expect(onClose).not.toHaveBeenCalled();
    } finally {
      delete (HTMLDialogElement.prototype as { closedBy?: string }).closedBy;
    }
  });

  it('closes on cancel, which Escape and a backdrop click both raise', () => {
    const onClose = vi.fn();
    const box = open(onClose);
    fireEvent(box, new Event('cancel', { bubbles: false, cancelable: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('the dialog footer', () => {
  it('draws the aside group before the main group, apart from it', () => {
    render(
      <DialogFooter aside={<button type="button">Cancel</button>}>
        <button type="button">Start</button>
      </DialogFooter>,
    );
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    const start = screen.getByRole('button', { name: 'Start' });
    expect(cancel.compareDocumentPosition(start) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(cancel.parentElement).not.toBe(start.parentElement);
  });
});
