// @vitest-environment jsdom
// jsdom cascades declared values from a `<style>` rule into getComputedStyle,
// which is all the apply step needs: it computes no layout, and nothing here asks for it.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mountJig } from './overlay';

type Request = { method: string; url: string; body: string; client: string | null };

const listeners = new Map<string, (data: never) => void>();
const emit = (event: string, data: unknown) => listeners.get(event)!(data as never);
let requests: Request[];
let sendReply: Response;
let hideReply: Response;
let failPut: boolean;
let target: HTMLElement;
let unmount: () => void;
let capture: () => Promise<string | null>;
/** The draft files the fake serves, by name. */
let drafts: Record<string, string>;

const hot = {
  on: (event: string, callback: (data: never) => void) => listeners.set(event, callback),
};

function fakeFetch(visible: boolean) {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? 'GET';
    const client = new Headers(init?.headers).get('x-mael-jig-client');
    requests.push({ method, url: String(input), body: String(init?.body ?? ''), client });
    if (method === 'PUT') {
      if (failPut) throw new TypeError('Failed to fetch');
      return new Response(null, { status: 204 });
    }
    if (String(input) === '/__mael/hide') return hideReply;
    if (method === 'POST') return sendReply;
    if (String(input) === '/__mael/state') return Response.json({ visible });
    return new Response(drafts[String(input).replace('/__mael/drafts/', '')] ?? '');
  };
}

const puts = () => requests.filter((r) => r.method === 'PUT');

async function mount({ visible }: { visible: boolean }) {
  unmount = await mountJig({
    hot,
    fetch: fakeFetch(visible),
    debounceMs: 5,
    capture: () => capture(),
  });
}

function jig() {
  const root = document.querySelector('#mael-jig')?.shadowRoot;
  if (!root) throw new Error('no jig');
  return {
    textarea: root.querySelector('textarea[aria-label="Monkeypatch"]') as HTMLTextAreaElement,
    message: root.querySelector(
      'textarea[aria-label="Message to the agent"]',
    ) as HTMLTextAreaElement,
    sendMessage: button(root, 'Send message'),
    sendMonkeypatch: button(root, 'Send monkeypatch'),
    pick: button(root, 'Pick'),
    hide: button(root, 'Hide jig'),
    close: button(root, 'Close'),
    picked: root.querySelector('.picked') as HTMLElement,
    clearPick: root.querySelector('[aria-label="Clear the picked element"]') as HTMLButtonElement,
    panel: root.querySelector('.panel') as HTMLElement,
    status: root.querySelector('.status') as HTMLElement,
    title: root.querySelector('[data-title]') as HTMLElement,
    tab: (name: string) => tab(root, name),
    pane: (name: string) =>
      root.querySelector(`[role="tabpanel"][aria-label="${name}"]`) as HTMLElement,
    scopes: root.querySelector('.scope') as HTMLElement,
    scope: (label: string) => radio(root, label),
    askControls: button(root, 'Ask for controls'),
    sendControls: button(root, 'Send controls'),
  };
}

function tab(root: ShadowRoot, name: string) {
  const found = [...root.querySelectorAll('[role="tab"]')].find((t) => t.textContent === name);
  if (!found) throw new Error(`no ${name} tab`);
  return found as HTMLButtonElement;
}

function radio(root: ShadowRoot, label: string) {
  const found = [...root.querySelectorAll('label')].find((l) => l.textContent?.trim() === label);
  if (!found) throw new Error(`no ${label} choice`);
  return found.querySelector('input') as HTMLInputElement;
}

function button(root: ShadowRoot, label: string) {
  const found = [...root.querySelectorAll('button')].find((b) => b.textContent === label);
  if (!found) throw new Error(`no ${label} button`);
  return found;
}

function write(message: string) {
  const box = jig().message;
  box.value = message;
  box.dispatchEvent(new Event('input', { bubbles: true }));
}

