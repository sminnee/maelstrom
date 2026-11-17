import { describe, expect, it } from 'vitest';
import { linksByFragment } from './documents';

describe('linksByFragment', () => {
  it('lists the whole document first, then each fragment in heading order', () => {
    const links = [
      { taskId: 'p/T-3', fragment: 'install' },
      { taskId: 'p/T-1', fragment: 'set-up' },
      { taskId: 'p/T-2', fragment: '' },
      { taskId: 'p/T-4', fragment: 'set-up' },
    ];
    expect(linksByFragment(links, ['set-up', 'install'])).toEqual([
      { fragment: '', taskIds: ['p/T-2'] },
      { fragment: 'set-up', taskIds: ['p/T-1', 'p/T-4'] },
      { fragment: 'install', taskIds: ['p/T-3'] },
    ]);
  });

  it('keeps a link to a fragment the latest version dropped, after the rest', () => {
    const links = [
      { taskId: 'p/T-1', fragment: 'gone' },
      { taskId: 'p/T-2', fragment: 'set-up' },
    ];
    expect(linksByFragment(links, ['set-up'])).toEqual([
      { fragment: 'set-up', taskIds: ['p/T-2'] },
      { fragment: 'gone', taskIds: ['p/T-1'] },
    ]);
  });

  it('is empty with no link', () => {
    expect(linksByFragment([], ['set-up'])).toEqual([]);
  });
});
