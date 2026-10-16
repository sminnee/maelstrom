import { AstBuilder, GherkinClassicTokenMatcher, Parser } from '@cucumber/gherkin';
import { IdGenerator } from '@cucumber/messages';
import type * as messages from '@cucumber/messages';

/** A step with its argument: a data table as rows of cells, or a doc string. */
export interface Step {
  keyword: string;
  text: string;
  table: string[][] | null;
  docString: string | null;
}

export interface Examples {
  keyword: string;
  name: string;
  tags: string[];
  header: string[] | null;
  rows: string[][];
}

/** A Background or a Scenario. An outline is a scenario with examples. */
export interface Block {
  kind: 'background' | 'scenario';
  keyword: string;
  name: string;
  description: string;
  tags: string[];
  steps: Step[];
  examples: Examples[];
}

export interface Rule {
  kind: 'rule';
  keyword: string;
  name: string;
  description: string;
  tags: string[];
  children: Block[];
}

export interface Feature {
  keyword: string;
  name: string;
  description: string;
  tags: string[];
  children: (Block | Rule)[];
}

/**
 * A `.feature` file's text as its feature, or `null` when the text holds none.
 * A parse error is `{ error }`, with the line and column in its message.
 */
export function parseFeature(text: string): { feature: Feature | null } | { error: string } {
  const parser = new Parser(
    new AstBuilder(IdGenerator.incrementing()),
    new GherkinClassicTokenMatcher(),
  );
  let doc: messages.GherkinDocument;
  try {
    doc = parser.parse(text);
  } catch (e) {
    // A composite error heads its list with a line of its own.
    const message = e instanceof Error ? e.message : String(e);
    return { error: message.replace(/^Parser errors:\n/, '') };
  }
  const f = doc.feature;
  if (!f) return { feature: null };
  return {
    feature: {
      keyword: f.keyword,
      name: f.name,
      description: f.description,
      tags: tags(f.tags),
      children: f.children.flatMap((c): (Block | Rule)[] => {
        if (c.rule)
          return [
            {
              kind: 'rule',
              keyword: c.rule.keyword,
              name: c.rule.name,
              description: c.rule.description,
              tags: tags(c.rule.tags),
              children: c.rule.children.flatMap(block),
            },
          ];
        return block(c);
      }),
    },
  };
}

function block(c: messages.RuleChild): Block[] {
  if (c.background)
    return [
      {
        kind: 'background',
        keyword: c.background.keyword,
        name: c.background.name,
        description: c.background.description,
        tags: [],
        steps: c.background.steps.map(step),
        examples: [],
      },
    ];
  if (c.scenario)
    return [
      {
        kind: 'scenario',
        keyword: c.scenario.keyword,
        name: c.scenario.name,
        description: c.scenario.description,
        tags: tags(c.scenario.tags),
        steps: c.scenario.steps.map(step),
        examples: c.scenario.examples.map((e) => ({
          keyword: e.keyword,
          name: e.name,
          tags: tags(e.tags),
          header: e.tableHeader ? cells(e.tableHeader) : null,
          rows: e.tableBody.map(cells),
        })),
      },
    ];
  return [];
}

function step(s: messages.Step): Step {
  return {
    keyword: s.keyword,
    text: s.text,
    table: s.dataTable ? s.dataTable.rows.map(cells) : null,
    docString: s.docString ? s.docString.content : null,
  };
}

const tags = (list: readonly messages.Tag[]) => list.map((t) => t.name);
const cells = (row: messages.TableRow) => row.cells.map((c) => c.value);
