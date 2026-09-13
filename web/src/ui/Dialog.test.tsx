import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { Dialog } from './Dialog';

/** The modal shell's one way out that a pointer takes: a press on the backdrop. */
describe('the dialog shell', () => {
  /**
   * Give the box a rect, since jsdom computes no layout and reports every
   * element as 0×0.
   */
  function layOutBox(box: HTMLElement) {
    vi.spyOn(box, 'getBoundingClientRect').mockReturnValue({
      x: 100,
      y: 100,
      left: 100,
      top: 100,
      right: 300,
      bottom: 300,
      width: 200,
      height: 200,
      toJSON: () => ({}),
    } as DOMRect);
  }

  function open(onClose: () => void) {
    render(
      <Dialog label="Test" onClose={onClose}>
        <p>Content</p>
      </Dialog>,
    );
    const box = screen.getByRole('dialog', { name: 'Test' });
    layOutBox(box);
    return box;
  }

  it('closes on a press outside the box, which is what the backdrop is', () => {
    const onClose = vi.fn();
    const box = open(onClose);
    // Well clear of the box: the dimmed area around it.
    fireEvent.mouseDown(box, { clientX: 20, clientY: 20 });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('holds open on a press on the box, so a stray click never discards the form', () => {
    const onClose = vi.fn();
    const box = open(onClose);
    // Inside the box, but on its own padding or on a gap between fieldsets, so
    // the event's target is the dialog element rather than a child. A tall form
    // is mostly gaps, and a press on one must not throw away what was typed.
    fireEvent.mouseDown(box, { clientX: 150, clientY: 150 });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('holds open on a press on the box edge, where a rounded corner reads as outside', () => {
    const onClose = vi.fn();
    const box = open(onClose);
    // Exactly on the boundary. Inclusive, so the 1px border and the corner
    // radius belong to the box.
    fireEvent.mouseDown(box, { clientX: 100, clientY: 300 });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes on Escape, which reaches the dialog as cancel', () => {
    const onClose = vi.fn();
    const box = open(onClose);
    fireEvent(box, new Event('cancel', { bubbles: false, cancelable: true }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
