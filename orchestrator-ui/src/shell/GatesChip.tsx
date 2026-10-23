import { useGateJobs } from '../api/gateJobs';
import { useAppStore } from '../store/store';
import { SplitChip } from '../ui/SplitChip';
import { GatesDialog, isRunning } from './GatesDialog';

/**
 * `gates 4/8` in the top bar: the gate jobs running, over every gate job.
 * A click lists the jobs. See DESIGN.md, "Gates chip". The one place the
 * dialog draws: a gate lozenge opens it through the store.
 */
export function GatesChip() {
  const jobs = useGateJobs().data?.gateJobs ?? [];
  const { open, jobId } = useAppStore((s) => s.ui.gates);
  const openGates = useAppStore((s) => s.openGates);
  const closeGates = useAppStore((s) => s.closeGates);
  const running = jobs.filter(isRunning).length;
  if (jobs.length === 0 && !open) return null;
  return (
    <>
      {jobs.length > 0 && (
        <SplitChip
          label="gates"
          title={`${running} gate jobs running, ${jobs.length - running} queued. Click to list them.`}
          onClick={() => openGates()}
        >
          {running}/{jobs.length}
        </SplitChip>
      )}
      {open && <GatesDialog jobs={jobs} markedJobId={jobId} onClose={closeGates} />}
    </>
  );
}
