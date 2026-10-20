import { useState } from 'react';
import { createPortal } from 'react-dom';
import { useWorktreeFile } from '../api/worktreeChanges';
import type { FileDiff } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { Dialog, DialogHeader } from '../ui/Dialog';
import { SegmentedControl } from '../ui/SegmentedControl';
import type { Mode } from './plugin';
import { rendererFor } from './plugins';
import styles from './RenderedFileDialog.module.css';

const MODES: { mode: Mode; label: string }[] = [
  { mode: 'final', label: 'Final' },
  { mode: 'whole', label: 'Whole file' },
  { mode: 'changes', label: 'Changes only' },
];

/**
 * The **Rendered view**: one file of a rev, drawn whole by its renderer plugin,
 * in one of three modes.
 *
 * Portalled to the body, as `ImageLightbox` is: a file's head is sticky and
 * clips what sits inside it.
 */
export function RenderedFileDialog({
  worktreeId,
  rev,
  file,
  onClose,
}: {
  worktreeId: WorktreeId;
  rev: string;
  file: FileDiff;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<Mode>('changes');
  const versions = useWorktreeFile(worktreeId, rev, file.path, file.oldPath);
  const plugin = rendererFor(file.path);
  const deleted = file.status === 'deleted';
  // Final has nothing to draw for a deleted file.
  const shown = deleted && mode === 'final' ? 'changes' : mode;
  const data = versions.data;
  return createPortal(
    <Dialog
      label={`${file.path}, rendered`}
      onClose={onClose}
      testId="rendered-file"
      className={styles.box}
    >
      <DialogHeader title={file.path} onClose={onClose}>
        <span className={styles.plugin}>{plugin.label}</span>
        <SegmentedControl<Mode>
          label="Mode"
          items={MODES.map(({ mode: m, label }) => ({
            id: m,
            label,
            disabled: m === 'final' && deleted,
          }))}
          pressed={shown}
          onPress={setMode}
        />
      </DialogHeader>
      <div className={styles.body}>
        {!data ? (
          <p className={styles.note} role={versions.isError ? 'alert' : undefined}>
            {versions.isError
              ? `Could not read the file: ${versions.error.message}`
              : 'Reading the file…'}
          </p>
        ) : data.old === null && data.new === null ? (
          <p className={styles.note}>
            {data.truncated ? 'This file is too large to show.' : 'Binary file, not shown.'}
          </p>
        ) : (
          <>
            {data.truncated && (
              <p className={styles.note}>One side is too large to show, so it reads as empty.</p>
            )}
            <plugin.View old={data.old} new={data.new} mode={shown} path={file.path} />
          </>
        )}
      </div>
    </Dialog>,
    document.body,
  );
}
