// The jig's overlay: the `Jig` pill and its panel. See docs/dev/orchestrator-ui.md,
// "The jig".

interface Update {
  css: string;
  from: string | null;
}

interface Options {
  hot: { on: (event: 'mael-jig:monkeypatch', callback: (data: Update) => void) => void };
  fetch?: typeof fetch;
  debounceMs?: number;
}

const STYLE = `
:host { all: initial; position: fixed; right: 12px; bottom: 12px; z-index: 2147483647;
  font: 12px/1.4 ui-sans-serif, system-ui, sans-serif; color: #e6e6e6; }
.pill, button { font: inherit; color: inherit; background: #2b2b33; border: 1px solid #4a4a55;
  border-radius: 6px; padding: 4px 10px; cursor: pointer; }
.pill { border-radius: 999px; }
button:disabled { opacity: 0.5; cursor: default; }
.panel { display: flex; flex-direction: column; gap: 6px; width: 360px; padding: 8px;
  background: #1c1c22; border: 1px solid #4a4a55; border-radius: 8px;
  box-shadow: 0 6px 24px rgb(0 0 0 / 0.4); }
.panel[hidden] { display: none; }
textarea, input { font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; color: inherit;
  background: #111116; border: 1px solid #4a4a55; border-radius: 4px; padding: 6px; }
textarea { height: 180px; resize: vertical; }
input { font-family: inherit; }
.row { display: flex; gap: 6px; align-items: center; justify-content: space-between; }
.status { color: #a0a0aa; }
`;

const PANEL = `
<style>${STYLE}</style>
<button class="pill" data-toggle>Jig</button>
<div class="panel" hidden>
  <div class="row"><strong>Monkeypatch</strong><button data-close>Close</button></div>
  <textarea spellcheck="false" aria-label="Monkeypatch"></textarea>
  <input data-note placeholder="Note to the agent (optional)" aria-label="Note" />
  <div class="row"><span class="status" role="status"></span>
    <button data-send>Send to agent</button></div>
</div>
`;

/** Mount the jig on the page. The promise holds the function that takes it off again. */
export async function mountJig({
  hot,
  fetch = window.fetch.bind(window),
  debounceMs = 300,
}: Options): Promise<() => void> {
  // Not `crypto.randomUUID`: a page served over plain http on the tailnet is not
  // a secure context, and has none.
  const client = Math.random().toString(36).slice(2);

  const style = document.createElement('style');
  style.dataset.maelJig = '';
  const keepLast = () => {
    if (document.head.lastElementChild !== style) document.head.append(style);
  };
  keepLast();
  // Vite appends a module's `<style>` when it loads or hot-updates, which would
  // put the app's rule after this one and win a tie of specificity.
  const observer = new MutationObserver(keepLast);
  observer.observe(document.head, { childList: true });

  const host = document.createElement('div');
  host.id = 'mael-jig';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = PANEL;
  document.body.append(host);

  const $ = <T extends Element>(selector: string) => root.querySelector(selector) as T;
  const panel = $<HTMLDivElement>('.panel');
  const pill = $<HTMLButtonElement>('[data-toggle]');
  const textarea = $<HTMLTextAreaElement>('textarea');
  const note = $<HTMLInputElement>('[data-note]');
  const sendButton = $<HTMLButtonElement>('[data-send]');
  const status = $<HTMLSpanElement>('.status');

  const show = (open: boolean) => {
    panel.hidden = !open;
    pill.hidden = open;
  };
  pill.addEventListener('click', () => show(true));
  $<HTMLButtonElement>('[data-close]').addEventListener('click', () => show(false));

  const apply = (css: string) => {
    style.textContent = css;
  };

  let pending: ReturnType<typeof setTimeout> | undefined;
  const write = () => {
    clearTimeout(pending);
    pending = undefined;
    return fetch('/__mael/monkeypatch', {
      method: 'PUT',
      headers: { 'x-mael-jig-client': client },
      body: textarea.value,
    });
  };
  textarea.addEventListener('input', () => {
    apply(textarea.value);
    clearTimeout(pending);
    pending = setTimeout(write, debounceMs);
  });

  hot.on('mael-jig:monkeypatch', ({ css, from }) => {
    if (from === client) return;
    textarea.value = css;
    apply(css);
  });

  // Off while a send is in flight: a second click would post to every agent again.
  sendButton.addEventListener('click', async () => {
    sendButton.disabled = true;
    status.textContent = 'Sending…';
    try {
      if (pending) await write();
      const reply = await fetch('/__mael/feedback', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ type: 'monkeypatch', css: textarea.value, note: note.value }),
      });
      const body = await reply.json();
      if (reply.ok) {
        const count = body.agentIds.length;
        status.textContent = `Sent to ${count} agent${count === 1 ? '' : 's'}`;
        note.value = '';
      } else {
        status.textContent = body.error?.message ?? `Send failed (${reply.status})`;
      }
    } catch (error) {
      status.textContent = `Send failed: ${String(error)}`;
    } finally {
      sendButton.disabled = false;
    }
  });

  // Read-only until the file loads, or the load would replace what the user typed.
  textarea.readOnly = true;
  const loaded = await (await fetch('/__mael/monkeypatch')).json();
  textarea.readOnly = false;
  textarea.value = loaded.css;
  apply(loaded.css);
  sendButton.disabled = !loaded.canSend;
  if (!loaded.canSend) sendButton.title = 'No orchestrator is set for this dev server';
  if (loaded.css) show(true);

  return () => {
    clearTimeout(pending);
    observer.disconnect();
    style.remove();
    host.remove();
  };
}
