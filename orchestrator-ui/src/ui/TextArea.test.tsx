import { useState } from 'react';
import { beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TextArea } from './TextArea';

/** jsdom computes no layout: each line of text is 20px of scroll height. */
const LINE = 20;

/** Characters per line at the field's width, which sets how the text wraps. */
let perLine = Infinity;

/** The field as a caller holds it, with a button that appends from outside. */
function Harness({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <label>
        <span>Notes</span>
        {/* jsdom gives an unstyled textarea a 16px border, which a browser does not. */}
        <TextArea
          grow
          value={value}
          style={{ borderWidth: 0 }}
          onChange={(e) => setValue(e.target.value)}
        />
      </label>
      <button type="button" onClick={() => setValue((v) => `${v}\n\n![shot](x.png)`)}>
        Attach
      </button>
    </>
  );
}

describe('TextArea grow', () => {
  beforeEach(() => {
    vi.spyOn(HTMLTextAreaElement.prototype, 'scrollHeight', 'get').mockImplementation(function (
      this: HTMLTextAreaElement,
    ) {
      const lines = this.value
        .split('\n')
        .reduce((n, line) => n + Math.max(1, Math.ceil(line.length / perLine)), 0);
      // A real field scrolls no less than its own height.
      return Math.max(lines * LINE, parseFloat(this.style.height) || 0);
    });
    perLine = Infinity;
  });

  it('fits its initial text', () => {
    render(<Harness initial={'one\ntwo\nthree'} />);
    expect(screen.getByRole('textbox', { name: 'Notes' })).toHaveStyle({ height: '60px' });
  });

  it('grows as text is typed and shrinks as it is removed', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const field = screen.getByRole('textbox', { name: 'Notes' });
    expect(field).toHaveStyle({ height: '20px' });

    await user.type(field, 'a{Enter}b{Enter}c');
    expect(field).toHaveStyle({ height: '60px' });

    await user.type(field, '{Backspace}{Backspace}');
    expect(field).toHaveStyle({ height: '40px' });
  });

  it('adds its border to the height', () => {
    render(
      <TextArea
        grow
        aria-label="Notes"
        value={'one\ntwo'}
        readOnly
        style={{ border: '1px solid' }}
      />,
    );
    expect(screen.getByRole('textbox', { name: 'Notes' })).toHaveStyle({ height: '42px' });
  });

  it('fits a value changed from outside', async () => {
    const user = userEvent.setup();
    render(<Harness initial="one" />);
    await user.click(screen.getByRole('button', { name: 'Attach' }));
    expect(screen.getByRole('textbox', { name: 'Notes' })).toHaveStyle({ height: '60px' });
  });

  it('refits when its width changes, and not when only its height does', () => {
    let resized: () => void = () => {};
    // The setup stub is writable but not configurable, so `stubGlobal` cannot
    // replace it.
    const stub = globalThis.ResizeObserver;
    onTestFinished(() => {
      globalThis.ResizeObserver = stub;
    });
    globalThis.ResizeObserver = class {
      constructor(callback: () => void) {
        resized = callback;
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
    let width = 400;
    vi.spyOn(HTMLTextAreaElement.prototype, 'clientWidth', 'get').mockImplementation(() => width);
    render(<Harness initial={'x'.repeat(30)} />);
    const field = screen.getByRole('textbox', { name: 'Notes' });
    expect(field).toHaveStyle({ height: '20px' });

    // The text now wraps to three lines, but the width is the same.
    perLine = 10;
    act(() => resized());
    expect(field).toHaveStyle({ height: '20px' });

    width = 100;
    act(() => resized());
    expect(field).toHaveStyle({ height: '60px' });
  });
});
