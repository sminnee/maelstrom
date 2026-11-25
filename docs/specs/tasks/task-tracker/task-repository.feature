@type:Task @repository @unit
Feature: Task repository
  Stores and returns tasks. The laws below hold for each implementation, so a test can use the
  one in memory in place of the database.

  @id:I1
  Rule: A task that is saved reads back equal
    Example: every field is set
      Given a task with a value in each field, two Follows and two sessions
      When the task is saved and loaded
      Then the loaded task equals the saved task

  @id:I2
  Rule: A read of an unknown project and id returns nothing

  @id:I3
  Rule: A save of a project and id that exist replaces that task

  @id:I4
  Rule: A delete removes the task, and a delete of an unknown task changes nothing

  @id:I5
  Rule: A list is oldest first, and the id breaks a tie

  @id:I6
  Rule: A list holds only the tasks of the project, the Status and the Parent that it names

  @id:I7
  Rule: A task is found by each of its session ids, and a blank id finds nothing

  @id:I8
  Rule: The followers of a set of ids are the tasks, of each project, that follow one of them

  @id:I9
  Rule: A transaction commits each of its writes, or none

  @id:I10
  Rule: A read of the changes since a revision names each saved and each deleted task once
    Example: one save after the reading
      Given a reading of the changes at the current revision
      When one task is saved
      Then the changes since that reading hold only that task

  # --- Code ---
  # Primary seam: `mael_domain.task_table.TaskTable`
  # The suite runs against each implementation: `InMemoryTaskTable` and `SqliteTaskTable`.
