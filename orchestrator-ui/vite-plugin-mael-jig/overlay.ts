// The jig's overlay: the `Jig` pill and its panel. See docs/dev/orchestrator-ui.md,
// "The jig".
import { domToJpeg } from 'modern-screenshot';

/** A draft file changed: its name, its text, and the page that wrote it, if one did. */
interface Draft {
  name: string;
  text: string;
  from: string | null;
}

interface Hot {
  on(event: 'mael-jig:draft', callback: (data: Draft) => void): void;
  on(event: 'mael-jig:state', callback: (data: { visible: boolean }) => void): void;
}

interface Options {
  hot: Hot;
  fetch?: typeof fetch;
  debounceMs?: number;
  /** A JPEG data URL of what the user sees, or `null` for none. */
  capture?: () => Promise<string | null>;
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
[hidden] { display: none !important; }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid #4a4a55; }
.tabs button { border: none; border-radius: 6px 6px 0 0; background: none; color: #a0a0aa; }
.tabs button[aria-selected='true'] { background: #2b2b33; color: inherit; }
[role='tabpanel'] { display: flex; flex-direction: column; gap: 6px; }
.empty { color: #a0a0aa; }
.scope { display: flex; gap: 10px; border: none; margin: 0; padding: 0; }
.scope label { display: flex; gap: 4px; align-items: center; }
.controls { display: flex; flex-direction: column; gap: 4px; }
.control { display: grid; grid-template-columns: 110px 1fr 64px auto; gap: 6px;
  align-items: center; }
.control > label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.control > span { display: flex; gap: 4px; min-width: 0; }
.control input, .control select { min-width: 0; flex: 1; }
.control input[type='color'] { flex: none; width: 28px; padding: 0; }
.control output { color: #a0a0aa; overflow: hidden; text-overflow: ellipsis; }
.control button { padding: 0 6px; }
.control.muted { opacity: 0.5; }
.control small { grid-column: 1 / -1; color: #a0a0aa; }
.control small:empty { display: none; }
`;

const PANEL = `
<style>${STYLE}</style>
<button class="pill" data-toggle>Jig</button>
<div class="outline" hidden></div>
<div class="panel" hidden>
  <div class="row"><strong data-title>Jig</strong>
    <span><button data-pick aria-pressed="false">Pick</button>
      <button data-hide>Hide jig</button> <button data-close>Close</button></span></div>
  <div class="tabs" role="tablist">
    <button role="tab" data-tab="Controls" aria-selected="false">Controls</button>
    <button role="tab" data-tab="Monkeypatch" aria-selected="true">Monkeypatch</button></div>
  <div role="tabpanel" aria-label="Controls" hidden>
    <div class="controls"></div>
    <p class="empty">No control set. Pick an element, then press Ask for controls.</p></div>
  <div role="tabpanel" aria-label="Monkeypatch">
    <textarea spellcheck="false" aria-label="Monkeypatch" placeholder="Monkeypatch CSS"></textarea>
    <div class="row"><span></span><button data-send-monkeypatch>Send monkeypatch</button></div></div>
  <div class="picked" hidden><code></code>
    <button data-clear-pick aria-label="Clear the picked element">×</button></div>
  <fieldset class="scope" hidden>
    <label><input type="radio" name="scope" value="element" checked> This element</label>
    <label><input type="radio" name="scope" value="similar"> Elements like this</label>
    <label><input type="radio" name="scope" value="area"> This area</label></fieldset>
  <textarea data-message aria-label="Message to the agent"
    placeholder="Message to the agent"></textarea>
  <div class="row"><span class="status" role="status"></span>
    <span><button data-ask-controls hidden>Ask for controls</button>
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

const KINDS = ['range', 'number', 'color', 'select', 'text'] as const;

/** One CSS variable the user sets in the jig. */
interface Control {
  var: string;
  kind: (typeof KINDS)[number];
  label?: string;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  options?: string[];
  /** The source expression the variable starts at. */
  from?: string;
}

/** The controls an agent wrote to `.drafts/jig-controls.json`. */
interface ControlSet {
  title?: string;
  controls: Control[];
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** The control set in `text`, `null` for an empty file, or the fault that makes it invalid. */
function parseControls(text: string): ControlSet | string | null {
  if (!text.trim()) return null;
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return 'the file is not JSON';
  }
  if (!isObject(data) || !Array.isArray(data.controls)) return 'the file has no controls list';
  const seen = new Set<string>();
  for (const [index, control] of data.controls.entries()) {
    const n = index + 1;
    if (!isObject(control) || typeof control.var !== 'string' || !control.var.startsWith('--')) {
      return `control ${n} has no var`;
    }
    if (!KINDS.includes(control.kind as Control['kind'])) return `control ${n} has an unknown kind`;
    if (seen.has(control.var)) return `control ${n} repeats ${control.var}`;
    seen.add(control.var);
    const { options } = control;
    if (control.kind === 'select' && !(Array.isArray(options) && options.length)) {
      return `control ${n} has no options`;
    }
  }
  return data as unknown as ControlSet;
}

/** The values in `text`, by var: anything that is not a string value is left out. */
function parseValues(text: string): Record<string, string> {
  try {
    const data: unknown = JSON.parse(text);
    if (!isObject(data)) return {};
    return Object.fromEntries(
      Object.entries(data).filter(
        (entry): entry is [string, string] => typeof entry[1] === 'string',
      ),
    );
  } catch {
    return {};
  }
}

// A pseudo-class or pseudo-element: `:hover`, `::before`, `:nth-of-type(2)`. Each
// one is dropped before the match, so a match errs towards "it matches".
const PSEUDO = /::?[\w-]+(?:\([^)]*\))?/g;

/** Whether a style rule that reads `name` matches an element, or `undefined` for no rule. */
function wiringMatches(name: string, skip: CSSStyleSheet | null): boolean | undefined {
  const reads = new RegExp(`var\\(\\s*${name.replace(/[^\w-]/g, '\\$&')}\\s*[,)]`);
  const selectors: string[] = [];
  const walk = (rules: CSSRuleList) => {
    for (const rule of rules) {
      if (rule instanceof CSSStyleRule && reads.test(rule.style.cssText)) {
        selectors.push(rule.selectorText);
      }
      if ('cssRules' in rule) walk((rule as CSSGroupingRule).cssRules);
    }
  };
  for (const sheet of document.styleSheets) {
    if (sheet === skip) continue;
    try {
      walk(sheet.cssRules);
    } catch {
      // A cross-origin sheet does not let the page read its rules.
    }
  }
  if (!selectors.length) return undefined;
  return selectors.some((selector) => {
    try {
      return !!document.querySelector(selector.replace(PSEUDO, '') || '*');
    } catch {
      return true;
    }
  });
}

/**
 * The viewport as the user sees it, without the jig, as a JPEG data URL. At
 * scale 1 and quality 0.8 it stays well under the agent host's 5 MB cap.
 */
function captureViewport(host: Element): Promise<string> {
  const { innerWidth: width, innerHeight: height, scrollX, scrollY } = window;
  return domToJpeg(document.documentElement, {
    width,
    height,
    scale: 1,
    quality: 0.8,
    filter: (node) => node !== host,
    // The page is drawn whole, so it is moved up and left by the scroll.
    style: { transform: `translate(${-scrollX}px, ${-scrollY}px)`, transformOrigin: '0 0' },
  });
}

/**
 * Mount the jig on the page. It draws nothing until the provider shows it.
 * The promise holds the function that takes it off again.
 */
export async function mountJig({
  hot,
  fetch = window.fetch.bind(window),
  debounceMs = 300,
  capture,
}: Options): Promise<() => void> {
  // Not `crypto.randomUUID`: a page served over plain http on the tailnet is not
  // a secure context, and has none.
  const client = Math.random().toString(36).slice(2);

  const style = document.createElement('style');
  style.dataset.maelJig = '';
  // The controls go after the Monkeypatch: the `:root` rule sets what the wiring reads.
  const controlsStyle = document.createElement('style');
  controlsStyle.dataset.maelJig = 'controls';
  const keepLast = () => {
    const head = document.head;
    if (head.lastElementChild !== controlsStyle || controlsStyle.previousElementSibling !== style) {
      head.append(style, controlsStyle);
    }
  };
  // Vite appends a module's `<style>` when it loads or hot-updates, which would
  // put the app's rule after this one and win a tie of specificity.
  const observer = new MutationObserver(keepLast);

  const host = document.createElement('div');
  host.id = 'mael-jig';
  const shoot = capture ?? (() => captureViewport(host));
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
  const scopes = $<HTMLFieldSetElement>('.scope');
  const askControls = $<HTMLButtonElement>('[data-ask-controls]');

  const tabs = [...root.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
  const selectTab = (name: string) => {
    for (const tab of tabs) tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
    for (const pane of root.querySelectorAll<HTMLElement>('[role="tabpanel"]')) {
      pane.hidden = pane.getAttribute('aria-label') !== name;
    }
  };
  for (const tab of tabs) tab.addEventListener('click', () => selectTab(tab.dataset.tab!));

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
      controlsStyle.remove();
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

  /** A debounced write of one draft file, with the text `text` gives when it runs. */
  const writer = (name: string, text: () => string) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const flush = () => {
      clearTimeout(timer);
      timer = undefined;
      return fetch(`/__mael/drafts/${name}`, {
        method: 'PUT',
        headers: { 'x-mael-jig-client': client },
        body: text(),
      });
    };
    return {
      flush,
      schedule: () => {
        clearTimeout(timer);
        timer = setTimeout(flush, debounceMs);
      },
      cancel: () => clearTimeout(timer),
      pending: () => timer !== undefined,
    };
  };

  const monkeypatch = writer('monkeypatch.css', () => textarea.value);
  textarea.addEventListener('input', () => {
    apply(textarea.value);
    monkeypatch.schedule();
  });

  const controlsPane = $<HTMLDivElement>('.controls');
  const controlsNote = $<HTMLParagraphElement>('.empty');
  const title = $<HTMLElement>('[data-title]');
  let controlSet: ControlSet | null = null;
  /** The value the user set on each control, by var. */
  let values: Record<string, string> = {};
  /** Where each control starts: its `from`, or the value the page gave it at load. */
  let starts: Record<string, string> = {};
  /** Redraws each row's inputs and value, but the input the user is typing in. */
  const syncs: ((except?: Element) => void)[] = [];

  const known = () =>
    Object.fromEntries(
      (controlSet?.controls ?? [])
        .filter((control) => control.var in values)
        .map((control) => [control.var, values[control.var]!]),
    );
  const valuesFile = writer('jig-values.json', () => JSON.stringify(known(), null, 2));

  const applyControls = () => {
    const declarations = (controlSet?.controls ?? []).flatMap((control) => {
      const value = values[control.var] ?? control.from;
      return value ? [`  ${control.var}: ${value};`] : [];
    });
    controlsStyle.textContent = declarations.length ? `:root {\n${declarations.join('\n')}\n}` : '';
  };
  const current = (control: Control) => values[control.var] ?? starts[control.var] ?? '';
  const rootValue = (name: string) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const markMatches = () => {
    for (const row of controlsPane.querySelectorAll<HTMLElement>('.control')) {
      const muted = wiringMatches(row.dataset.var!, controlsStyle.sheet) === false;
      row.classList.toggle('muted', muted);
      row.querySelector('small')!.textContent = muted ? 'no match on this page' : '';
    }
  };

  const setValue = (control: Control, value: string | undefined, source?: Element) => {
    if (value === undefined) delete values[control.var];
    else values[control.var] = value;
    applyControls();
    for (const sync of syncs) sync(source);
    valuesFile.schedule();
    settle();
  };

  const drawControl = (control: Control) => {
    const name = control.label ?? control.var;
    const row = document.createElement('div');
    row.className = 'control';
    row.dataset.var = control.var;
    const label = document.createElement('label');
    label.textContent = name;
    label.title = control.var;
    const inputs = document.createElement('span');
    const output = document.createElement('output');
    const reset = document.createElement('button');
    reset.textContent = '×';
    reset.setAttribute('aria-label', `Reset ${name}`);
    reset.addEventListener('click', () => setValue(control, undefined));
    const note = document.createElement('small');
    row.append(label, inputs, output, reset, note);

    const unit = control.unit ?? '';
    const make = (tag: 'input' | 'select', type?: string) => {
      const input = document.createElement(tag);
      if (input instanceof HTMLInputElement && type) input.type = type;
      input.setAttribute('aria-label', name);
      inputs.append(input);
      return input;
    };
    let sync: (except?: Element) => void;
    if (control.kind === 'range' || control.kind === 'number') {
      const input = make('input', control.kind) as HTMLInputElement;
      for (const key of ['min', 'max', 'step'] as const) {
        if (control[key] !== undefined) input[key] = String(control[key]);
      }
      input.addEventListener('input', () => setValue(control, `${input.value}${unit}`, input));
      sync = (except) => {
        // A `from` such as `var(--space-2)` has no number: the page has resolved it.
        const number = [current(control), rootValue(control.var)]
          .map(parseFloat)
          .find(Number.isFinite);
        if (except !== input && number !== undefined) input.value = String(number);
      };
    } else if (control.kind === 'color') {
      const picker = make('input', 'color') as HTMLInputElement;
      const text = make('input', 'text') as HTMLInputElement;
      for (const input of [picker, text]) {
        input.addEventListener('input', () => setValue(control, input.value, input));
      }
      sync = (except) => {
        const value = current(control);
        if (except !== picker && /^#[\da-f]{6}$/i.test(value)) picker.value = value;
        if (except !== text) text.value = value;
      };
    } else if (control.kind === 'select') {
      const select = make('select') as HTMLSelectElement;
      for (const option of control.options ?? []) select.add(new Option(option));
      select.addEventListener('input', () => setValue(control, select.value, select));
      sync = () => {
        select.value = current(control);
      };
    } else {
      const input = make('input', 'text') as HTMLInputElement;
      input.addEventListener('input', () => setValue(control, input.value, input));
      sync = (except) => {
        if (except !== input) input.value = current(control);
      };
    }
    syncs.push((except) => {
      sync(except);
      output.textContent = current(control);
    });
    return row;
  };

  const drawControls = (text: string) => {
    const parsed = parseControls(text);
    controlSet = typeof parsed === 'string' ? null : parsed;
    title.textContent = controlSet?.title ?? 'Jig';
    controlsNote.hidden = !!controlSet;
    controlsNote.textContent =
      typeof parsed === 'string'
        ? `The control set is not valid: ${parsed}.`
        : 'No control set. Pick an element, then press Ask for controls.';
    // A control with no `from` starts where the page has it, without the jig's override.
    controlsStyle.textContent = '';
    starts = Object.fromEntries(
      (controlSet?.controls ?? []).map((control) => [
        control.var,
        control.from ?? rootValue(control.var),
      ]),
    );
    applyControls();
    syncs.length = 0;
    controlsPane.replaceChildren(...(controlSet?.controls ?? []).map(drawControl));
    for (const sync of syncs) sync();
    markMatches();
    if (parsed !== null) selectTab('Controls');
    settle();
  };

  hot.on('mael-jig:draft', ({ name, text, from }) => {
    if (from !== client) {
      if (name === 'monkeypatch.css') {
        textarea.value = text;
        apply(text);
      } else if (name === 'jig-controls.json') {
        drawControls(text);
      } else if (name === 'jig-values.json') {
        values = parseValues(text);
        applyControls();
        for (const sync of syncs) sync();
      }
    }
    markMatches();
  });

  let picked: Picked | undefined;
  const showPicked = (element: Element | undefined) => {
    picked = element && describeElement(element);
    pickedRow.hidden = scopes.hidden = askControls.hidden = !picked;
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
    askControls.disabled = sending;
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
      if (monkeypatch.pending()) await monkeypatch.flush();
      // A screenshot is a help, not a need: a failed one sends the words alone.
      const screenshot = await shoot().catch(() => null);
      const reply = await fetch('/__mael/feedback', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(screenshot ? { ...feedback, screenshot } : feedback),
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
  askControls.addEventListener('click', () => {
    const scope = scopes.querySelector<HTMLInputElement>('input:checked')!.value;
    void send({ type: 'pick', scope, element: picked, note: message.value });
  });

  // Read-only until the file loads, or the load would replace what the user typed.
  textarea.readOnly = true;
  const draft = (name: string) => fetch(`/__mael/drafts/${name}`).then((reply) => reply.text());
  const [css, controlsText, valuesText, state] = await Promise.all([
    draft('monkeypatch.css'),
    draft('jig-controls.json'),
    draft('jig-values.json'),
    fetch('/__mael/state').then((reply) => reply.json()),
  ]);
  textarea.readOnly = false;
  textarea.value = css;
  apply(css);
  values = parseValues(valuesText);
  if (!heard) attach(state.visible);
  drawControls(controlsText);

  return () => {
    monkeypatch.cancel();
    valuesFile.cancel();
    attach(false);
  };
}
