@type:Task @repository
Feature: Task repository
  Primary seam: `mael_domain.task_table.TaskTable`

  # The suite runs against each implementation: `InMemoryTaskTable` and `SqliteTaskTable`.

  @id:I1 @unit
  Rule: A task that is saved reads back equal
    Example: every field is set
      Given a task with a value in each field, two Follows and two sessions
      When the task is saved and loaded
      Then the loaded task equals the saved task

  @id:I2 @unit
  Rule: A read of an unknown project and id returns nothing

  @id:I3 @unit
  Rule: A save of a project and id that exist replaces that task

  @id:I4 @unit
  Rule: A delete removes the task, and a delete of an unknown task changes nothing

  @id:I5 @unit
  Rule: A list is oldest first, and the id breaks a tie
    Example: two tasks of one instant
      Given tasks "b" and "a" with the same creation time
      When the project is listed
      Then "a" is before "b"

  @id:I6 @unit
  Rule: A list holds only the tasks of the project, the status and the Parent that it names

  @id:I7 @unit
  Rule: A task is found by each of its session ids, and a blank id finds nothing

  @id:I8 @unit
  Rule: The followers of a set of ids are the tasks, of each project, that follow one of them

  @id:I9 @unit
  Rule: A transaction commits each of its writes, or none
    Example: a failure after the first write
      Given a transaction that saves one task and then fails
      When the task is loaded
      Then nothing is returned

  @id:I10 @unit
  Rule: A read of the changes since a revision names each saved and each deleted task once
    Example: one save after the reading
      Given a reading of the changes at the current revision
      When one task is saved
      Then the changes since that reading hold only that task
