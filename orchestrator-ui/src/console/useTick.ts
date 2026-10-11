import { useEffect, useState } from 'react';

/** The time, read once a second while `live`: a running step's duration counts up. */
export function useTick(live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);
  return now;
}
