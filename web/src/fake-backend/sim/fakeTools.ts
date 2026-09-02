import type { Rng } from './rng';
import { pick } from './rng';

/** Canned file contents, edits and command outputs the scripts draw from. */

export const FILES: Record<string, string> = {
  'app/models/order.py':
    'from sqlalchemy import Column, Integer, String\n\nclass Order(Base):\n    __tablename__ = "orders"\n    id = Column(Integer, primary_key=True)\n    status = Column(String, nullable=False)\n',
  'app/exports/orders.py':
    'import csv\n\ndef export_orders(orders):\n    for order in orders:\n        yield [order.id, order.status]\n',
  'tests/test_orders.py':
    'def test_export_has_header(client):\n    body = client.get("/orders/export").text\n    assert body.splitlines()[0] == "id,status"\n',
  'src/maelstrom/task_index.py': 'def is_stale(index, head):\n    return index.head != head\n',
  'tests/test_task_index.py':
    'def test_restamp_after_head_moves(tmp_path):\n    index = build(tmp_path)\n    assert not is_stale(index, index.head)\n',
  'migrations/0042_pg16.sql': 'ALTER DATABASE northwind SET default_collation = "und-x-icu";\n',
  '.github/workflows/test.yml': 'jobs:\n  test:\n    runs-on: ubuntu-latest\n',
};

export const EDITS: { path: string; old: string; new: string }[] = [
  {
    path: 'app/exports/orders.py',
    old: '    for order in orders:\n        yield [order.id, order.status]',
    new: '    yield ["id", "status"]\n    for order in orders:\n        yield [order.id, order.status]',
  },
  {
    path: 'src/maelstrom/task_index.py',
    old: '    return index.head != head',
    new: '    if index.head is None:\n        return True\n    return index.head != head',
  },
  {
    path: 'migrations/0042_pg16.sql',
    old: 'ALTER DATABASE northwind SET default_collation = "und-x-icu";',
    new: 'ALTER DATABASE northwind SET default_collation = "und-x-icu";\nREINDEX DATABASE northwind;',
  },
  {
    path: '.github/workflows/test.yml',
    old: '    runs-on: ubuntu-latest',
    new: '    runs-on: ubuntu-latest\n    timeout-minutes: 20',
  },
];

export function testRun(
  passing: boolean,
  rng: Rng,
): { command: string; output: string; exitCode: number } {
  const total = 40 + Math.floor(rng() * 60);
  if (passing) {
    return {
      command: "uv run pytest -m 'not slow' -q",
      output: `${'.'.repeat(total)}\n${total} passed in ${(2 + rng() * 6).toFixed(2)}s\n`,
      exitCode: 0,
    };
  }
  const failing = pick(rng, [
    'test_export_has_header',
    'test_restamp_after_head_moves',
    'test_migration_applies',
  ]);
  return {
    command: "uv run pytest -m 'not slow' -q",
    output: `${'.'.repeat(total - 1)}F\nFAILED tests/${failing}.py::${failing} - AssertionError\n${total - 1} passed, 1 failed in ${(2 + rng() * 6).toFixed(2)}s\n`,
    exitCode: 1,
  };
}

export function ciRun(
  passing: boolean,
  rng: Rng,
): { command: string; output: string; exitCode: number } {
  const run = 4210000 + Math.floor(rng() * 900000);
  return passing
    ? {
        command: 'mael gh read-pr --wait',
        output: `Checks: 4/4 complete\n  ✓ test\n  ✓ lint\n  ✓ e2e\n  ✓ web\nAll checks passed.\n`,
        exitCode: 0,
      }
    : {
        command: 'mael gh read-pr --wait',
        output: `Checks: 4/4 complete\n  ✓ lint\n  ✗ test (run ${run})\n  ✓ e2e\n  ✓ web\nBuild failed.\n`,
        exitCode: 1,
      };
}
