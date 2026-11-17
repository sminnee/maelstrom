import { describe, expect, it } from 'vitest';
import { qualifiedTaskLabel, taskLabel, taskLabelTitle } from './taskLabel';
import { makeTask } from '../fake/fixtures';

const open = makeTask({
  id: 'northwind/k3f9',
  notebookId: 'k3f9',
  project: 'northwind',
  codename: 'falcon',
});
const closed = makeTask({
  id: 'northwind/k3f9',
  notebookId: 'k3f9',
  project: 'northwind',
  codename: null,
});

describe('taskLabel', () => {
  it('names a task by its codename, else by its bare id', () => {
    expect(taskLabel(open)).toBe('falcon');
    expect(taskLabel(closed)).toBe('k3f9');
  });

  it('puts the project in front for a place that does not name it', () => {
    expect(qualifiedTaskLabel(open)).toBe('northwind/falcon');
    expect(qualifiedTaskLabel(closed)).toBe('northwind/k3f9');
  });

  it('names the id in the tooltip beside a codename', () => {
    expect(taskLabelTitle(open)).toBe('falcon · k3f9');
    expect(taskLabelTitle(closed)).toBe('k3f9');
  });
});
