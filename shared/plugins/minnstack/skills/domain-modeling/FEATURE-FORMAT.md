# Feature file format

A feature file is a Gherkin file in `docs/specs/<subdomain>/<subsystem>/`. It is in `docs/specs/<subsystem>/` when the project has no subdomain level, and in `docs/specs/` when it has no subsystems. It holds one of these:

| File | Holds |
| --- | --- |
| `<command>.feature` or `<query>.feature` | The contract of one command or one query |
| `<type>.feature` | The invariants of one domain type |
| `<type>-repository.feature` | The laws of the repository of one domain type |

A contract is the set of rules of one command or query. A rule is one clause: what the command or query requires of its caller, what it ensures, or what always holds for the domain type. The contract states what a caller can observe, not how the code does it.

The reader is a developer or an analyst who is new to the subsystem. A feature file explains the command first, and is an index of the tests second. Write it as you would explain the command to that person: what it is for, what it does, what it needs, then the special cases.

| Gherkin | Contract meaning |
| --- | --- |
| `Feature` | One command, one query, or one domain type |
| The text below `Feature` | The purpose paragraph: what the command is for, in two or three sentences |
| `@type:<Type>` on the feature | The domain type that the feature belongs to |
| `Rule` | One contract clause |
| `@id:<id>` on the rule | The stable id of the clause |
| `Example` | One case that tells the reader a fact that the rule text does not |
| `Scenario Outline` with `Examples` | A rule that is a table: each row is one parallel fact |
| Given / When / Then | A state that meets the precondition / the command or query / the outcome |

- Name a feature for what the system does, not for a code module: `place-order.feature`, not `order_service.feature`.
- A command that a CLI and an API both reach has one feature file.
- A command that touches two domain types names the type whose state it changes. When it changes both, it belongs to the subsystem of its verb.
- Two doors that do the same thing from two input forms, such as one file and many files, are one command with one feature file.

## The shape of a file

```gherkin
@type:Order
Feature: Place order
  Turns the cart of a customer into an order, and takes the stock for it.

  Each value must be legal for an order: see order.feature.

  @id:E1
  Rule: A placed order is in state Placed

  # --- Who can place an order ---

  @id:R2
  Rule: Suspended customers cannot place orders

  # --- Code ---
  # Primary seam: `Order.place`
  # Outer seams: the `POST /orders` handler, the checkout page
```

- Open with the purpose paragraph. It says what the command is for and names the feature files that hold rules it also keeps. It names no code.
- Order the rules as you would explain them: what the command does, what it needs, then the special cases. Group them below comment headings when the file has more than about five.
- End with the code names, as comments below `# --- Code ---`. `Primary seam:` is required. `Held today at:` and `Outer seams:` are there only when the feature needs them.

## Seams

- The **primary seam** is the one interface in the code where the contract holds in full: most often a method of the domain type, or a use-case function. The tests of each rule call it. A feature has one.
- A feature file describes the model that the project wants. When the code does not yet hold the contract in full at the primary seam, the primary seam is the target, and a `Held today at:` line says where each kind of clause is held now and which doors do not hold it: ``Held today at: `R` rules in `validate_order`, `E` rules in `Order.place`. The batch import skips the `R` rules``. The tests of a rule call the code that holds it today. Delete the line when the code reaches the target.
- A `<type>.feature` can name a type that the target model has and the code does not. Its `Held today at:` line says what the code has in its place.
- An **outer seam** is an interface that reaches the primary seam from outside: a handler, an API route, a CLI command, a page. It has no contract of its own. It gets a test only for what it adds: that it passes the request on, and that it shows a refusal.
- `<type>.feature` names the type as its primary seam. `<type>-repository.feature` names the repository interface.
- Name a symbol or a route, not a file path or a line. When the code moves the symbol, edit the line in the same commit.
- `codebase-design` has the word seam, and the rules for where to put one.

## Rule ids

A rule id is a prefix and a number. The prefix is the kind of clause. The three kinds are Meyer's terms from Design by Contract, used for the Assertions of Evans (Domain-Driven Design, ch. 10):

| Prefix | Clause | Who it binds | Typical example |
| --- | --- | --- | --- |
| `R` | Requires: a precondition | The caller | A refusal with a named domain error, or with its reason |
| `E` | Ensures: an outcome | The command | A resulting state, or a domain event |
| `I` | Invariant | The domain type, always | Holds before and after each command |

