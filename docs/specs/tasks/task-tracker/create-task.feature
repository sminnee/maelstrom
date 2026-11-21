@type:Task
Feature: Create task
  Primary seam: `mael_domain.task.create`
  Held today at: R rules in `validate_command("task.create")`. E rules in `task.create` and
    `task.duplicate`. `mael task add` in a terminal goes through the orchestrator server.
  Outer seams: `mael task add`, `POST /api/tasks`

  @id:R1 @unit
  Rule: A new task needs a title or content
    Example: no title and no content
      When a task is created with no title, no content and no task to copy
      Then it is refused: a title or prose is required

  @id:R2 @unit
  Rule: Each task that the new task follows must exist
    Example: an unknown task
      When a task is created that follows "zzzz"
      Then it is refused: no task zzzz

  @id:R3 @unit
  Rule: The Priority and the mode must be values that a task can have
    Example: an unknown priority
      When a task is created with the priority "urgent"
      Then it is refused: no priority urgent

  @id:R4 @unit
  Rule: A task to copy must exist
    Example: an unknown source
      When a task is created as a copy of "zzzz"
      Then it is refused: no task zzzz

  @id:R5 @unit
  Rule: Each comm that the new task links to must exist
    Example: an unknown comm
      When a task is created with the comm link "c99"
      Then it is refused: no comm c99

  @id:E6 @unit
  Rule: A new task is todo, with the Priority medium and the mode plan unless it names others

  @id:E7 @unit
  Rule: A new task gets an id that no task of its project has
    Example: the third child of a parent
      Given the tasks "p.1" and "p.2" with the Parent "p"
      When a task is created with the Parent "p"
      Then its id is "p.3"

  @id:E8 @unit
  Rule: A new task takes the branch of its Chain, else of its Parent task, else a generated branch
    Example: a second task under a parent
      Given the task "p.1" with the Parent "p" and the branch "feat/login"
      When a task is created with the Parent "p" and no branch
      Then its branch is "feat/login"
    Example: a branch that the caller names
      Given the task "p.1" with the Parent "p" and the branch "feat/login"
      When a task is created with the Parent "p" and the branch "fix/other"
      Then its branch is "fix/other"
    Example: no Chain
      When a task is created with the title "Add login", no Parent and no branch
      Then its branch is generated from the title

  @id:E9 @unit
  Rule: A new task links to the comms of each task that it is made from
    Example: a task that follows a linked task
      Given the task "a" with the comm link "c3"
      When a task is created that follows "a", with no comm links named
      Then it has the comm link "c3"
    Example: an empty list wins
      Given the task "a" with the comm link "c3"
      When a task is created that follows "a", with an empty list of comm links
      Then it has no comm link

  @id:E10 @unit
  Rule: A copy takes only the Recipe of its source, and each value that the caller names wins
    Example: a copy with a new title
      Given the task "a" with the title "Weekly check" and the command "/review"
      When a task is created as a copy of "a" with the title "Check now"
      Then its title is "Check now" and its command is "/review"
      And the task "a" is not changed
    Example: a source that has run
      Given the task "a" with a Task run and a schedule
      When a task is created as a copy of "a"
      Then the copy has no Task run and no schedule

  @id:E11 @unit
  Rule: A new task that follows the end of a Chain follows each task of it that no other follows
    Example: a Chain with one end
      Given "p.2" follows "p.1", both with the Parent "p"
      When a task is created that follows the end of the Chain of "p"
      Then it follows "p.2"

  @id:E12 @unit
  Rule: A new task with content and no title takes its title from the first line of its content
    # Held today in the orchestrator server, as the stand-in until the generated title arrives.
    Example: content only
      When a task is created with the content "Fix the login page\nIt fails on Safari." and no title
      Then its title is "Fix the login page"

  @id:R13 @unit
  Rule: A new task is todo or a Template
    # A caller names no status, or names `template`.
    Example: a task created as done
      When a task is created with the status "done"
      Then it is refused: a new task cannot be done

  @id:R14 @unit
  Rule: A task to copy must be a task of the project of the new task
    Example: a task of another project
      Given the task "a" in the project "acme"
      When a task is created in the project "northwind" as a copy of "acme/a"
      Then it is refused: the task to copy is in another project

  @id:R15 @unit
  Rule: The project of a new task must exist
    Example: an unknown project
      When a task is created in the project "nowhere"
      Then it is refused: no project nowhere
