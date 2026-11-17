import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { fakeFragments } from './fragments';

const SLUG_CASES: { name: string; markdown: string; slugs: string[] }[] = JSON.parse(
  readFileSync(resolve(__dirname, '../../../lib/domain/fixtures/heading-slugs.json'), 'utf8'),
).cases;

describe("the fake server's fragments", () => {
  it.each(SLUG_CASES.map((c) => [c.name, c] as const))(
    'are the server fragments: %s',
    (_, { markdown, slugs }) => {
      expect(fakeFragments(markdown)).toEqual(slugs);
    },
  );
});
