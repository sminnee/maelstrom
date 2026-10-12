// The jig's overlay: the `Jig` pill and its panel. See docs/dev/orchestrator-ui.md,
// "The jig".

interface Update {
  css: string;
  from: string | null;
}

interface Hot {
  on(event: 'mael-jig:monkeypatch', callback: (data: Update) => void): void;
  on(event: 'mael-jig:state', callback: (data: { visible: boolean }) => void): void;
}

interface Options {
  hot: Hot;
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
textarea[data-message] { height: 60px; font-family: inherit; }
.row { display: flex; gap: 6px; align-items: center; justify-content: space-between; }
.status { flex: 1; min-width: 0; color: #a0a0aa; }
.row > span:last-child { flex: none; }
button[aria-pressed='true'] { background: #3d5afe; border-color: #3d5afe; }
.picked { display: flex; gap: 6px; align-items: center; padding: 4px 6px;
  background: #111116; border: 1px solid #4a4a55; border-radius: 4px; }
.picked[hidden], .outline[hidden] { display: none; }
.picked code { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picked button { padding: 0 6px; }
.outline { position: fixed; pointer-events: none; box-sizing: border-box;
  border: 2px solid #3d5afe; background: rgb(61 90 254 / 0.12); }
`;

const PANEL = `
<style>${STYLE}</style>
<button class="pill" data-toggle>Jig</button>
<div class="outline" hidden></div>
<div class="panel" hidden>
  <div class="row"><strong>Jig</strong>
    <span><button data-pick aria-pressed="false">Pick</button>
      <button data-hide>Hide jig</button> <button data-close>Close</button></span></div>
  <textarea spellcheck="false" aria-label="Monkeypatch" placeholder="Monkeypatch CSS"></textarea>
  <div class="picked" hidden><code></code>
    <button data-clear-pick aria-label="Clear the picked element">×</button></div>
  <textarea data-message aria-label="Message to the agent"
    placeholder="Message to the agent"></textarea>
  <div class="row"><span class="status" role="status"></span>
    <span><button data-send-monkeypatch>Send monkeypatch</button>
      <button data-send-message>Send message</button></span></div>
</div>
`;

/** The computed properties a picked element's description carries. */
const PICKED_STYLES = [
  'color',
  'background-color',
  'font-size',
  'font-weight',
  'line-height',
  'padding',
  'margin',
  'gap',
  'border',
  'border-radius',
  'width',
  'height',
];

/** What the jig tells the agent about a picked element. */
interface Picked {
  selector: string;
  tag: string;
  classes: string[];
  text: string;
  components?: string[];
  styles: Record<string, string>;
  ancestors: string[];
  url: string;
  story?: string;
}

// A CSS-module class, as Vite names it: `_chip_x9y8z` or, in dev, `_chip_x9y8z_12`.
// The stem, `_chip_`, stays the same when the hash or the line moves.
const MODULE_CLASS = /^(_.+_)[\w-]{5}(?:_\d+)?$/;

const stem = (name: string) => MODULE_CLASS.exec(name)?.[1];

const classPart = (name: string) => {
  const found = stem(name);
  return found ? `[class*="${found}"]` : `.${CSS.escape(name)}`;
};

const shape = (element: Element) => {
  const tag = element.localName;
  return tag + [...element.classList].map(classPart).join('');
};

// React's `useId` makes ids like `_r_2_` or `:r2:`. They move when the tree
// changes, so the selector does not stop at one.
const GENERATED_ID = /^(?:_r_|:r|«r)/;

const stableId = (element: Element) =>
  element.id && !GENERATED_ID.test(element.id) ? element.id : undefined;

/** One step of the selector path, counted among its siblings when it needs to be. */
function step(element: Element): string {
  const id = stableId(element);
  if (id) return `#${CSS.escape(id)}`;
  const part = shape(element);
  const parent = element.parentElement;
  if (!parent) return part;
  // Counted as the selector will match: a stem also matches a longer class.
  if (parent.querySelectorAll(`:scope > ${part}`).length === 1) return part;
  const sameTag = [...parent.children].filter((child) => child.localName === element.localName);
  return `${part}:nth-of-type(${sameTag.indexOf(element) + 1})`;
}

/** A short path to the element: up to an id, at most 4 levels, never past `<body>`. */
function selectorOf(element: Element): string {
  const steps: string[] = [];
  for (let at: Element | null = element; at && steps.length < 4; at = at.parentElement) {
    if (at === document.body || at === document.documentElement) break;
    steps.unshift(step(at));
    if (stableId(at)) break;
  }
  return steps.join(' > ');
}

/** The tag and class stems, as a reader would name the element. */
const label = (element: Element) =>
  element.localName +
  (stableId(element) ? `#${stableId(element)}` : '') +
  [...element.classList].map((name) => `.${stem(name) ?? name}`).join('');

/** A click inside an SVG icon picks the nearest HTML element around it. */
function pickable(target: Element): Element {
  let element = target;
  while (element instanceof SVGElement && element.parentElement) element = element.parentElement;
  return element;
}

interface Fiber {
  type?: unknown;
  return?: Fiber | null;
}

/** The names of the nearest React components, from the dev build's fiber. */
function componentsOf(element: Element): string[] | undefined {
  const key = Object.keys(element).find((name) => name.startsWith('__reactFiber$'));
  if (!key) return undefined;
  const names: string[] = [];
  let fiber = (element as unknown as Record<string, Fiber>)[key];
  for (; fiber && names.length < 3; fiber = fiber.return ?? undefined) {
    const type = fiber.type as
      | {
          displayName?: string;
          name?: string;
          render?: { name?: string };
          type?: { name?: string };
        }
      | undefined;
    if (!type || typeof type === 'string') continue;
    const name = type.displayName || type.name || type.render?.name || type.type?.name;
    if (name) names.push(name);
  }
  return names.length ? names : undefined;
}

function describeElement(element: Element): Picked {
  const computed = getComputedStyle(element);
  const styles: Record<string, string> = {};
  for (const name of PICKED_STYLES) {
    const value = computed.getPropertyValue(name);
    if (value) styles[name] = value;
  }
  const ancestors: string[] = [];
  for (let at = element.parentElement; at && ancestors.length < 3; at = at.parentElement) {
    if (at === document.documentElement) break;
    ancestors.push(label(at));
  }
  const picked: Picked = {
    selector: selectorOf(element),
    tag: element.localName,
    classes: [...element.classList],
    text: (element.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 80),
    styles,
    ancestors,
    url: location.href,
  };
  const components = componentsOf(element);
  if (components) picked.components = components;
  const story = new URLSearchParams(location.search).get('story');
  if (story) picked.story = story;
  return picked;
}

/**
 * Mount the jig on the page. It draws nothing until the provider shows it.
 * The promise holds the function that takes it off again.
 */
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
  // Vite appends a module's `<style>` when it loads or hot-updates, which would
  // put the app's rule after this one and win a tie of specificity.
  const observer = new MutationObserver(keepLast);

  const host = document.createElement('div');
  host.id = 'mael-jig';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = PANEL;

  const $ = <T extends Element>(selector: string) => root.querySelector(selector) as T;
  const panel = $<HTMLDivElement>('.panel');
  const pill = $<HTMLButtonElement>('[data-toggle]');
  const textarea = $<HTMLTextAreaElement>('textarea');
  const message = $<HTMLTextAreaElement>('[data-message]');
  const sendMonkeypatch = $<HTMLButtonElement>('[data-send-monkeypatch]');
  const sendMessage = $<HTMLButtonElement>('[data-send-message]');
  const status = $<HTMLSpanElement>('.status');
  const pickButton = $<HTMLButtonElement>('[data-pick]');
  const outline = $<HTMLDivElement>('.outline');
  const pickedRow = $<HTMLDivElement>('.picked');

  const show = (open: boolean) => {
    if (!open) endPick();
    panel.hidden = !open;
    pill.hidden = open;
  };
  pill.addEventListener('click', () => show(true));
  $<HTMLButtonElement>('[data-close]').addEventListener('click', () => show(false));

  // Hidden, the page carries nothing of the jig: no pill, no panel, no CSS.
  const attach = (visible: boolean) => {
    if (visible === host.isConnected) return;
    if (visible) {
      keepLast();
      observer.observe(document.head, { childList: true });
      document.body.append(host);
      show(true);
    } else {
      endPick();
      observer.disconnect();
      style.remove();
      host.remove();
    }
  };
  // A state event during the first load is newer than what the load reads.
  let heard = false;
  hot.on('mael-jig:state', ({ visible }) => {
    heard = true;
    attach(visible);
  });

  // The jig goes when the provider says it has, not before.
  $<HTMLButtonElement>('[data-hide]').addEventListener('click', async () => {
    try {
      const reply = await fetch('/__mael/hide', { method: 'POST' });
      if (!reply.ok) status.textContent = (await reply.json()).error?.message ?? 'Hide failed';
    } catch (error) {
      status.textContent = `Hide failed: ${String(error)}`;
    }
  });

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

  let picked: Picked | undefined;
  const showPicked = (element: Element | undefined) => {
    picked = element && describeElement(element);
    pickedRow.hidden = !picked;
    if (element && picked) {
      const code = pickedRow.querySelector('code')!;
      code.textContent = `${label(element)}${picked.text ? ` "${picked.text}"` : ''}`;
      code.title = picked.selector;
    }
  };
  $<HTMLButtonElement>('[data-clear-pick]').addEventListener('click', () => showPicked(undefined));

  // Capture on window, ahead of the app; see orchestrator-ui.md, "The jig".
  const ours = (target: EventTarget | null) => target instanceof Node && host.contains(target);
  const onMove = (event: MouseEvent) => {
    if (ours(event.target) || !(event.target instanceof Element)) {
      outline.hidden = true;
      return;
    }
    const box = pickable(event.target).getBoundingClientRect();
    Object.assign(outline.style, {
      top: `${box.top}px`,
      left: `${box.left}px`,
      width: `${box.width}px`,
      height: `${box.height}px`,
    });
    outline.hidden = false;
  };
  // The app hears no part of the press: menus, focus and drags start on the down.
  const onPress = (event: Event) => {
    if (ours(event.target) || !(event.target instanceof Element)) return;
    event.preventDefault();
    event.stopPropagation();
  };
  const onClick = (event: MouseEvent) => {
    if (ours(event.target) || !(event.target instanceof Element)) return;
    onPress(event);
    showPicked(pickable(event.target));
    endPick();
  };
  const onKey = (event: KeyboardEvent) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    endPick();
  };
  const PRESSES = ['pointerdown', 'mousedown', 'pointerup', 'mouseup'];
  function startPick() {
    pickButton.setAttribute('aria-pressed', 'true');
    window.addEventListener('mousemove', onMove, true);
    for (const type of PRESSES) window.addEventListener(type, onPress, true);
    window.addEventListener('click', onClick, true);
    window.addEventListener('keydown', onKey, true);
  }
  function endPick() {
    pickButton.setAttribute('aria-pressed', 'false');
    outline.hidden = true;
    window.removeEventListener('mousemove', onMove, true);
    for (const type of PRESSES) window.removeEventListener(type, onPress, true);
    window.removeEventListener('click', onClick, true);
    window.removeEventListener('keydown', onKey, true);
  }
  pickButton.addEventListener('click', () =>
    pickButton.getAttribute('aria-pressed') === 'true' ? endPick() : startPick(),
  );

  let sending = false;
  const settle = () => {
    sendMonkeypatch.disabled = sending;
    sendMessage.disabled = sending || !message.value.trim();
  };
  message.addEventListener('input', settle);
  settle();

  // Off while a send is in flight: a second click would post to every agent again.
  const send = async (feedback: Record<string, unknown>) => {
    sending = true;
    settle();
    status.textContent = 'Sending…';
    try {
      // The provider's message names the file, so it must hold what was typed.
      if (pending) await write();
      const reply = await fetch('/__mael/feedback', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(feedback),
      });
      const body = await reply.json();
      if (reply.ok) {
        const count = body.agentIds.length;
        status.textContent = `Sent to ${count} agent${count === 1 ? '' : 's'}`;
        message.value = '';
      } else {
        status.textContent = body.error?.message ?? `Send failed (${reply.status})`;
      }
    } catch (error) {
      status.textContent = `Send failed: ${String(error)}`;
    } finally {
      sending = false;
      settle();
    }
  };

  sendMonkeypatch.addEventListener('click', () =>
    send({ type: 'monkeypatch', css: textarea.value, note: message.value }),
  );
  sendMessage.addEventListener('click', () =>
    send({ type: 'instruction', text: message.value, ...(picked && { element: picked }) }),
  );

  // Read-only until the file loads, or the load would replace what the user typed.
  textarea.readOnly = true;
  const loaded = await (await fetch('/__mael/monkeypatch')).json();
  textarea.readOnly = false;
  textarea.value = loaded.css;
  apply(loaded.css);
  if (!heard) attach(loaded.visible);

  return () => {
    clearTimeout(pending);
    attach(false);
  };
}