```gherkin
@type:Order
Feature: Place order
  Turns the cart of a customer into an order, and takes the stock for it.

  @id:E1
  Rule: A placed order is in state Placed

  @id:R2
  Rule: Suspended customers cannot place orders
    Example: a customer who is suspended after the cart was filled
      Given a customer with a cart, who is then suspended
      When they place the order
      Then it is refused as CustomerSuspended

  # --- Code ---
  # Primary seam: `Order.place`
```

- Each rule has an id, with no exception: a plan cites it, a test cites it, and a check can join the two. Write it as the tag `@id:<id>` on the rule, not in the rule's name. The name stays plain prose, and a new wording does not touch the id.
- An id is local to its feature file. The rules of a file share one sequence of numbers across the three prefixes: `E1`, `R2`, `I3`. A new rule takes the next free number, and goes where the explanation needs it. An id is a label, not a position.
- The prefix is the kind of clause. The tag is the kind of evidence. They are separate choices.
- Never use an id again. A changed rule keeps its id. One exception: the rules of a file that is not yet merged can be deleted and numbered again.
- To withdraw a rule, delete its examples and its evidence tag, and write `@id:R2 @withdrawn` above `Rule: withdrawn`. The tag stays, so the id stays used.
- A test cites a rule as `<feature file stem>:<id>`, for example `place-order:E1`, in its name. Where a test name cannot hold `-` or `:`, write `_`: `test_place_order_E1_single_line_cart`. A test class keeps its name: each test method in it cites the rule. A test cites one rule. A test of the case that an `R` rule permits cites that rule. The stem names the command, not the door: when a command has two doors, the rest of the name says which. The feature file does not name its tests: a path or a test name in a feature file goes stale.
- A test of a rule of `<type>.feature` cites that file, and the rest of its name says which command: `test_order_I2_a_place_refuses_an_illegal_value`.
- A test of a table rule names its row: as the case id of a parametrised test, or in the test name, `test_order_I2_currency_…`.
- A feature file that has no ids gets them when it is next edited.

## Evidence tags

Each rule has one kind of evidence. A second tag on the rule, after `@id:<id>`, says which:

| Tag | Evidence | Use it for |
| --- | --- | --- |
| `@example` (the default, can be left out) | The rule's examples, run through step definitions | A requires or ensures rule. One or two examples. |
| `@invariant` | A predicate bound in code | A statement that always holds |
| `@cases:<path>` | A decision table or a fixture directory at the path | A rule that is a table: pricing, eligibility, tax, parser edge cases, state transitions |
| `@property` | A generator-based test | A statement that is a property, such as a round trip |
| `@unit` | Plain tests that cite the rule id | A project with no Gherkin runner, or a calculation with many boundaries |

- A tag on the `Feature` line is the evidence of each rule of the file that has no tag of its own.
- When `docs/README.md` names no Gherkin runner, or says `none`, the evidence of each rule that would be `@example` is `@unit`: write `@unit` once, on the `Feature` line. The other tags do not change.
- Write a table in the file, as a `Scenario Outline`, when it has about ten rows or fewer. Use `@cases:<path>` for a larger table or a fixture directory. A `@cases` path is relative to the repository root.
- No test is also valid. When a type or an assertion in the code makes the bad state impossible, write no test. Say which type or assertion in a comment below the rule. A refusal that an `R` rule names is not such an assertion: it gets evidence.
- A `@cases` rule keeps one example in the file, so the rule stays readable. Each fixture name states its equivalence class, for example `quoted-embedded-newline`.

## Domain types

A domain type is a type that the glossary names: an entity, a value object, or an aggregate. Its invariants are in `<type>.feature`. A `Rule` with no example is valid Gherkin.

```gherkin
@type:Order @invariant
Feature: Order
  An order is what one customer buys in one step. This file says what always holds for it.

  @id:I1
  Rule: Order total equals the sum of line totals

  @id:I2
  Rule: A Placed order has at least one line

  # --- Code ---
  # Primary seam: `Order`
```

