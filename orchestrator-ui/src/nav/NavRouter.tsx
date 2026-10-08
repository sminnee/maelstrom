import { RouterProvider } from 'react-router';
import { RouterContext, type AppRouter } from './router';

/** The router, and the context that hands it to a move: see `RouterContext`. */
export function NavRouter({ router }: { router: AppRouter }) {
  return (
    <RouterContext.Provider value={router}>
      <RouterProvider router={router} />
    </RouterContext.Provider>
  );
}
