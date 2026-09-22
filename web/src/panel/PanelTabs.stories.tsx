import type { Story } from '@ladle/react';
import { QueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ApiProvider } from '../api/ApiProvider';
import { useAppStore } from '../store/store';
import { createFakeServer } from '../test/fakeServer';
import { PanelTabs } from './PanelTabs';
import {
  type Strip,
  freeAgent,
  fourPhases,
  gone,
  longTitle,
  realWidths,
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

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: Infinity } },
});

/**
 * The strip over the ground it heads, at the width the story is asked for.
 *
 * `PanelTabs` reads the app's one store, so a story sets that store's tabs
 * before it draws — which is why one story draws one strip. Ladle's width
 * control drives the rest: drag it to 320px for the panel's minimum.
 */
function Strip({ strip, width }: { strip: Strip; width?: number }) {
  useState(() => {
    useAppStore.setState((s) => ({
      ui: { ...s.ui, tabs: strip.tabs, activeTabKey: strip.activeTabKey },
    }));
  });
  return (
    <ApiProvider api={api} queryClient={queryClient}>
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
        <PanelTabs />
        <div
          style={{
            padding: 'var(--space-5)',
            color: 'var(--fg-faint)',
            fontSize: 'var(--text-sm)',
          }}
        >
          The panel body. The tab in view takes this ground.
        </div>
      </div>
    </ApiProvider>
  );
}

/** One tab. Nothing to rank against, so the active state has to stand alone. */
export const OneTab: Story = () => <Strip strip={single} />;

/**
 * Four tabs, one per phase. This is where the edge is read: whether 2px names
 * a phase, and whether the drained edges of the three inactive tabs still do.
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

/** Four tabs at the panel's 320px minimum: the width the truncation is for. */
export const NarrowPanel: Story = () => <Strip strip={fourPhases} width={320} />;

/** Ids at the length the notebook really writes them. See `panelTabs.fixture.ts`. */
export const RealWidths: Story = () => <Strip strip={realWidths} />;
