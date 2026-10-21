import { useCallback, useState } from 'react';
import { createPortal } from 'react-dom';
import { useWorktreeFile, worktreeFileUrl } from '../api/worktreeChanges';
import type { FileDiff } from '../protocol/entities';
import type { WorktreeId } from '../protocol/ids';
import { Dialog, DialogHeader } from '../ui/Dialog';
import { SegmentedControl } from '../ui/SegmentedControl';
import type { DisplayOption, Mode, Side } from './plugin';
import { rendererFor } from './plugins';
import styles from './RenderedFileDialog.module.css';
import { TextView } from './TextView';

const MODES: { mode: Mode; label: string }[] = [
  { mode: 'final', label: 'Final' },
  { mode: 'whole', label: 'Whole file' },
  { mode: 'changes', label: 'Changes only' },
];

const RAW: DisplayOption = { id: 'raw', label: 'Raw' };

/**
 * The **Rendered view**: one file of a rev, drawn whole by its renderer plugin,
 * in one of three modes and, for a plugin with displays, one of them or Raw.
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
  const [display, setDisplay] = useState<string | null>(null);
  const plugin = rendererFor(file.path);
  const bytes = plugin.reads === 'bytes';
  const versions = useWorktreeFile(worktreeId, rev, file.path, file.oldPath, { enabled: !bytes });
  const deleted = file.status === 'deleted';
  // Final has nothing to draw for a deleted file.
  const shown = deleted && mode === 'final' ? 'changes' : mode;
  const data = bytes ? { old: null, new: null, truncated: false } : versions.data;
  // Stable, so a Markdown block's memo holds across renders.
  const sideUrl = useCallback(
    (path: string, side: Side) => worktreeFileUrl(worktreeId, rev, path, side),
    [worktreeId, rev],
  );
  const src = {
    old:
      file.status === 'added'
        ? null
        : worktreeFileUrl(worktreeId, rev, file.path, 'old', file.oldPath),
    new: deleted ? null : sideUrl(file.path, 'new'),
  };
  const displays = plugin.displays && [...plugin.displays, RAW];
  const shownDisplay = displays?.find((d) => d.id === display)?.id ?? displays?.[0]?.id;
  const Draw = shownDisplay === RAW.id ? TextView : plugin.View;
  return createPortal(
    <Dialog
      label={`${file.path}, rendered`}
      onClose={onClose}
      testId="rendered-file"
      className={styles.box}
    >
      <DialogHeader title={file.path} onClose={onClose}>
        {displays && (
          <SegmentedControl
            label="Display"
            items={displays}
            pressed={shownDisplay}
            onPress={setDisplay}
          />
        )}
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
        ) : !bytes && data.old === null && data.new === null ? (
          <p className={styles.note}>
            {data.truncated ? 'This file is too large to show.' : 'Binary file, not shown.'}
          </p>
        ) : (
          <>
            {data.truncated && (
              <p className={styles.note}>
                One version is too large to show, so it is drawn as empty.
              </p>
            )}
            <Draw
              old={data.old}
              new={data.new}
              mode={shown}
              display={shownDisplay}
              truncated={data.truncated}
              path={file.path}
              oldPath={file.oldPath}
              src={src}
              sideUrl={sideUrl}
            />
          </>
        )}
      </div>
    </Dialog>,
    document.body,
  );
}