const posts = () =>
  requests.filter((r) => r.url === '/__mael/feedback').map((r) => JSON.parse(r.body));

function type(css: string) {
  const { textarea } = jig();
  textarea.value = css;
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

const until = async (check: () => void) => {
  for (let i = 0; ; i++) {
    try {
      return check();
    } catch (error) {
      if (i > 50) throw error;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  }
};

beforeEach(() => {
  requests = [];
  failPut = false;
  capture = async () => null;
  drafts = { 'monkeypatch.css': '.chip { margin: 3px; }' };
  target = document.createElement('div');
  target.className = 'chip';
  document.body.append(target);
});

afterEach(() => {
  unmount();
  target.remove();
});

describe('a hidden jig', () => {
  beforeEach(() => mount({ visible: false }));

  it('puts nothing on the page', () => {
    expect(document.querySelector('#mael-jig')).toBeNull();
    expect(document.querySelector('style[data-mael-jig]')).toBeNull();
    expect(getComputedStyle(target).marginTop).not.toBe('3px');
  });

  it('opens with the CSS applied when the provider shows it', () => {
    emit('mael-jig:state', { visible: true });

    expect(jig().panel.hidden).toBe(false);
    expect(getComputedStyle(target).marginTop).toBe('3px');
  });

  it('comes off the page again when the provider hides it', () => {
    emit('mael-jig:state', { visible: true });
    emit('mael-jig:state', { visible: false });

    expect(document.querySelector('#mael-jig')).toBeNull();
    expect(document.querySelector('style[data-mael-jig]')).toBeNull();
  });
});

describe('the jig overlay', () => {
  beforeEach(() => mount({ visible: true }));

  it('applies the file it loads on start', () => {
    expect(jig().textarea.value).toBe('.chip { margin: 3px; }');
    expect(getComputedStyle(target).marginTop).toBe('3px');
  });

  it('applies typed CSS to the page at once, then writes it to the file', async () => {
    type('.chip { padding: 20px; }');

    expect(getComputedStyle(target).paddingTop).toBe('20px');
    await until(() => expect(puts().map((p) => p.body)).toEqual(['.chip { padding: 20px; }']));
  });

  it('keeps its rule last when the page adds a style after it', async () => {
    type('.chip { padding: 20px; }');

    const late = document.createElement('style');
    late.textContent = '.chip { padding: 1px; }';
    document.head.append(late);
    await Promise.resolve();

    expect(getComputedStyle(target).paddingTop).toBe('20px');
    late.remove();
  });

  it('follows a change made elsewhere', () => {
    emit('mael-jig:draft', {
      name: 'monkeypatch.css',
      text: '.chip { padding: 9px; }',
      from: null,
    });

    expect(jig().textarea.value).toBe('.chip { padding: 9px; }');
    expect(getComputedStyle(target).paddingTop).toBe('9px');
  });

  it('ignores the echo of its own write, so typing is not overwritten', async () => {
    type('.chip { padding: 1px; }');
    await until(() => expect(puts()).toHaveLength(1));
    type('.chip { padding: 12px; }');

    emit('mael-jig:draft', {
      name: 'monkeypatch.css',
      text: '.chip { padding: 1px; }',
      from: puts()[0]!.client,
    });

    expect(jig().textarea.value).toBe('.chip { padding: 12px; }');
  });

  it('asks the provider to hide it, and stays until the provider has', async () => {
    hideReply = new Response(null, { status: 204 });
    jig().hide.click();

    await until(() =>
      expect(requests.at(-1)).toMatchObject({ method: 'POST', url: '/__mael/hide' }),
    );
    expect(document.querySelector('#mael-jig')).not.toBeNull();
  });

  it('shows why a hide failed', async () => {
    hideReply = Response.json(
      { error: { message: 'The live feedback provider is not reachable' } },
      { status: 503 },
    );
    jig().hide.click();

    await until(() =>
      expect(jig().status.textContent).toBe('The live feedback provider is not reachable'),
    );
  });

  it('opens on the Monkeypatch tab when there is no control set', () => {
    expect(jig().title.textContent).toBe('Jig');
    expect(jig().tab('Monkeypatch').getAttribute('aria-selected')).toBe('true');
    expect(jig().pane('Monkeypatch').hidden).toBe(false);
    expect(jig().pane('Controls').hidden).toBe(true);

    jig().tab('Controls').click();

    expect(jig().pane('Controls').hidden).toBe(false);
    expect(jig().pane('Monkeypatch').hidden).toBe(true);
    expect(jig().pane('Controls').textContent).toContain('No control set');
  });

  it('switches to the Controls tab when the agent writes a set', () => {
    emit('mael-jig:draft', {
      name: 'jig-controls.json',
      text: JSON.stringify({ controls: [{ var: '--jig-gap', kind: 'text', from: '2px' }] }),
      from: null,
    });

    expect(jig().tab('Controls').getAttribute('aria-selected')).toBe('true');
    expect(jig().pane('Controls').hidden).toBe(false);
  });

  describe('send monkeypatch', () => {
    it('writes the file first, then posts the CSS with the message as its note', async () => {
      sendReply = Response.json({ agentIds: ['ag1', 'ag2'], refused: [] });
      type('.chip { padding: 20px; }');
      write('tighter');

      jig().sendMonkeypatch.click();

      await until(() => expect(jig().status.textContent).toBe('Sent to 2 agents'));
      expect(
        requests.filter((r) => r.method !== 'GET').map((r) => [r.method, r.url, r.body]),
      ).toEqual([
        ['PUT', '/__mael/drafts/monkeypatch.css', '.chip { padding: 20px; }'],
        [
          'POST',
          '/__mael/feedback',
          JSON.stringify({ type: 'monkeypatch', css: '.chip { padding: 20px; }', note: 'tighter' }),
        ],
      ]);
      expect(jig().message.value).toBe('');
      expect(jig().sendMonkeypatch.disabled).toBe(false);
    });

    it('posts once when pressed twice while the file write is pending', async () => {
      sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
      type('.chip { padding: 20px; }');

      jig().sendMonkeypatch.click();
      jig().sendMonkeypatch.click();

      await until(() => expect(jig().status.textContent).toBe('Sent to 1 agent'));
      expect(posts()).toHaveLength(1);
    });

    it('shows a failed file write, and does not post', async () => {
      failPut = true;
      type('.chip { padding: 20px; }');

      jig().sendMonkeypatch.click();

      await until(() => expect(jig().status.textContent).toMatch(/^Send failed: /));
      expect(posts()).toEqual([]);
    });

    it('shows a refusal and keeps the message', async () => {
      sendReply = Response.json(
        { error: { code: 'invalid', message: 'No agent is running in northwind-alpha' } },
        { status: 400 },
      );
      write('tighter');

      jig().sendMonkeypatch.click();

      await until(() =>
        expect(jig().status.textContent).toBe('No agent is running in northwind-alpha'),
      );
      expect(jig().message.value).toBe('tighter');
    });
  });

  describe('send message', () => {
    it('is off until there is text to send', () => {
      expect(jig().sendMessage.disabled).toBe(true);
      write('  ');
      expect(jig().sendMessage.disabled).toBe(true);
      write('Make the header sticky.');
      expect(jig().sendMessage.disabled).toBe(false);
    });

    it('posts the text alone as an instruction, then clears the box', async () => {
      sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
      write('Make the header sticky.');

      jig().sendMessage.click();

      await until(() => expect(jig().status.textContent).toBe('Sent to 1 agent'));
      expect(posts()).toEqual([{ type: 'instruction', text: 'Make the header sticky.' }]);
      expect(jig().message.value).toBe('');
      expect(jig().sendMessage.disabled).toBe(true);
    });

    it('sends a screenshot of the viewport with the text', async () => {
      sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
      capture = async () => 'data:image/jpeg;base64,/9j/';
      write('Bigger.');

      jig().sendMessage.click();

      await until(() => expect(posts()).toHaveLength(1));
      expect(posts()[0]).toEqual({
        type: 'instruction',
        text: 'Bigger.',
        screenshot: 'data:image/jpeg;base64,/9j/',
      });
    });

    it('sends the text alone when the screenshot fails', async () => {
      sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
      capture = async () => {
        throw new Error('The canvas is tainted');
      };
      write('Bigger.');

      jig().sendMessage.click();

      await until(() => expect(jig().status.textContent).toBe('Sent to 1 agent'));
      expect(posts()).toEqual([{ type: 'instruction', text: 'Bigger.' }]);
    });
  });

  describe('pick', () => {
    let appClicks: number;
    const countClick = () => appClicks++;

    beforeEach(() => {
      appClicks = 0;
      target.addEventListener('click', countClick);
    });
    afterEach(() => target.removeEventListener('click', countClick));

    const click = (element: Element) =>
      element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

    it('takes the click from the app, and shows the element it picked', () => {
      target.textContent = 'Save';
      jig().pick.click();

      click(target);

      expect(appClicks).toBe(0);
      expect(jig().picked.hidden).toBe(false);
      expect(jig().picked.textContent).toContain('div.chip');
      expect(jig().picked.textContent).toContain('Save');
    });

    it('ends after one pick, so the next click reaches the app', () => {
      jig().pick.click();
      click(target);
      click(target);

      expect(appClicks).toBe(1);
    });

    it('ends on Esc, and the app does not hear that Esc', () => {
      let appEscapes = 0;
      const countEscape = () => appEscapes++;
      document.addEventListener('keydown', countEscape);
      jig().pick.click();

      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      click(target);

      document.removeEventListener('keydown', countEscape);
      expect(jig().pick.getAttribute('aria-pressed')).toBe('false');
      expect(appEscapes).toBe(0);
      expect(appClicks).toBe(1);
    });

    it('takes the whole press, so the app hears no pointer or mouse button', () => {
      const heard: string[] = [];
      const presses = ['pointerdown', 'mousedown', 'pointerup', 'mouseup'];
      for (const type of presses) target.addEventListener(type, () => heard.push(type));
      jig().pick.click();

      for (const type of presses) {
        target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true }));
      }

      expect(heard).toEqual([]);
    });

    it('ends when the panel closes', () => {
      jig().pick.click();
      jig().close.click();
      click(target);

      expect(appClicks).toBe(1);
    });

    it('ends when the provider hides the jig', () => {
      jig().pick.click();
      emit('mael-jig:state', { visible: false });
      click(target);

      expect(appClicks).toBe(1);
    });

    it('ends on a second press of Pick', () => {
      jig().pick.click();
      jig().pick.click();
      click(target);

      expect(jig().picked.hidden).toBe(true);
    });

    it('never picks the jig itself', () => {
      jig().pick.click();
      click(document.querySelector('#mael-jig')!);

      expect(jig().picked.hidden).toBe(true);
      click(target);
      expect(jig().picked.hidden).toBe(false);
    });

    it('sends the message about the picked element, and keeps the pick', async () => {
      sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
      target.textContent = `  Save   the ${'chip '.repeat(30)}`;
      Object.assign(target, {
        __reactFiber$x1: {
          type: 'div',
          return: {
            type: function Chip() {},
            return: {
              type: { displayName: 'Toolbar' },
              return: { type: function App() {}, return: { type: function Root() {} } },
            },
          },
        },
      });
      jig().pick.click();
      click(target);
      write('Bigger.');

      jig().sendMessage.click();

      await until(() => expect(posts()).toHaveLength(1));
      const sent = posts()[0];
      // jsdom fills in defaults for the other styles; the margin is the one the page set.
      expect(sent).toEqual({
        type: 'instruction',
        text: 'Bigger.',
        element: {
          selector: 'div.chip',
          tag: 'div',
          classes: ['chip'],
          text: 'Save the chip chip chip chip chip chip chip chip chip chip chip chip chip chip c',
          components: ['Chip', 'Toolbar', 'App'],
          styles: expect.objectContaining({ margin: '3px' }),
          ancestors: ['body'],
          url: 'http://localhost:3000/',
        },
      });
      await until(() => expect(jig().message.value).toBe(''));
      expect(jig().picked.hidden).toBe(false);
    });

    it('names a component through forwardRef and memo', async () => {
      sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
      Object.assign(target, {
        __reactFiber$x1: {
          type: { render: function Field() {} },
          return: { type: { type: function Form() {} } },
        },
      });
      jig().pick.click();
      click(target);
      write('Bigger.');

      jig().sendMessage.click();

      await until(() => expect(posts()).toHaveLength(1));
      expect(posts()[0].element.components).toEqual(['Field', 'Form']);
    });

    it('leaves out components on a page that is not React, and names a Ladle story', async () => {
      sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
      history.replaceState(null, '', '/?story=chip--default');
      jig().pick.click();
      click(target);
      write('Bigger.');

      jig().sendMessage.click();

      await until(() => expect(posts()).toHaveLength(1));
      history.replaceState(null, '', '/');
      expect(posts()[0].element).not.toHaveProperty('components');
      expect(posts()[0].element.story).toBe('chip--default');
    });

    it('picks the element an icon sits in, not the icon', () => {
      target.innerHTML = '<button><svg><path d="M0 0"></path></svg>Sync</button>';
      jig().pick.click();

      click(target.querySelector('path')!);

      const code = jig().picked.querySelector('code')!;
      expect(code.textContent).toBe('button "Sync"');
      expect(code.title).toBe('div.chip > button');
    });

    it('sends a plain instruction once the pick is cleared', async () => {
      sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
      jig().pick.click();
      click(target);
      jig().clearPick.click();
      write('Bigger.');

      jig().sendMessage.click();

      await until(() => expect(posts()).toEqual([{ type: 'instruction', text: 'Bigger.' }]));
      expect(jig().picked.hidden).toBe(true);
    });

    describe('ask for controls', () => {
      it('shows the scope choice and the button only once an element is picked', () => {
        expect(jig().scopes.hidden).toBe(true);
        expect(jig().askControls.hidden).toBe(true);

        jig().pick.click();
        click(target);

        expect(jig().scopes.hidden).toBe(false);
        expect(jig().askControls.hidden).toBe(false);
        expect(jig().scope('This element').checked).toBe(true);
      });

      it.each([
        ['This element', 'element'],
        ['Elements like this', 'similar'],
        ['This area', 'area'],
      ])('posts a pick for %s with the message as its note', async (choice, scope) => {
        sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
        jig().pick.click();
        click(target);
        jig().scope(choice).click();
        write('roomier');

        jig().askControls.click();

        await until(() => expect(jig().status.textContent).toBe('Sent to 1 agent'));
        expect(posts()).toEqual([
          {
            type: 'pick',
            scope,
            element: expect.objectContaining({ selector: 'div.chip', tag: 'div' }),
            note: 'roomier',
          },
        ]);
        expect(jig().message.value).toBe('');
      });
    });

    describe('the selector', () => {
      let fixture: HTMLElement;
      beforeEach(() => {
        fixture = document.createElement('div');
        document.body.append(fixture);
      });
      afterEach(() => fixture.remove());

      const selectorOf = async (html: string, pick: string) => {
        sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
        fixture.innerHTML = html;
        jig().pick.click();
        click(fixture.querySelector(pick)!);
        write('x');
        jig().sendMessage.click();
        await until(() => expect(posts()).toHaveLength(1));
        return posts()[0].element.selector;
      };

      it('stops at an id', async () => {
        expect(
          await selectorOf('<nav id="toolbar"><button class="save">S</button></nav>', 'button'),
        ).toBe('#toolbar > button.save');
      });

      it('uses the stem of a CSS-module class, not its hash', async () => {
        const html =
          '<section id="s"><div class="_card_a1b2c_3"><span class="_chip_x9Y8z_12 on">c</span></div></section>';
        expect(await selectorOf(html, 'span')).toBe(
          '#s > div[class*="_card_"] > span[class*="_chip_"].on',
        );
      });

      it('counts among siblings of one shape with :nth-of-type', async () => {
        const html = '<ul id="list"><li>a</li><li>b</li><li>c</li></ul>';
        expect(await selectorOf(html, 'li:nth-child(2)')).toBe('#list > li:nth-of-type(2)');
      });

      it.each(['_r_2_', ':r2:', '«r2»'])(
        'passes over the id %s that React generated',
        async (id) => {
          const html = `<nav id="bar"><div id="${id}"><button>S</button></div></nav>`;
          expect(await selectorOf(html, 'button')).toBe('#bar > div > button');
        },
      );

      it('leaves :nth-of-type off when the siblings differ in class', async () => {
        const html = '<ul id="list"><li class="a">a</li><li class="b">b</li></ul>';
        expect(await selectorOf(html, 'li.b')).toBe('#list > li.b');
      });

      it('counts a sibling whose longer class holds the same stem', async () => {
        const html =
          '<ul id="list"><li class="_chip_label_abcde">a</li><li class="_chip_x9y8z">b</li></ul>';
        expect(await selectorOf(html, 'li:nth-child(2)')).toBe(
          '#list > li[class*="_chip_"]:nth-of-type(2)',
        );
      });

      it('climbs at most 4 levels', async () => {
        const html =
          '<div class="a"><div class="b"><div class="c"><div class="d"><i class="e">x</i></div></div></div></div>';
        expect(await selectorOf(html, 'i')).toBe('div.b > div.c > div.d > i.e');
      });
    });
  });
});

