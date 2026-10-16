/**
 * Two versions of one feature file, for the stories and the fake `changes`
 * scenario: a step edit, a rename, an added and a removed scenario, an outline
 * whose rows move, and a rule with a tag added.
 */
export const OLD_FEATURE = `@auth
Feature: Token rotation
  A token past its expiry rotates, so a client keeps its session.

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
  A token past its expiry rotates, so a client keeps its session.

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
