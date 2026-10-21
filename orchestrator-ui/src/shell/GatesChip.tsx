import { useState } from 'react';
import { useGateJobs } from '../api/gateJobs';
import { SplitChip } from '../ui/SplitChip';
import { GatesDialog, isRunning } from './GatesDialog';

/**
 * `gates 4/8` in the top bar: the gate jobs running, over every gate job.
 * A click lists the jobs. See DESIGN.md, "Gates chip".
 */
export function GatesChip() {
  const jobs = useGateJobs().data?.gateJobs ?? [];
  const [open, setOpen] = useState(false);
  const running = jobs.filter(isRunning).length;
  if (jobs.length === 0 && !open) return null;
  return (
    <>
      {jobs.length > 0 && (
        <SplitChip
          label="gates"
          title={`${running} gate jobs running, ${jobs.length - running} queued. Click to list them.`}
          onClick={() => setOpen(true)}
        >
          {running}/{jobs.length}
        </SplitChip>
      )}
      {open && <GatesDialog jobs={jobs} onClose={() => setOpen(false)} />}
    </>
  );
}
