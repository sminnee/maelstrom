import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { SRC, withoutComments } from '../test/sourceGate';

/**
 * The rules that keep a field on the soft keyboard. jsdom applies no CSS, so
 * this reads the stylesheet text: it proves a rule is present, and a phone
 * proves that it works. See DESIGN.md, "The Still Screen Rule".
 */

const sheet = (path: string) => withoutComments(readFileSync(join(SRC, path), 'utf8'));

/** The body of the narrow-layout media block, up to its closing brace. */
function narrow(css: string): string {
  const start = css.indexOf('@media (max-width: 839px)');
  expect(start).toBeGreaterThanOrEqual(0);
  let depth = 0;
  for (let i = css.indexOf('{', start); i < css.length; i++) {
    if (css[i] === '{') depth++;
    if (css[i] === '}' && --depth === 0) return css.slice(start, i);
  }
  throw new Error('unclosed media block');
}

/** The declarations of the first rule whose selector is exactly `selector`. */
function rule(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = new RegExp(`(?:^|[}\\s])${escaped}\\s*\\{([^}]*)\\}`).exec(css);
  expect(match, `no rule for ${selector}`).not.toBeNull();
  return match?.[1] ?? '';
}

describe('the mobile stylesheets', () => {
  const dialog = sheet('ui/Dialog.module.css');

  it('scroll a dialog body, so its header and footer stay in the box', () => {
    expect(rule(dialog, '.body')).toMatch(/overflow:\s*auto/);
    expect(rule(dialog, '.body')).toMatch(/min-height:\s*0/);
  });

  it('stop a narrow dialog with a body scrolling as a whole', () => {
    expect(rule(narrow(dialog), '.dialog:has(> .body)')).toMatch(/overflow:\s*hidden/);
  });

  it('cap the narrow composer by lines, not by the visible height', () => {
    const input = rule(narrow(sheet('session/MessageInput.module.css')), '.input');
    expect(input).toMatch(/max-height:\s*8\.5em/);
  });

  it('pack the narrow New work form to its foot, with a short draft', () => {
    const newWork = narrow(sheet('newwork/NewWork.module.css'));
    // Not `justify-content: flex-end`, which puts the top of the form out of reach.
    expect(rule(newWork, '.body > :first-child')).toMatch(/margin-top:\s*auto/);
    expect(newWork).not.toMatch(/flex-end/);
    expect(rule(newWork, '.draft')).toMatch(/max-height:\s*8\.5em/);
  });
});
