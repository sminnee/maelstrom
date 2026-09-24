import { describe, expect, it } from 'vitest';
import { rangeToTextOffsets } from './domText';

describe('rangeToTextOffsets', () => {
  it('maps a range across elements to offsets into the container text', () => {
    const container = document.createElement('div');
    container.innerHTML = '<h1>Plan</h1><p>Add the <em>export</em>. Cap it.</p>';
    document.body.appendChild(container);
    const em = container.querySelector('em')!.firstChild!;
    const tail = container.querySelector('p')!.lastChild!;
    const range = document.createRange();
    range.setStart(em, 0);
    range.setEnd(tail, 5); // '. Cap'
    const text = container.textContent ?? '';
    const offsets = rangeToTextOffsets(container, range);
    expect(text.slice(offsets.start, offsets.end)).toBe('export. Cap');
    container.remove();
  });
});
