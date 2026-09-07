import { useState } from 'react';
import { createPortal } from 'react-dom';
import { Dialog } from './Dialog';
import styles from './ImageLightbox.module.css';

/**
 * One picture in a message: a thumbnail that opens the full image.
 *
 * A screenshot is often larger than the column it lands in, so the thumbnail
 * is capped and the full picture lives behind a click. The button is a real
 * `<button>`, not a click handler on the image, so the picture is reachable
 * by keyboard as well as by mouse.
 *
 * The overlay is `Dialog`, which already owns the scrim, Escape and the click
 * outside. Nothing about closing is re-implemented here.
 *
 * It is portalled to the body because markdown puts an image inside a `<p>`,
 * and a scrim rendered there would be a `<div>` inside a paragraph — which the
 * browser unnests, dropping it out of its own overlay.
 */
export function ImageLightbox({ src, alt }: { src: string; alt: string }) {
  const [open, setOpen] = useState(false);
  // `![](/x.png)` is legal markdown, so the alt may be empty. A name built
  // around an empty one reads as a dangling dash to a screen reader, and
  // `aria-label=""` leaves the dialog with no name at all.
  const openLabel = alt ? `${alt} — open full size` : 'Open image full size';
  const dialogLabel = alt || 'Image';
  return (
    <>
      <button
        type="button"
        className={styles.thumb}
        onClick={() => setOpen(true)}
        aria-label={openLabel}
      >
        <img src={src} alt={alt} />
      </button>
      {open &&
        createPortal(
          <Dialog
            label={dialogLabel}
            onClose={() => setOpen(false)}
            testId="image-lightbox"
            className={styles.box}
          >
            <img className={styles.full} src={src} alt={alt} />
          </Dialog>,
          document.body,
        )}
    </>
  );
}