const CHIP_SET = {
  title: 'Chip spacing',
  controls: [
    {
      var: '--jig-chip-pad',
      label: 'Chip padding',
      kind: 'range',
      min: 0,
      max: 24,
      step: 1,
      unit: 'px',
      from: '4px',
    },
    { var: '--accent', kind: 'color' },
    { var: '--jig-align', kind: 'select', options: ['start', 'center'], from: 'start' },
  ],
};

const WIRING = '.chip { padding: var(--jig-chip-pad); }\n.toolbar { gap: var(--jig-align); }';

function controlRow(name: string) {
  const root = document.querySelector('#mael-jig')!.shadowRoot!;
  const row = root.querySelector(`.control[data-var="${name}"]`) as HTMLElement | null;
  if (!row) throw new Error(`no row for ${name}`);
  return {
    row,
    input: row.querySelector('input, select') as HTMLInputElement,
    text: row.querySelector('input[type="text"]') as HTMLInputElement,
    value: row.querySelector('output')!.textContent,
    reset: row.querySelector('[aria-label^="Reset"]') as HTMLButtonElement,
  };
}

function setControl(input: HTMLInputElement, value: string) {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

const rootVar = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const valuePuts = () =>
  puts()
    .filter((p) => p.url === '/__mael/drafts/jig-values.json')
    .map((p) => JSON.parse(p.body));

describe('a control set', () => {
  let token: HTMLStyleElement;

  beforeEach(async () => {
    // The app's own token, from a sheet the jig did not write.
    token = document.createElement('style');
    token.textContent = ':root { --accent: #3366ff; }';
    document.head.prepend(token);
    drafts['monkeypatch.css'] = WIRING;
    drafts['jig-controls.json'] = JSON.stringify(CHIP_SET);
    await mount({ visible: true });
  });
  afterEach(() => token.remove());

  it('draws a row per control on the Controls tab, under the set title', () => {
    expect(jig().title.textContent).toBe('Chip spacing');
    expect(jig().tab('Controls').getAttribute('aria-selected')).toBe('true');
    expect(controlRow('--jig-chip-pad').row.textContent).toContain('Chip padding');
    expect(controlRow('--jig-chip-pad').value).toBe('4px');
    expect(controlRow('--accent').row.textContent).toContain('--accent');
    expect(controlRow('--jig-align').input.localName).toBe('select');
  });

  it('starts each variable at its from, and a token at its value on the page', () => {
    expect(rootVar('--jig-chip-pad')).toBe('4px');
    expect(rootVar('--accent')).toBe('#3366ff');
    expect(controlRow('--accent').text.value).toBe('#3366ff');
  });

  it('sets the variable at once as a range moves, then writes the values file', async () => {
    setControl(controlRow('--jig-chip-pad').input, '10');

    expect(rootVar('--jig-chip-pad')).toBe('10px');
    expect(controlRow('--jig-chip-pad').value).toBe('10px');
    await until(() => expect(valuePuts()).toEqual([{ '--jig-chip-pad': '10px' }]));
  });

  it('sets a colour from its text field', () => {
    setControl(controlRow('--accent').text, '#ff0000');

    expect(rootVar('--accent')).toBe('#ff0000');
  });

  it('resets a control to its from, and writes the values file without it', async () => {
    setControl(controlRow('--jig-chip-pad').input, '10');
    controlRow('--jig-chip-pad').reset.click();

    expect(rootVar('--jig-chip-pad')).toBe('4px');
    expect(controlRow('--jig-chip-pad').value).toBe('4px');
    await until(() => expect(valuePuts().at(-1)).toEqual({}));
  });

  it('applies a value set on another page', () => {
    emit('mael-jig:draft', {
      name: 'jig-values.json',
      text: JSON.stringify({ '--jig-chip-pad': '16px' }),
      from: null,
    });

    expect(rootVar('--jig-chip-pad')).toBe('16px');
    expect(controlRow('--jig-chip-pad').input.value).toBe('16');
  });

  it('ignores the echo of its own values write', async () => {
    setControl(controlRow('--jig-chip-pad').input, '10');
    await until(() => expect(valuePuts()).toHaveLength(1));
    setControl(controlRow('--jig-chip-pad').input, '12');

    emit('mael-jig:draft', {
      name: 'jig-values.json',
      text: JSON.stringify({ '--jig-chip-pad': '10px' }),
      from: puts().at(-1)!.client,
    });

    expect(rootVar('--jig-chip-pad')).toBe('12px');
  });

  it('mutes a control whose wiring matches nothing on the page', () => {
    expect(controlRow('--jig-align').row.textContent).toContain('no match on this page');
    expect(controlRow('--jig-chip-pad').row.textContent).not.toContain('no match');
    // A token with no rule on the page: the jig cannot tell, so it is not muted.
    expect(controlRow('--accent').row.textContent).not.toContain('no match');
  });

  it('unmutes a control when the wiring changes to match', () => {
    emit('mael-jig:draft', {
      name: 'monkeypatch.css',
      text: `${WIRING}\n@media (min-width: 1px) { div.chip { gap: var(--jig-align); } }`,
      from: null,
    });

    expect(controlRow('--jig-align').row.textContent).not.toContain('no match');
  });

  it('matches wiring on a pseudo-class or pseudo-element by the element alone', () => {
    emit('mael-jig:draft', {
      name: 'monkeypatch.css',
      text: `${WIRING}\ndiv.chip:hover::before { gap: var(--jig-align); }`,
      from: null,
    });

    expect(controlRow('--jig-align').row.textContent).not.toContain('no match');
  });

  it('takes its overrides off the page when the agent deletes the set', () => {
    setControl(controlRow('--jig-chip-pad').input, '10');

    emit('mael-jig:draft', { name: 'jig-controls.json', text: '', from: null });

    expect(rootVar('--jig-chip-pad')).toBe('');
    expect(jig().pane('Controls').querySelector('.control')).toBeNull();
    expect(jig().pane('Controls').textContent).toContain('No control set');
  });

  it('sends a change with no start without a from', async () => {
    sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
    emit('mael-jig:draft', {
      name: 'jig-controls.json',
      text: JSON.stringify({ controls: [{ var: '--jig-new', kind: 'text' }] }),
      from: null,
    });
    setControl(controlRow('--jig-new').input, '6px');

    jig().sendControls.click();

    await until(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].changes).toEqual([{ var: '--jig-new', label: '--jig-new', to: '6px' }]);
  });

  it('redraws when the agent writes a new set', () => {
    emit('mael-jig:draft', {
      name: 'jig-controls.json',
      text: JSON.stringify({ controls: [{ var: '--jig-gap', kind: 'number', from: '2px' }] }),
      from: null,
    });

    expect(jig().title.textContent).toBe('Jig');
    expect(controlRow('--jig-gap').value).toBe('2px');
    expect(() => controlRow('--jig-chip-pad')).toThrow();
    expect(rootVar('--jig-gap')).toBe('2px');
  });

  it('keeps Send controls off until a value differs from its start', () => {
    const send = jig().sendControls;
    expect(send.disabled).toBe(true);

    setControl(controlRow('--jig-chip-pad').input, '4');
    expect(send.disabled).toBe(true);

    setControl(controlRow('--jig-chip-pad').input, '10');
    expect(send.disabled).toBe(false);
  });

  it('sends each changed value from its start, with the wiring and the note', async () => {
    sendReply = Response.json({ agentIds: ['ag1'], refused: [] });
    setControl(controlRow('--jig-chip-pad').input, '10');
    setControl(controlRow('--accent').text, '#ff0000');
    write('keep it tight');

    jig().sendControls.click();

    await until(() => expect(jig().status.textContent).toBe('Sent to 1 agent'));
    expect(posts()).toEqual([
      {
        type: 'controls',
        title: 'Chip spacing',
        changes: [
          { var: '--jig-chip-pad', label: 'Chip padding', from: '4px', to: '10px' },
          { var: '--accent', label: '--accent', from: '#3366ff', to: '#ff0000' },
        ],
        css: WIRING,
        note: 'keep it tight',
      },
    ]);
    expect(jig().message.value).toBe('');
  });

  it.each([
    ['{', 'the file is not JSON'],
    ['[]', 'the file has no controls list'],
    ['{"controls": [{"var": "chip-pad", "kind": "text"}]}', 'control 1 has no var'],
    ['{"controls": [{"kind": "range"}]}', 'control 1 has no var'],
    ['{"controls": [{"var": "--a", "kind": "dial"}]}', 'control 1 has an unknown kind'],
    [
      '{"controls": [{"var": "--a", "kind": "text"}, {"var": "--a", "kind": "text"}]}',
      'control 2 repeats --a',
    ],
    ['{"controls": [{"var": "--a", "kind": "select"}]}', 'control 1 has no options'],
  ])('names the fault in an invalid file %s', (text, fault) => {
    emit('mael-jig:draft', { name: 'jig-controls.json', text, from: null });

    expect(jig().pane('Controls').textContent).toContain(fault);
    expect(jig().pane('Controls').querySelector('.control')).toBeNull();
  });
});

describe('a control set with values from before', () => {
  beforeEach(async () => {
    drafts['jig-controls.json'] = JSON.stringify(CHIP_SET);
    drafts['jig-values.json'] = JSON.stringify({ '--jig-chip-pad': '8px', '--gone': '1px' });
    await mount({ visible: true });
  });

  it('applies them on start, and does not write back a var the set has lost', async () => {
    expect(rootVar('--jig-chip-pad')).toBe('8px');

    setControl(controlRow('--jig-align').input, 'center');

    await until(() =>
      expect(valuePuts()).toEqual([{ '--jig-chip-pad': '8px', '--jig-align': 'center' }]),
    );
  });
});
