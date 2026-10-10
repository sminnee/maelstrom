import type { Story } from '@ladle/react';
import { useState, type CSSProperties, type ReactNode } from 'react';
import cardStyles from '../canvas/NodeCard.module.css';
import { makeTask, makeWorktree } from '../fake/fixtures';
import tabStyles from '../panel/PanelTabs.module.css';
import { TASK_STATUSES, type TaskStatus } from '../protocol/entities';
import chipStyles from '../shell/AttentionChip.module.css';
import { ExternalLink } from '../shell/ExternalLink';
import { OffDeskIcon } from '../shell/OffDeskIcon';
import { PanelLink } from '../shell/PanelLink';
import { worktreePr } from '../selectors/cardPr';
import { PrChip } from '../shell/PrChip';
import barStyles from '../shell/TopBar.module.css';
import { actionIcon } from './actionIcons';
import { AppButton } from './AppButton';
import { ConfirmButton } from './ConfirmButton';
import { DialogFooter } from './Dialog';
import { MultiSelect } from './MultiSelect';
import { SplitButton } from './SplitButton';
import { SplitChip } from './SplitChip';
import { StatusPicker } from './StatusPicker';

export default { title: 'UI / Controls' };

/**
 * Each control of the chrome, in the combinations the app draws them in.
 *
 * What these stories answer: whether one row of mixed controls shares one
 * height and one baseline, on the desktop and at the narrow height. A control here takes its height from `--control` and from nothing
 * else, so a column re-points that one token.
 *
 * The top bar's nav, the panel tab and the attention chip read the store or
 * the world, so the board draws their markup with their own style modules.
 */
const later = () => new Promise<void>((resolve) => setTimeout(resolve, 1200));

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: 'grid', gap: 'var(--u)' }}>
      <span
        style={{
          fontSize: 'var(--text-sm)',
          letterSpacing: 'var(--tracking-micro)',
          textTransform: 'uppercase',
          color: 'var(--fg-faint)',
        }}
      >
        {label}
      </span>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--u)', alignItems: 'center' }}>
        {children}
      </div>
    </div>
  );
}

/** The task list's status filter, holding its own pick. */
function StatusFilter() {
  const [value, setValue] = useState<TaskStatus[]>(['todo', 'in-progress', 'blocked']);
  return (
    <MultiSelect
      label="Status"
      options={TASK_STATUSES.map((s) => ({ value: s, label: s }))}
      value={value}
      onChange={setValue}
    />
  );
}

