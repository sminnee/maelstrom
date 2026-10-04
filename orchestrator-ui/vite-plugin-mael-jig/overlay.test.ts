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
let target: HTMLElement;
let unmount: () => void;

const hot = {
  on: (event: string, callback: (data: never) => void) => listeners.set(event, callback),
};

function fakeFetch(visible: boolean) {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? 'GET';
    const client = new Headers(init?.headers).get('x-mael-jig-client');
    requests.push({ method, url: String(input), body: String(init?.body ?? ''), client });
    if (method === 'PUT') return new Response(null, { status: 204 });
    if (String(input) === '/__mael/hide') return hideReply;
    if (method === 'POST') return sendReply;
    return Response.json({ css: '.chip { margin: 3px; }', visible });
  };
}

const puts = () => requests.filter((r) => r.method === 'PUT');

async function mount({ visible }: { visible: boolean }) {
  unmount = await mountJig({ hot, fetch: fakeFetch(visible), debounceMs: 5 });
}

function jig() {
  const root = document.querySelector('#mael-jig')?.shadowRoot;
  if (!root) throw new Error('no jig');
  return {
    textarea: root.querySelector('textarea') as HTMLTextAreaElement,
    note: root.querySelector('input[data-note]') as HTMLInputElement,
    send: root.querySelector('button[data-send]') as HTMLButtonElement,
    hide: root.querySelector('button[data-hide]') as HTMLButtonElement,
    panel: root.querySelector('.panel') as HTMLElement,
    status: root.querySelector('.status') as HTMLElement,
  };
}

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
    emit('mael-jig:monkeypatch', { css: '.chip { padding: 9px; }', from: null });

    expect(jig().textarea.value).toBe('.chip { padding: 9px; }');
    expect(getComputedStyle(target).paddingTop).toBe('9px');
  });

  it('ignores the echo of its own write, so typing is not overwritten', async () => {
    type('.chip { padding: 1px; }');
    await until(() => expect(puts()).toHaveLength(1));
    type('.chip { padding: 12px; }');

    emit('mael-jig:monkeypatch', { css: '.chip { padding: 1px; }', from: puts()[0]!.client });

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
      { error: { message: 'The jig provider is not reachable' } },
      { status: 503 },
    );
    jig().hide.click();

    await until(() => expect(jig().status.textContent).toBe('The jig provider is not reachable'));
  });

  describe('send', () => {
    it('writes the file first, then posts the CSS and the note', async () => {
      sendReply = Response.json({ agentIds: ['ag1', 'ag2'], refused: [] });
      type('.chip { padding: 20px; }');
      jig().note.value = 'tighter';

      jig().send.click();

      await until(() => expect(jig().status.textContent).toBe('Sent to 2 agents'));
      expect(requests.slice(1).map((r) => [r.method, r.url, r.body])).toEqual([
        ['PUT', '/__mael/monkeypatch', '.chip { padding: 20px; }'],
        [
          'POST',
          '/__mael/feedback',
          JSON.stringify({ type: 'monkeypatch', css: '.chip { padding: 20px; }', note: 'tighter' }),
        ],
      ]);
      expect(jig().note.value).toBe('');
      expect(jig().send.disabled).toBe(false);
    });

    it('shows a refusal and keeps the note', async () => {
      sendReply = Response.json(
        { error: { code: 'invalid', message: 'No agent is running in northwind-alpha' } },
        { status: 400 },
      );
      jig().note.value = 'tighter';

      jig().send.click();

      await until(() =>
        expect(jig().status.textContent).toBe('No agent is running in northwind-alpha'),
      );
      expect(jig().note.value).toBe('tighter');
    });
  });
});
