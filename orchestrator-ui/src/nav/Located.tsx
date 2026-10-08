import { Navigate, useLocation } from 'react-router';
import { AppShell } from '../shell/AppShell';
import { parseLocation } from './location';

/** The app at a location it has a screen for. Any other path, `/` among them, moves to the desk. */
export function Located() {
  const { pathname, search } = useLocation();
  if (!parseLocation(pathname, search))
    return <Navigate replace to={{ pathname: '/desk', search }} />;
  return <AppShell />;
}
