/**
 * Two versions of one feature file, for the stories and the fake `changes`
 * scenario: a step edit, a rename, an added and a removed scenario, an outline
 * whose rows move, and a rule with a tag added.
 */
export const OLD_FEATURE = `@auth
Feature: Token rotation
  A token past its expiry rotates, so a client keeps its session. The
  client never sees the expiry: the API swaps the token in its reply.

  Rotation is per client. Two clients of one user rotate apart, and a
  revoked token never rotates.

  Background:
    Given a signed-in client

  Scenario: Rotate an expired token
    Given a token that expired a minute ago
    When the client calls the API
    Then it gets a new token

  Scenario: Keep a fresh token
    Given a token that expires in an hour
    When the client calls the API
    Then it keeps its token

  Scenario Outline: Grace period
    Given a token that expired <age> ago
    Then the old token is <state>

    Examples:
      | age        | state    |
      | 30 seconds | accepted |
      | 2 minutes  | refused  |

  Rule: Revocation
    Scenario: Revoke a stolen token
      Given a token reported stolen
      When an admin revokes it
      Then the client must sign in again

  Scenario: Log every rotation
    When a token rotates
    Then the log says:
      """
      rotated token for client 42
      """
`;

export const NEW_FEATURE = `@auth
Feature: Token rotation
  A token past its expiry rotates, so a client keeps its session. The
  client never sees the expiry: the API swaps the token in its reply.

  Rotation is per client. Two clients of one user rotate apart, and a
  revoked token never rotates.

  Background:
    Given a signed-in client

  Scenario: Rotate an expired token
    Given a token that expired a minute ago
    When the client calls the API
    Then it gets a new token
    And the old token is kept for a minute

  Scenario: Leave a fresh token alone
    Given a token that expires in an hour
    When the client calls the API
    Then it keeps its token

  Scenario Outline: Grace period
    Given a token that expired <age> ago
    Then the old token is <state>

    Examples:
      | age        | state    |
      | 30 seconds | accepted |
      | 59 seconds | accepted |
      | 2 minutes  | refused  |

  @security
  Rule: Revocation
    Scenario: Revoke a stolen token
      Given a token reported stolen
      When an admin revokes it
      Then the client must sign in again

  Scenario: Rotate on demand
    Given a token that expires in an hour
    When the client asks for a new token
    Then it gets a new token
      | field  | value |
      | expiry | 1h    |
`;

/**
 * A new feature file, for the story and the fake `changes` scenario: a
 * description of hard-wrapped paragraphs, a rule, and a step table wider than
 * the prose column.
 */
export const ADDED_FEATURE = `@billing
Feature: Invoice reminders
  An unpaid invoice sends its customer a reminder, so a payment is not
  missed. A reminder names the invoice and links to \`invoice.pay_url\`.

  Reminders stop the moment the invoice is paid, and a customer can
  mute them for one invoice.

  Background:
    Given a customer with an email address
    And an invoice of 120.00 due on 1 March

  Scenario: Remind on the due date
    When the clock reaches 1 March 09:00
    Then the customer gets a reminder for the invoice

  Rule: Overdue invoices escalate
    An invoice past its due date gets a reminder each week, and after
    four weeks its account manager is told.

    Scenario: Weekly reminders
      Given the invoice is unpaid
      When these times pass:
        | time                      | reminder sent at          | recipient                 |
        | 2026-03-08T09:00:00+13:00 | 2026-03-08T09:00:04+13:00 | customer@northwind.example |
        | 2026-03-15T09:00:00+13:00 | 2026-03-15T09:00:02+13:00 | customer@northwind.example |
        | 2026-03-29T09:00:00+13:00 | 2026-03-29T09:00:05+13:00 | manager@northwind.example  |
      Then the customer has 3 reminders

    @slow
    Scenario: Stop once paid
      Given the invoice is paid on 10 March
      When the clock reaches 15 March 09:00
      Then no reminder is sent
`;
