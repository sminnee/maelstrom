@type:Task @unit
Feature: Create task
  Makes one new todo task. The caller names what it knows. The Task tracker takes each other
  value from the Chain that the task joins, or from a default. A copy starts from the Recipe of
  another task.

  Each value must be legal, and each task and project that the caller names must exist: see
  task.feature.

  # --- What the caller must give ---

  @id:R1
  Rule: A new task needs a title, content, or a task to copy

  @id:R2
  Rule: A new task is todo or a Template
    Example: a task created as done
      When a task is created with the status "done"
      Then it is refused: a new task cannot be done

  @id:R3
  Rule: A task to copy must be in the project of the new task

  # --- What the Task tracker fills in ---

  @id:E4
  Rule: A value that the caller does not name comes from the Chain, else from a default
    Scenario Outline: <field>
      Then the <field> of the new task is <value>

      Examples:
        | field      | value |
        | Status     | todo                                                                   |
        | Priority   | medium                                                                 |
        | mode       | plan                                                                   |
        | id         | an id that no task of its project has: below a Parent, the next free one, such as "p.3" |
        | title      | the first line of its content                                          |
        | branch     | the branch of its Chain, else of its Parent task, else a generated one |
        | comm links | the comm links of its Parent, of each task that it follows or copies, and of the task of the session that makes it |

    Example: an empty list is a value
      Given the task "a" with the comm link "c3"
      When a task is created that follows "a", with an empty list of comm links
      Then it has no comm link

  @id:E6
  Rule: A new task can follow the end of a Chain

  # --- Copies ---

  @id:E5
  Rule: A copy takes only the Recipe of its source, and each value that the caller names wins
    Example: a source that has run
      Given the task "a" with a Task run and a schedule
      When a task is created as a copy of "a"
      Then the copy has no Task run and no schedule
      And the task "a" is not changed

  # --- Code ---
  # Primary seam: `mael_domain.task.create`
  # Held today at: R rules in `validate_command("task.create")`. E rules in `task.create` and
  #   `task.duplicate`. The title row of E4 is held in the orchestrator server, until the
  #   generated title arrives. `mael task add` in a terminal goes through the orchestrator server.
  # Outer seams: `mael task add`, `POST /api/tasks`
