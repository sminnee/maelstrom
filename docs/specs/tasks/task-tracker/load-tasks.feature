@type:Task @unit
Feature: Load tasks
  Makes many tasks in one step, from task files: the Task drafts of a Task set, or the blocks
  of one text. The new tasks form a Chain. A load after a task puts that Chain between the task
  and its followers.

  Each value must be legal, and each task and project that the caller names must exist: see
  task.feature.

  # --- What the caller must give ---

  @id:R1
  Rule: A load needs one task file or more

  @id:R2
  Rule: The caller of a load can name the command, the mode, the models, the Priority, the branch, the Parent, the Status actions and the Post-merge work only
    Example: a title from the caller
      When a task file is loaded with the title "Other" named by the caller
      Then it is refused: a load cannot set the title

  @id:R3
  Rule: The task that a load is after must be a task of the project of the load
    # A load ignores an after-task that no longer exists.

  # --- All or none ---

  @id:E4
  Rule: A load creates each of its tasks, or none
    Example: the second file does not parse
      Given two task files, where the second has no title
      When they are loaded
      Then it is refused, and the refusal names the second file
      And no task is created and no task has new Follows

  # --- The order of the new tasks ---

  @id:E5
  Rule: The tasks of a Task set form one Chain in the order given
    Example: three Task drafts
      When the Task drafts "one", "two" and "three" are loaded in that order
      Then "two" follows "one" and "three" follows "two"

  @id:E6
  Rule: The first task follows what the caller names, else the task that it is loaded after

  @id:E7
  Rule: A load after a task is a Splice
    Example: one follower waits and one has run
      Given "c" (todo) and "d" (Done) follow "p"
      When the Task drafts "n1" and "n2" are loaded after "p"
      Then "c" follows "n2" and no longer follows "p"
      And "d" still follows "p"

  @id:E8
  Rule: A task file can follow a task of the same load by its name
    Example: the second block follows the first
      Given the blocks "api" and "ui", where "ui" follows "api"
      When they are loaded
      Then the task of "ui" follows the id that the task of "api" got

  @id:E9
  Rule: A task file can follow the end of a Chain
    Example: the wildcard is the Chain of its own Parent
      Given "p.1" and "p.2" with the Parent "p", where "p.2" follows "p.1"
      When a task with the Parent "p" is loaded to follow the end "*"
      Then it follows "p.2"

  # --- The values of the new tasks ---

  @id:E10
  Rule: A loaded task is todo, and each other value comes from the caller, else from its task file, else as create-task says
    The default Parent of a load applies only to a task file that names none.
    Example: the default Parent
      Given a load with the default Parent "p", of one file with no Parent and one with the Parent "q"
      When it is loaded
      Then the first task has the Parent "p" and the second has the Parent "q"
    Example: the caller wins
      Given a Task draft with the Parent "a"
      When it is loaded with the Parent "b" named by the caller
      Then the new task has the Parent "b"

  @id:E11
  Rule: A loaded task links to the comms of each task that it comes from
    These are the task that the load is after, the Parent of the new task, each task that the
    new task follows, and the task of the session that loads it.

  # --- Code ---
  # Primary seam: `mael_domain.task.load_tasks`
  # Held today at: two functions. `task.promote_chain` takes Task drafts. `task.load_many` takes
  #   the blocks that `parse_task_blocks` reads. R rules in `validate_command("task.promote")`
  #   and `validate_command("task.loadMany")`.
  # Outer seams: `mael task promote`, `mael task load-many`, `POST /api/tasks/promote`,
  #   `POST /api/tasks/load-many`, the approval of a Task set
  # `parse_task_blocks` and `parse_draft` are queries that this command calls. This file states
  #   no rule of them.