function Board({ control, heading }: { control: string; heading: string }) {
  const [asking, setAsking] = useState(false);
  const [picking, setPicking] = useState(false);
  const [status, setStatus] = useState(makeTask({ status: 'in-progress' }));
  const worktree = makeWorktree({
    prNumber: 118,
    prCommits: 4,
    prUrl: 'https://github.com/acme/northwind/pull/118',
    prState: 'ci-running',
  });
  return (
    <section
      style={
        {
          '--control': control,
          display: 'grid',
          gap: 'var(--u-3)',
          alignContent: 'start',
          padding: 'var(--u-2)',
          minWidth: 0,
          flex: 1,
        } as CSSProperties
      }
    >
      <h2 style={{ margin: 0, fontSize: 'var(--text-ui)' }}>{heading}</h2>
      <Row label="AppButton — the four variants">
        <AppButton>Stop</AppButton>
        <AppButton variant="primary" onClick={later} processingChildren="Launching">
          Launch
        </AppButton>
        <AppButton variant="quiet">Edit task</AppButton>
        <AppButton variant="link">Show more</AppButton>
        <AppButton disabled>Resume</AppButton>
      </Row>
      <Row label="AppButton with an icon">
        <AppButton icon={actionIcon('stop')}>Stop</AppButton>
        <AppButton icon={actionIcon('compact')}>Compact</AppButton>
        <AppButton
          variant="primary"
          icon={actionIcon('launch')}
          onClick={later}
          processingChildren="Launching"
        >
          Launch
        </AppButton>
        <AppButton icon={actionIcon('approve')} onClick={() => Promise.reject(new Error('no'))}>
          Approve
        </AppButton>
      </Row>
      <Row label="SplitButton with an icon, and a failure">
        <SplitButton
          options={[
            { label: 'Sync', icon: actionIcon('sync'), run: later },
            { label: 'Squash', icon: actionIcon('merge'), run: later },
          ]}
        />
        <AppButton icon={actionIcon('terminate')} onClick={() => Promise.reject(new Error('no'))}>
          Terminate
        </AppButton>
      </Row>
      <Row label="SplitButton and ConfirmButton">
        <SplitButton
          options={[
            { label: 'Sync', run: later },
            { label: 'Sync and push', run: later },
            { label: 'Squash', run: later },
          ]}
        />
        <SplitButton variant="primary" options={[{ label: 'Approve', run: later }]} />
        {/* One option with a confirm: the main segment anchors the question. */}
        <SplitButton
          variant="quiet"
          options={[
            {
              label: 'Delete',
              icon: actionIcon('removeWorktree'),
              confirm: {
                question: 'Delete charlie? The checkout goes; the branch stays.',
                confirm: 'Delete it',
              },
              run: later,
            },
          ]}
        />
        <ConfirmButton
          variant="quiet"
          question="Close this worktree?"
          confirm="Close it"
          asking={asking}
          onAsk={() => setAsking(true)}
          onDismiss={() => setAsking(false)}
          onConfirm={() => setAsking(false)}
        >
          Close
        </ConfirmButton>
      </Row>
      <Row label="The node card's links, chips and status">
        <StatusPicker
          task={status}
          picking={picking}
          onPick={() => setPicking(true)}
          onDone={() => setPicking(false)}
          onChange={(next) => {
            setStatus({ ...status, status: next });
            setPicking(false);
          }}
          label="Status"
        />
        <PanelLink tab={{ key: 'session:a', kind: 'session', agentId: 'a' }}>Session</PanelLink>
        <PanelLink tab={{ key: 'changes:w', kind: 'changes', worktreeId: 'w' }}>Changes</PanelLink>
        <ExternalLink href="https://example.org">cmux</ExternalLink>
        <PrChip pr={worktreePr(worktree)} size="large" />
        <PrChip pr={worktreePr(worktree)} />
        <SplitChip label="agents" title="4 of 8 agents are working">
          4/8
        </SplitChip>
        <SplitChip label="5h" tone="busy" title="62% of the five-hour window">
          62%
        </SplitChip>
      </Row>
      <Row label="The node card's command row">
        <div className={cardStyles.commands} style={{ alignSelf: 'auto' }}>
          <AppButton variant="primary" icon={actionIcon('launch')}>
            Launch
          </AppButton>
          <AppButton variant="quiet" icon={actionIcon('edit')}>
            Edit task
          </AppButton>
          <AppButton variant="quiet" icon={actionIcon('resume')}>
            Resume
          </AppButton>
          <SplitButton
            variant="quiet"
            options={[
              { label: 'Terminate', icon: actionIcon('terminate'), run: later },
              {
                label: '…and take off desk',
                isDefault: true,
                buttonLabel: 'Dismiss',
                run: later,
              },
            ]}
          />
          <SplitButton
            variant="quiet"
            options={[
              { label: 'Sync', icon: actionIcon('sync'), run: later },
              { label: 'Sync & squash', icon: actionIcon('sync'), run: later },
            ]}
          />
        </div>
      </Row>
      <Row label="The top bar">
        <div className={barStyles.views} role="group" aria-label="Views">
          {['Desk', 'Tasks', 'Worktrees'].map((label, i) => (
            <button key={label} type="button" className={barStyles.view} aria-pressed={i === 0}>
              {label}
            </button>
          ))}
        </div>
        <StatusFilter />
        <AppButton className={chipStyles.chip} data-count={2}>
          <span className={chipStyles.asks}>⚠ 2</span>
          <span className={chipStyles.unanswered}>1</span>
        </AppButton>
        <AppButton variant="primary" icon={actionIcon('new')}>
          New
        </AppButton>
      </Row>
      <Row label="A panel tab and a field">
        <div className={tabStyles.strip} role="tablist" style={{ flex: 'none' }}>
          <div role="tab" className={`${tabStyles.tab} nowrap`} data-active aria-selected>
            <span className={`${tabStyles.label} truncate`}>NORT-7 Session</span>
          </div>
          <div role="tab" className={`${tabStyles.tab} nowrap`} aria-selected={false}>
            <span className={`${tabStyles.label} truncate`}>NORT-7 Plan</span>
          </div>
        </div>
        <input type="text" defaultValue="feat/orders" aria-label="Branch" />
        <select aria-label="Group by" defaultValue="project">
          <option value="project">Project</option>
          <option value="worktree">Worktree</option>
        </select>
      </Row>
      <Row label="A dialog footer">
        <div style={{ flex: 1, border: '1px solid var(--border)', borderRadius: 'var(--radius)' }}>
          <DialogFooter
            aside={
              <>
                <AppButton variant="link">Clear</AppButton>
                <AppButton variant="link">Cancel</AppButton>
              </>
            }
          >
            <AppButton icon={actionIcon('save')}>Save</AppButton>
            <AppButton variant="primary" icon={actionIcon('start')}>
              Start
            </AppButton>
          </DialogFooter>
        </div>
      </Row>
    </section>
  );
}

