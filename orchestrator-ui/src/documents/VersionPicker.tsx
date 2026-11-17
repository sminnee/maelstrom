import type { DocumentVersion } from '../protocol/documents';
import { clockTime } from '../protocol/time';
import { useNow } from '../ui/useNow';
import { describeDocumentStatus } from '../selectors/status';
import styles from './DocumentTab.module.css';

/**
 * The document's stored versions, newest first: `v3 · approved · 12 Nov`.
 * Picking the latest is `undefined`, so the tab follows the versions to come.
 */
export function VersionPicker({
  versions,
  shown,
  onPick,
}: {
  versions: readonly DocumentVersion[];
  shown: number;
  onPick: (version: number | undefined) => void;
}) {
  const latest = versions.at(-1)?.version;
  const now = useNow();
  return (
    <select
      aria-label="Version"
      className={styles.versionPicker}
      value={shown}
      onChange={(e) => {
        const picked = Number(e.target.value);
        onPick(picked === latest ? undefined : picked);
      }}
    >
      {[...versions].reverse().map((v) => (
        <option key={v.version} value={v.version}>
          v{v.version} · {describeDocumentStatus(v.reviewStatus)} · {clockTime(v.createdAt, now)}
        </option>
      ))}
    </select>
  );
}
