import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useApi } from './ApiProvider';
import { keys } from './keys';

/** The release that runs. */
export interface SystemVersion {
  tag: string;
  commit: string;
  dirty: boolean;
}

/**
 * The record of the last **Update job**. `interrupted` is a `running` job
 * whose process is gone: a reboot or a kill stopped it.
 */
export interface UpdateRecord {
  state: 'running' | 'succeeded' | 'failed' | 'interrupted';
  pid: number;
  startedAt: string;
  finishedAt: string | null;
  fromTag: string | null;
  toTag: string | null;
  exitCode: number | null;
  /** The job's log on the host. */
  log: string;
}

/** What the server knows of its install, for the **Welcome dialog**. */
export interface SystemBody {
  /** `null` for a run from a checkout, with no release installed. */
  version: SystemVersion | null;
  /** The user's home directory, so a path can read `~/…`. */
  home: string;
  configDir: string;
  projectsDir: string;
  /** The core plugin's `skills/` dir: the skills each launched agent loads. */
  skillsDir: string;
  /** The skills in `skillsDir`, by name. */
  skills: string[];
  /** Whether Claude Code has the optional `minnstack` plugin installed. */
  practices: boolean;
  /** The last **Update job**, or `null` when none ran. */
  update: UpdateRecord | null;
  /** Where `mael self update` installs from: `stable` or `main`. */
  updateChannel: string;
  /** False on a development build. See `can_update` in `system_info.py`. */
  canUpdate: boolean;
}

/** How often the dialog reads the install while an **Update job** runs. */
export const UPDATE_POLL_MS = 2000;

/**
 * The install's paths, skills and release. Not part of the world: no notice names it.
 *
 * While an **Update job** runs, or one was started and its record has not yet
 * replaced the one that was there before, it reads again every
 * {@link UPDATE_POLL_MS}. The orchestrator restarts in the job, so some reads
 * fail; the last good body stays.
 */
export function useSystem({ startedJob }: { startedJob?: StartedJob } = {}) {
  const api = useApi();
  return useQuery({
    queryKey: keys.system(),
    queryFn: ({ signal }) => api.get<SystemBody>('/api/system', { signal }),
    refetchInterval: (query) =>
      awaitingJob(query.state.data, startedJob) || query.state.data?.update?.state === 'running'
        ? UPDATE_POLL_MS
        : false,
  });
}

/**
 * How long a started job may take to write its record. Past it, the job did
 * not start: it failed before its first write, or another update held the lock.
 */
export const JOB_START_MS = 60_000;

/**
 * A job this client started: `after` is the `startedAt` of the record it
 * replaces. `stalled` is set once {@link JOB_START_MS} has passed.
 */
export interface StartedJob {
  after: string | null;
  stalled?: boolean;
}

/** Whether the job `started` has not yet written its own record. */
export function awaitingJob(info: SystemBody | undefined, started: StartedJob | undefined) {
  return (
    started !== undefined && !started.stalled && (info?.update?.startedAt ?? null) === started.after
  );
}

/** Whether the job `started` never wrote its record. */
export function jobStalled(info: SystemBody | undefined, started: StartedJob | undefined) {
  return started?.stalled === true && (info?.update?.startedAt ?? null) === started.after;
}

/** Start an **Update job**. The server answers once the OS supervisor has it. */
export function useStartUpdate() {
  const api = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<Record<string, never>>('/api/self/update', {}),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.system() }),
  });
}