/** The board at the desktop height. What to look at: each row holds one height. */
export const Desktop: Story = () => (
  <Board control="calc(var(--u) * 4)" heading="--control: 4 units, 32px" />
);

/**
 * The board at the narrow height. What to look at, at 390px: each control is
 * 48px high, and a row wraps and does not overflow. A button with an icon
 * takes its narrow shape — the icon over a caption — from the viewport, not
 * from this board, so open the story in a viewport under 840px to see it.
 */
export const Narrow: Story = () => (
  <div style={{ maxWidth: 390 }}>
    <Board control="calc(var(--u) * 6)" heading="--control: 6 units, 48px" />
  </div>
);

const terminateAt = (corner: CSSProperties) => (
  <div style={{ position: 'fixed', ...corner }}>
    <SplitButton
      options={[
        { label: 'Terminate', icon: actionIcon('terminate'), run: later },
        {
          label: '…and take off desk',
          icon: <OffDeskIcon />,
          isDefault: true,
          buttonLabel: 'Dismiss',
          run: later,
        },
        {
          label: '…and close charlie',
          icon: <OffDeskIcon />,
          disabled: true,
          detail: '2 other agents still running in charlie',
          run: later,
        },
        {
          label: '…shelving the branch',
          icon: <OffDeskIcon />,
          disabled: true,
          detail: '2 other agents still running in charlie',
          run: later,
        },
        {
          label: '…or trashing the branch',
          icon: <OffDeskIcon />,
          disabled: true,
          detail: '2 other agents still running in charlie',
          run: later,
        },
        {
          label: '…or ignoring the branch',
          icon: <OffDeskIcon />,
          disabled: true,
          detail: '2 other agents still running in charlie',
          run: later,
        },
      ]}
    />
  </div>
);

/**
 * A split button in each corner of the screen, as the detail screen's command
 * bar draws Dismiss. What to look at, at 390px: each menu opens toward the
 * free space and stays on the screen.
 */
export const MenuAtTheEdges: Story = () => (
  <>
    {terminateAt({ top: 'var(--u)', left: 'var(--u)' })}
    {terminateAt({ top: 'var(--u)', right: 'var(--u)' })}
    {terminateAt({ bottom: 'var(--u)', left: 'var(--u)' })}
    {terminateAt({ bottom: 'var(--u)', right: 'var(--u)' })}
  </>
);
