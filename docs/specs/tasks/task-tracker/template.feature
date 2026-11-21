@type:Template
Feature: Template
  Primary seam: `mael_domain.task.Template`
  Held today at: no type. A template is a `Task` row with the status `template`. `is_actionable`
    and the default view of `mael task list` keep I1.

  @id:I1 @unit
  Rule: A template is never Actionable
    Example: a template that follows nothing
      Given a template that follows no task
      When the Task tracker is asked if it is Actionable
      Then the answer is no
