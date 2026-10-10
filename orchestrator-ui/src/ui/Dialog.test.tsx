import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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

describe('the focused field', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  /**
   * A box that records its own scroll, with a field `top` px down its content
   * and `height` tall. The field's rect moves as the box scrolls.
   */
  function form(field: { top: number; height: number }) {
    const viewport = Object.assign(new EventTarget(), { height: 900, offsetTop: 0 });
    vi.stubGlobal('visualViewport', viewport);
    const view = render(
      <Dialog label="Test" onClose={() => {}}>
        <label>
          Base <input />
        </label>
        <label>
          Title <input />
        </label>
      </Dialog>,
    );
    const box = screen.getByRole('dialog', { name: 'Test' });
    let height = 900;
    let scrollTop = 0;
    Object.defineProperty(box, 'scrollTop', {
      configurable: true,
      get: () => scrollTop,
      set: (v: number) => {
        scrollTop = v;
      },
    });
    box.getBoundingClientRect = () => DOMRect.fromRect({ x: 0, y: 0, width: 400, height });
    const base = screen.getByRole('textbox', { name: 'Base' });
    base.getBoundingClientRect = () =>
      DOMRect.fromRect({ x: 0, y: field.top - scrollTop, width: 400, height: field.height });
    /** The keyboard opens: the visible area, and so the box, is now `to` tall. */
    const keyboard = (to: number) => {
      height = to;
      viewport.height = to;
      viewport.dispatchEvent(new Event('resize'));
      vi.advanceTimersToNextFrame();
    };
    const drawn = () => base.getBoundingClientRect();
    return { box, base, keyboard, drawn, scrolled: () => scrollTop, unmount: view.unmount };
  }

  it('scrolls the box to a field the keyboard covers, to 16px above its foot', () => {
    const { base, keyboard, drawn } = form({ top: 600, height: 40 });
    fireEvent.focus(base);
    vi.advanceTimersToNextFrame();
    // In view before the keyboard: nothing moves.
    expect(drawn().bottom).toBe(640);

    keyboard(500);
    expect(drawn().bottom).toBe(484);
    // A second resize finds it in view, and leaves it there.
    keyboard(500);
    expect(drawn().bottom).toBe(484);
  });

  it('scrolls back to a field above the top of the box, to 16px below it', () => {
    const { box, base, drawn } = form({ top: 50, height: 40 });
    box.scrollTop = 100;
    fireEvent.focus(base);
    vi.advanceTimersToNextFrame();
    expect(drawn().top).toBe(16);
  });

  it('leaves a field taller than the box where it is, so the caret stays in view', () => {
    const { base, keyboard, scrolled } = form({ top: 100, height: 900 });
    fireEvent.focus(base);
    keyboard(500);
    expect(scrolled()).toBe(0);
  });

  it('leaves the box alone once the field has lost the focus', () => {
    const { base, keyboard, scrolled } = form({ top: 600, height: 40 });
    fireEvent.focus(base);
    fireEvent.blur(base);
    keyboard(500);
    expect(scrolled()).toBe(0);
  });

  it('stops watching when the dialog goes while the field has the focus', () => {
    // A removed field fires no blur in every browser.
    const { base, keyboard, scrolled, unmount } = form({ top: 600, height: 40 });
    fireEvent.focus(base);
    unmount();
    keyboard(500);
    expect(scrolled()).toBe(0);
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
