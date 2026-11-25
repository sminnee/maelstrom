@type:Task @unit
Feature: Task
  A task is one piece of work for one agent session. This file says what a task can hold, and
  what each command that names a task does. A command file does not state these rules again.

  @id:I1
  Rule: A task id is unique in its project

  @id:I2
  Rule: Each field of a task holds a legal value
    A command that sets a field refuses an illegal value, and the refusal names the value.

    Scenario Outline: <field>
      Then the <field> "<good>" is accepted, and "<bad>" is refused: <refusal>

      Examples:
        | field     | legal                                                  | good      | bad    | refusal             |
        | title     | not blank                                              | Add login | (spaces) | a title is required |
        | Priority  | critical, high, medium, low                            | high      | urgent | no priority urgent  |
        | mode      | plan, normal, auto                                     | plan      | yolo   | no mode yolo        |
        | Status    | todo, in-progress, built, merged, published, cancelled | built     | paused | no status paused    |
        | id        | letters, digits, ".", "-" and "_", with no path in it  | p.3       | ../a   | unsafe task id      |
        | Follows   | tasks that exist, of any project                       | acme/a    | zzzz   | no task zzzz        |
        | comm link | a comm that exists                                     | c3        | c99    | no comm c99         |

    # A move also takes the word `done`: see set-task-status.feature.
    # An update checks only the comm links that it adds.
    # A task that is Done or cancelled can name, in its Follows, a task that is gone.

  @id:I3
  Rule: Follows makes no cycle
    Example: a task that follows itself
      When the task "a" is changed to follow "a"
      Then it is refused: a task cannot follow itself
    Example: three tasks
      Given "b" follows "a" and "c" follows "b"
      When the task "a" is changed to follow "c"
      Then it is refused: that would make a cycle

  @id:I4
  Rule: A command refuses a task or a project that does not exist
    This holds for the task that a command changes or copies, and for the project of a new task.

  @id:I5
  Rule: Each command sets the updated time of the task that it names, and keeps its created time
    # A task that a delete or a rename changes on the side keeps its updated time.

  # --- Code ---
  # Primary seam: `mael_domain.task.Task`
  # Held today at: no predicate. The refusals of `validate_command` in the orchestrator server
  #   keep I2 to I4 for a create, an update and a load. `task.move`, `task.rename` and
  #   `task.delete` keep their own part. The task table keeps I1.
  # Doors that do not keep I2 and I3: `mael task update` in a terminal checks the Priority,
  #   the mode and the comm links only, and `mael task edit` checks nothing. A load of task blocks does not check
  #   the Follows or the mode that a block names.
  # The code has one more Status, `template`, until Template is a type: a move to it or from it
  #   is accepted.
