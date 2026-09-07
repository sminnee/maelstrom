import type { Story } from '@ladle/react';
import { useState } from 'react';
import { ComboBox, type ComboOption } from './ComboBox';
import dialog from './Dialog.module.css';

export default { title: 'UI / ComboBox' };

/**
 * The control is controlled and lives in a dialog field, so every story draws
 * it in one — `Dialog.module.css`'s `.field` rule is what dresses the input.
 * The chosen value is shown below, since that is what the form submits.
 */
function Field({
  label,
  options,
  initial = '',
  placeholder,
}: {
  label: string;
  options: ComboOption[];
  initial?: string;
  placeholder?: string;
}) {
  const [value, setValue] = useState(initial);
  return (
    <div style={{ width: 420, padding: 20, background: 'var(--bg-raised)' }}>
      <label className={dialog.field}>
        <span>{label}</span>
        <ComboBox value={value} options={options} onChange={setValue} placeholder={placeholder} />
      </label>
      <p style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
        Value: <code>{value || '(empty)'}</code>
      </p>
    </div>
  );
}

const BRANCHES: ComboOption[] = [
  { value: 'feat/orders' },
  { value: 'feat/logs' },
  { value: 'fix/export-drops-a-row' },
  { value: 'chore/bump-deps' },
];

const ISSUES: ComboOption[] = [
  { value: 'MAEL-101', label: 'Add a Linear kind to the new panel' },
  { value: 'MAEL-102', label: 'Read a project’s bases off the event loop' },
  { value: 'MAEL-103', label: 'Drop the per-row project context' },
  { value: 'MAEL-104', label: 'Leave one public async command runner' },
];

/** Bare values, as the Branch and Command fields offer them. */
export const Values: Story = () => <Field label="Branch" options={BRANCHES} />;

/** With labels: the words are what you search by, the id is what you submit. */
export const ValuesWithLabels: Story = () => (
  <Field label="Linear issue" options={ISSUES} placeholder="Choose an issue" />
);

/** A list long enough to scroll rather than run off the dialog. */
export const LongList: Story = () => (
  <Field
    label="Branch"
    options={Array.from({ length: 40 }, (_, i) => ({ value: `feat/branch-${i + 1}` }))}
  />
);

/** Free text that matches nothing: the offer closes rather than showing an empty box. */
export const FreeText: Story = () => (
  <Field label="Branch" options={BRANCHES} initial="feat/something-new" />
);

/** Nothing to offer — the field still takes a typed value. */
export const NoOptions: Story = () => <Field label="Branch" options={[]} />;
