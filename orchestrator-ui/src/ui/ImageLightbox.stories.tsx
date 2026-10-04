import type { Story } from '@ladle/react';
import { ImageLightbox } from './ImageLightbox';

export default { title: 'UI / ImageLightbox' };

/**
 * A picture of a fixed natural size, so the opened lightbox can be measured
 * against it: a small one must not grow, a large one must fit and keep its
 * ratio.
 */
function picture(width: number, height: number): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">` +
    `<rect width="100%" height="100%" fill="#3b82f6"/>` +
    `<circle cx="${width / 2}" cy="${height / 2}" r="${Math.min(width, height) / 2}" fill="#f59e0b"/>` +
    `</svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

export const Small: Story = () => <ImageLightbox src={picture(160, 100)} alt="Small, 160×100" />;

export const Wide: Story = () => <ImageLightbox src={picture(3000, 1200)} alt="Wide, 3000×1200" />;

export const Tall: Story = () => <ImageLightbox src={picture(800, 3000)} alt="Tall, 800×3000" />;
