import type { RouteObject } from 'react-router';
import { Located } from './Located';

/**
 * The app's one route. `location.ts` matches the path, so the hrefs the app builds and the
 * paths it reads come from one table.
 */
export const routes: RouteObject[] = [{ path: '*', element: <Located /> }];
