import { useState, type ReactNode } from 'react';
import { createMemoryRouter } from 'react-router';
import { NavRouter } from './NavRouter';

/**
 * `children` at `url`, on a memory router: for a story, whose page URL Ladle owns. The
 * children are read once, as a route's element is.
 */
export function MemoryNav({ url, children }: { url: string; children: ReactNode }) {
  const [router] = useState(() =>
    createMemoryRouter([{ path: '*', element: children }], { initialEntries: [url] }),
  );
  return <NavRouter router={router} />;
}
