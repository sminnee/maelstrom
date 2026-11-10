import { useGateJobs } from '../api/gateJobs';
import { useAppStore } from '../store/store';
import { SplitChip } from '../ui/SplitChip';
import { isRunning } from './GatesDialog';

/**
 * `gates 4/8` in the top bar, or in the Welcome dialog on the narrow layout:
 * the gate jobs running, over every gate job. A click lists the jobs. See
 * DESIGN.md, "Gates chip".
 */
export function GatesChip() {
  const jobs = useGateJobs().data?.gateJobs ?? [];
  const openGates = useAppStore((s) => s.openGates);
  const running = jobs.filter(isRunning).length;
  if (jobs.length === 0) return null;
  return (
    <SplitChip
      label="gates"
      title={`${running} gate jobs running, ${jobs.length - running} queued. Click to list them.`}
      onClick={() => openGates()}
    >
      {running}/{jobs.length}
    </SplitChip>
  );
}
