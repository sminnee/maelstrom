import { useState } from 'react';
import { ImageLightbox } from '../../ui/ImageLightbox';
import type { RenderedProps, Side } from '../plugin';
import styles from '../rendered.module.css';

const LABEL: Record<Side, string> = { old: 'Before', new: 'After' };

/**
 * An image as its two sides, side by side: Before and After. A side the file
 * does not have drops its column, and the other is tinted added or removed.
 * Final draws the After side alone.
 */
export function ImageFileView({ src, mode, path }: RenderedProps) {
  if (mode === 'final') {
    if (src.new === null) return <p className={styles.note}>Deleted file.</p>;
    return (
      <div className={styles.images}>
        <ImageSide src={src.new} alt={path} />
      </div>
    );
  }
  const change = src.old === null ? 'added' : src.new === null ? 'removed' : null;
  const sides = (['old', 'new'] as const).filter((side) => src[side] !== null);
  return (
    <div className={styles.images}>
      {sides.map((side) => (
        <ImageSide
          key={side}
          src={src[side]!}
          alt={`${path}, ${LABEL[side].toLowerCase()}`}
          label={change ? undefined : LABEL[side]}
          change={change}
        />
      ))}
    </div>
  );
}

function ImageSide({
  src,
  alt,
  label,
  change = null,
}: {
  src: string;
  alt: string;
  label?: string;
  change?: 'added' | 'removed' | null;
}) {
  const [size, setSize] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  return (
    <figure className={styles.image} data-change={change ?? undefined}>
      {failed ? (
        <p className={styles.note} role="status">
          Could not load the image.
        </p>
      ) : (
        <div className={styles.checkerboard}>
          <ImageLightbox
            src={src}
            alt={alt}
            onLoad={(img) => setSize(`${img.naturalWidth} × ${img.naturalHeight}`)}
            onError={() => setFailed(true)}
          />
        </div>
      )}
      {(label || change || size) && (
        <figcaption className={styles.imageCaption}>
          {label && <span className={styles.imageLabel}>{label}</span>}
          {change && (
            <span className={styles.imageLabel}>{change === 'added' ? 'Added' : 'Removed'}</span>
          )}
          {size && <span>{size}</span>}
        </figcaption>
      )}
    </figure>
  );
}
