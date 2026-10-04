// @vitest-environment jsdom
// jsdom cascades declared values from a `<style>` rule into getComputedStyle,
// which is all the apply step needs: it computes no layout, and nothing here asks for it.
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mountJig } from './overlay';

type Listener = (data: { css: string; from: string | null }) => void;
type Request = { method: string; url: string; body: string; client: string | null };

let listener: Listener;
let requests: Request[];
let sendReply: Response;
let target: HTMLElement;
let unmount: () => void;

const hot = { on: (_event: string, callback: Listener) => (listener = callback) };

function fakeFetch(canSend: boolean) {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? 'GET';
    const client = new Headers(init?.headers).get('x-mael-jig-client');
    requests.push({ method, url: String(input), body: String(init?.body ?? ''), client });
    if (method === 'PUT') return new Response(null, { status: 204 });
    if (method === 'POST') return sendReply;
    return Response.json({ css: '.chip { margin: 3px; }', canSend });
  };
}

const puts = () => requests.filter((r) => r.method === 'PUT');

async function mount({ canSend }: { canSend: boolean }) {
  unmount = await mountJig({ hot, fetch: fakeFetch(canSend), debounceMs: 5 });
}

function jig() {
  const root = document.querySelector('#mael-jig')?.shadowRoot;
  if (!root) throw new Error('no jig');
  return {
    textarea: root.querySelector('textarea') as HTMLTextAreaElement,
    note: root.querySelector('input[data-note]') as HTMLInputElement,
    send: root.querySelector('button[data-send]') as HTMLButtonElement,
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

describe('the jig overlay', () => {
  beforeEach(() => mount({ canSend: false }));

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
    listener({ css: '.chip { padding: 9px; }', from: null });

    expect(jig().textarea.value).toBe('.chip { padding: 9px; }');
    expect(getComputedStyle(target).paddingTop).toBe('9px');
  });

  it('ignores the echo of its own write, so typing is not overwritten', async () => {
    type('.chip { padding: 1px; }');
    await until(() => expect(puts()).toHaveLength(1));
    type('.chip { padding: 12px; }');

    listener({ css: '.chip { padding: 1px; }', from: puts()[0]!.client });

    expect(jig().textarea.value).toBe('.chip { padding: 12px; }');
  });

  it('turns Send off when no orchestrator is set', () => {
    expect(jig().send.disabled).toBe(true);
  });
});

describe('send', () => {
  beforeEach(() => mount({ canSend: true }));

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
