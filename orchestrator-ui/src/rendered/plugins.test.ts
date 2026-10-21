import { describe, expect, it } from 'vitest';
import { rendererFor } from './plugins';

describe('rendererFor', () => {
  it('draws a feature file as scenarios', () => {
    expect(rendererFor('specs/orders/Checkout.feature').id).toBe('gherkin');
  });

  it('draws a markdown file as a document', () => {
    for (const path of ['README.md', 'docs/guide.markdown', 'NOTES.MD'])
      expect(rendererFor(path).id).toBe('markdown');
  });

  it('draws an image as its two sides', () => {
    for (const path of ['docs/shot.png', 'logo.svg', 'photo.JPEG', 'favicon.ico'])
      expect(rendererFor(path).id).toBe('image');
  });

  it('draws any other file as text', () => {
    for (const path of ['src/app.ts', 'Dockerfile', 'notes', 'png'])
      expect(rendererFor(path).id).toBe('text');
  });
});
