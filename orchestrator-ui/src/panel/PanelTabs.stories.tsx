import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { ApiProvider } from '../api/ApiProvider';
import { MemoryNav } from '../nav/MemoryNav';
import { useAppStore } from '../store/store';
import { createFakeServer } from '../fake/fakeServer';
import { PanelTabs } from './PanelTabs';
import { WorktreeBar } from './WorktreeBar';
import { usePanelGroups } from './usePanelGroups';
import {
  type Strip,
  freeAgent,
  fourPhases,
  picker,
  pickerOneTask,
  pickerTwoTasks,
  gone,
  longTitle,
  realWidths,
  urlOf,
  sessionAndPlan,
  single,
  world,
} from './panelTabs.fixture';

export default { title: 'Panel / Tabs' };

/**
 * The strip above the panel body, drawn by the real component. What the suite
 * cannot answer is in `panelTabs.fixture.ts`; check both schemes.
 *
 * The strip is one tab stop: Tab into it, then arrow between tabs. That is
 * where the ring and the close button's reachability are checked.
 */

/** The suite's own fake server, not a stub — see `TaskNode.stories.tsx`. */
const { api } = createFakeServer({ world });

const newClient = () =>
  new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
const queryClient = newClient();

/**
 * The strip over the ground it heads, at the width the story is asked for.
 *
 * `PanelTabs` reads the app's one store, so a story sets that store's tabs
 * before it draws — which is why one story draws one strip. The strip shows
 * the tabs of one worktree group. Ladle's width control drives the rest.
 */
function Strip({
  strip,
  width,
  serverApi = api,
  bar = false,
}: {
  strip: Strip;
  width?: number;
  /** A server of another world, for a story the shared one cannot draw. */
  serverApi?: typeof api;
  /** Draw the worktree bar above the strip, as `Panel` does. */
  bar?: boolean;
}) {
  // A world of its own needs a cache of its own: the queries share their keys.
  const [client] = useState(() => (serverApi === api ? queryClient : newClient()));
  useState(() => {
    useAppStore.setState((s) => ({
      ui: { ...s.ui, tabs: strip.tabs, tabRecency: [] },
    }));
  });
  return (
    <ApiProvider api={serverApi} queryClient={client}>
      <MemoryNav url={urlOf(strip)}>
        <div
          style={{
            fontFamily: 'var(--font)',
            background: 'var(--bg)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            overflow: 'hidden',
            width: width ? `${width}px` : '100%',
            maxWidth: '100%',
          }}
        >
          <GroupTabs bar={bar} />
          {/*
          The shape `Panel.tsx` builds: a scrolling box holding a status row
          and the reading under it. Keep the nesting, or the story stops
          matching the panel it stands for.
        */}
          <div style={{ overflow: 'auto' }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: 'var(--u)',
                padding: '6px 12px',
                borderBottom: '1px solid var(--border)',
                color: 'var(--fg-muted)',
                fontFamily: 'var(--mono)',
                fontSize: 'var(--text-sm)',
              }}
            >
              <span>The status row. The tab in view opens onto it.</span>
              <span>34k ctx</span>
            </div>
            <div
              style={{
                padding: 'var(--u-3)',
                color: 'var(--fg-faint)',
                fontSize: 'var(--text-sm)',
              }}
            >
              The conversation body.
            </div>
          </div>
        </div>
      </MemoryNav>
    </ApiProvider>
  );
}

/** The strip as `Panel` feeds it: the active tab's group. Under the provider, which the hook reads. */
function GroupTabs({ bar }: { bar: boolean }) {
  const { activeGroup, close } = usePanelGroups();
  return (
    <>
      {bar && <WorktreeBar group={activeGroup} />}
      <PanelTabs group={activeGroup} onClose={close} />
    </>
  );
}

/** Press the **Tab picker**'s `+` once the world has drawn it, so the story opens on the menu. */
function useOpenPicker() {
  useEffect(() => {
    const timer = setInterval(() => {
      const plus = document.querySelector<HTMLButtonElement>('[aria-label="Open a tab"]');
      if (!plus) return;
      clearInterval(timer);
      plus.click();
    }, 100);
    return () => clearInterval(timer);
  }, []);
}

const oneTaskServer = createFakeServer({ world: pickerOneTask });
const twoTasksServer = createFakeServer({ world: pickerTwoTasks });

/**
 * The **Tab picker** open, for a worktree of one task: the bar names the task,
 * and the menu rows carry no prefix. The open tabs carry the check. Check the
 * `+` sits just past the last tab, and the long title's ellipsis in the bar.
 */
export const PickerOneTask: Story = () => {
  useOpenPicker();
  return <Strip strip={picker} serverApi={oneTaskServer.api} bar />;
};

/** The picker for two tasks: the bar reads `2 tasks`, and each row names its task. */
export const PickerTwoTasks: Story = () => {
  useOpenPicker();
  return <Strip strip={picker} serverApi={twoTasksServer.api} bar />;
};

/** The picker in 320px: the `+` stays in view as the tabs scroll. */
export const PickerNarrow: Story = () => (
  <Strip strip={picker} serverApi={oneTaskServer.api} bar width={320} />
);

/** One tab. Nothing to rank against, so the active state has to stand alone. */
export const OneTab: Story = () => <Strip strip={single} />;

/**
 * Four tabs, one per phase. This is where the edge is read: only the tab in
 * view draws one, so arrow along the strip to see each phase in turn.
 */
export const FourPhases: Story = () => <Strip strip={fourPhases} />;

/** The pair the naming rule exists for: a session and its own plan, read together. */
export const SessionAndPlan: Story = () => <Strip strip={sessionAndPlan} />;

/** A free agent beside a task's. Its own id fills the id slot, and it draws no phase. */
export const FreeAgent: Story = () => <Strip strip={freeAgent} />;

/** A tab whose entity has left the world: no phase, no title, and still drawn. */
export const EntityGone: Story = () => <Strip strip={gone} />;

/** A long label. The id holds — Mono Means Literal — and the label takes the ellipsis. */
export const LongLabel: Story = () => <Strip strip={longTitle} />;

/** Four tabs in 320px: the strip's share of a panel at its minimum, beside the sidebar. */
export const NarrowPanel: Story = () => <Strip strip={fourPhases} width={320} />;

/**
 * Ids at the length the notebook really writes them. See `panelTabs.fixture.ts`.
 *
 * This is where the close control is judged: hover an inactive tab's cross and
 * watch what it covers. The id does not ellipsise under it — a known conflict
 * with the Mono Means Literal Rule, recorded rather than settled.
 */
export const RealWidths: Story = () => <Strip strip={realWidths} />;
