@type:Task
Feature: Load tasks
  Primary seam: `mael_domain.task.load_tasks`
  Held today at: two functions. `task.promote_chain` takes Task drafts. `task.load_many` takes
    the blocks that `parse_task_blocks` reads. R rules in `validate_command("task.promote")`
    and `validate_command("task.loadMany")`. No door keeps R2 for a follow that a block names,
    or R11 for a mode that a task file names.
  Outer seams: `mael task promote`, `mael task load-many`, `POST /api/tasks/promote`,
    `POST /api/tasks/load-many`, the approval of a Task set

  # `parse_task_blocks` and `parse_draft` are queries that this command calls. This file states
  # no rule of them.

  @id:R1 @unit
  Rule: A load needs one task file or more
    Example: no file
      When a load is asked for with no task file
      Then it is refused: nothing to promote

  @id:R2 @unit
  Rule: Each task that a loaded task follows must exist or be in the same load
    Example: an unknown task
      When a load names "zzzz" as the task that the first task follows
      Then it is refused: no task zzzz

  @id:E3 @unit
  Rule: A load creates each of its tasks, or none
    Example: the second file does not parse
      Given two task files, where the second has no title
      When they are loaded
      Then it is refused, and the refusal names the second file
      And no task is created and no task has new Follows

  @id:E4 @unit
  Rule: The tasks of a Task set form one Chain in the order given
    Example: three Task drafts
      When the Task drafts "one", "two" and "three" are loaded in that order
      Then "two" follows "one" and "three" follows "two"

  @id:E5 @unit
  Rule: The first task follows what the caller names, else the task that it is loaded after
    Example: no follows named
      Given the planning task "p.1"
      When two Task drafts are loaded after "p.1"
      Then the first new task follows "p.1"

  @id:E6 @unit
  Rule: A load after a task is a Splice
    Example: one follower waits and one has run
      Given "c" (todo) and "d" (done) follow "p"
      When the Task drafts "n1" and "n2" are loaded after "p"
      Then "c" follows "n2" and no longer follows "p"
      And "d" still follows "p"

  @id:E7 @unit
  Rule: A task file can follow a task of the same load by its name
    Example: the second block follows the first
      Given the blocks "api" and "ui", where "ui" follows "api"
      When they are loaded
      Then the task of "ui" follows the id that the task of "api" got

  @id:E8 @unit
  Rule: A task that follows the end of a Chain follows each task of it that no other follows
    Example: the wildcard
      Given "p.1" and "p.2" with the Parent "p", where "p.2" follows "p.1"
      When a task with the Parent "p" is loaded to follow the end "*"
      Then it follows "p.2"

  @id:E9 @unit
  Rule: A value that the caller names wins over the value of a task file
    Example: the parent
      Given a Task draft with the Parent "a"
      When it is loaded with the Parent "b" named by the caller
      Then the new task has the Parent "b"

  @id:E10 @unit
  Rule: A loaded task is todo, with the Recipe and the branch of its task file
    Example: a task file with a model
      Given a task file with the title "Add login" and the model "opus"
      When it is loaded
      Then a todo task "Add login" exists with the model "opus"

  @id:R11 @unit
  Rule: The Priority and the mode must be values that a task can have
    Example: an unknown Priority from the caller
      When a task file is loaded with the Priority "urgent"
      Then it is refused: urgent is not a Priority

  @id:E12 @unit
  Rule: A task file with no Parent takes the Parent of the load
    Example: a Parent in the file wins
      Given a load with the Parent "p" of one file with no Parent and one file with the Parent "q"
      When it is loaded
      Then the first task has the Parent "p" and the second has the Parent "q"

  @id:E13 @unit
  Rule: A loaded task links to the comms of each task that it comes from
    Example: a load after a task
      Given the task "a" linked to the comm "c1"
      When a task file is loaded after "a"
      Then the new task links to "c1"

  @id:R14 @unit
  Rule: The caller of a load can name the command, the mode, the models, the Priority, the branch, the Parent and the Status actions only
    Example: a title from the caller
      When a task file is loaded with the title "Other" named by the caller
      Then it is refused: a load cannot set the title

  @id:R15 @unit
  Rule: The task that a load is after must be a task of the project of the load
    Example: a task of another project
      When a task file is loaded in the project "northwind" after "acme/a"
      Then it is refused: the task is in another project

  @id:R16 @unit
  Rule: The project of a load must exist
    Example: an unknown project
      When a task file is loaded in the project "nowhere"
      Then it is refused: no project nowhere
