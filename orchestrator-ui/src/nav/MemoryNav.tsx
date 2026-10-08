import { createContext, useContext, useState, type ReactNode } from 'react';
import { createMemoryRouter } from 'react-router';
import { NavRouter } from './NavRouter';

const Children = createContext<ReactNode>(null);

/** The route's element: whatever `MemoryNav` was given on this render. */
function Outlet() {
  return useContext(Children);
}

/** `children` at `url`, on a memory router: for a story, whose page URL Ladle owns. */
export function MemoryNav({ url = '/desk', children }: { url?: string; children: ReactNode }) {
  const [router] = useState(() =>
    createMemoryRouter([{ path: '*', element: <Outlet /> }], { initialEntries: [url] }),
  );
  return (
    <Children.Provider value={children}>
      <NavRouter router={router} />
    </Children.Provider>
  );
}