- Write an `I` rule for a domain type only when more than one command must keep it true. Its evidence is one predicate, not a test for each command. Do not state it again as an `E` rule of a command.
- State a rule that more than one command keeps once, in `<type>.feature`. A command file refers to that file in its purpose paragraph and does not state the rule again. "The order must exist" and "the currency must be legal" are rules of the type, not one `R` rule for each command.
- When the code has no such predicate yet, tag the `I` rule `@unit`. Each command that must keep it has a test that cites the `I` rule.
- State the legal values of the fields as one table rule, with a legal value, an illegal value and the refusal for each field:

  ```gherkin
  @id:I2
  Rule: Each field of an order holds a legal value
    Scenario Outline: <field>
      Then the <field> "<good>" is accepted, and "<bad>" is refused: <refusal>

      Examples:
        | field    | legal                 | good | bad | refusal         |
        | currency | an ISO 4217 code      | NZD  | XX  | no currency XX  |
        | quantity | a whole number, 1 up  | 2    | 0   | no quantity 0   |
  ```

- A constraint that one command guards is an `R` or `E` rule of that command, not an invariant. When a second command must keep it, withdraw the rule in the command's file and add an `I` rule to the type.

## Queries

A query reads and changes nothing. Its rules are `E` rules: what the result holds, and in which order. Give it an `R` rule only when it can refuse. A pure function that several commands call, such as a parser, is a query. When it belongs to no domain type, its feature has no `@type` tag.

## Repositories

A repository stores and returns one domain type. Write `<type>-repository.feature` only when the repository has two or more implementations, such as a database and an in-memory fake. The tests that cite its rules run against each implementation, the fake included. When the domain code talks to the database directly, write no file: the `E` rules of the commands cover the stored state.

The rules are laws that hold across methods, so they are `I` rules. The rule about more than one command does not apply to them:

```gherkin
@type:Order @repository
Feature: Order repository
  Stores and returns orders. The laws hold for each implementation.

  @id:I1 @property
  Rule: An order that is written reads back equal

  @id:I2 @unit
  Rule: A read of an unknown id returns nothing

  @id:I3 @unit
  Rule: A list is in creation order

  # --- Code ---
  # Primary seam: `OrderRepository`
```

Use `@property` for a law about the preservation of data: one example does not set every field. Use `@unit` for the others, as one suite that runs against each implementation.

## Authoring rules

A contract is partial by design. A command with few rules is not a defect. `design-build-test` selects the tests for a rule, at its primary seam.

- When you look for rules, look for the `I` and `R` rules before the `E` rules. They cost least and carry the most value. This is the order of the search, not of the file.
- Write an `E` rule only for an outcome that a caller depends on. Do not try to describe the whole command: a complete postcondition is a second implementation.
- Write rules from the glossary and the purpose of the command or query, not from the existing tests. Read the tests after that, to find the ones to change, to delete, or to question.
- State each rule as prose: the rule text, the example names, and the steps. Use no expression language. Take each domain noun from the glossary: when a rule needs one that the glossary lacks, add the term first. An engineering word for a property that a caller observes, such as "idempotent" or "atomic", is correct.
- A rule states what a caller observes. A field, a key, an argument, a constant, and a call order are facts about the code, not rules.
- A rule that is a table stays one rule, not one rule for each row. Two rules that differ by one noun are two rows of one table: "the branch comes from…", "the Priority comes from…".
- A rule that says what the name of the command says is not a rule. Delete it: "the deleted order no longer exists".
- A rule that the glossary states is not stated again. Use the term.
- Behaviour that crosses subsystems or services is not a rule: a saga, eventual consistency, a message schema. Name its event in the glossary. An ADR or a schema holds the agreement.
- Write an example only when it tells the reader a fact that the rule text does not: a boundary, a surprise, or a case that the reader would guess wrong. A rule that is clear has no example. An example that states the rule again, such as "an unknown order / refused: no order", is deleted.
- A rule that needs more than two examples is a table.
- The example of an `R` rule names the domain error of the refusal, as the glossary has it. When the project has no named domain errors, the example states the reason in prose: `Then it is refused: a task cannot follow itself`.
- A precondition is an `R` rule only when the caller can check it before the call. When the caller cannot, such as stock that runs out meanwhile, it is an `E` rule whose outcome is a refusal.

Before you finish a file, read it aloud as an explanation to a person who is new. Where you would say "and the same for…", the file has a table that is still written as rules. Where you would skip a line, delete it.
