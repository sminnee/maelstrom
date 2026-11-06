import { useQuery } from '@tanstack/react-query';
import { useApi } from './ApiProvider';
import { keys } from './keys';

/** The release that runs. */
export interface SystemVersion {
  tag: string;
  commit: string;
  dirty: boolean;
}

/** What the server knows of its install, for the **Welcome dialog**. */
export interface SystemBody {
  /** `null` for a run from a checkout, with no release installed. */
  version: SystemVersion | null;
  /** The user's home directory, so a path can read `~/…`. */
  home: string;
  configDir: string;
  projectsDir: string;
  skillsDir: string;
  /** The skills maelstrom linked into `skillsDir`, by name. */
  skills: string[];
}

/** The install's paths, skills and release. Not part of the world: no notice names it. */
export function useSystem() {
  const api = useApi();
  return useQuery({
    queryKey: keys.system(),
    queryFn: ({ signal }) => api.get<SystemBody>('/api/system', { signal }),
  });
}
