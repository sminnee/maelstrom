# Plan Task Command

⚠️ **PLAN MODE REQUIRED**: This command ONLY works in plan mode. You must stop with an error message
immediately if not in plan mode.

Creates sub-tasks on a Linear issue to break down larger projects into manageable Claude Code
sessions.

## Usage

```
/plan-task <issue-id-or-description>
```

Examples:

- `/plan-task NORT-445`
- `/plan-task flexible research agent`

## What This Command Does

1. **Fetches the issue** from Linear using the CLI tool
2. **Researches the codebase** to understand the issue context
3. **Guides you through project breakdown** with interactive questions
4. **Creates sub-tasks on the Linear issue** for each project phase
5. **Ensures phases fit within single Claude Code sessions**
6. **Only creates sub-tasks if the issue doesn't already have them**
7. **Does NOT begin work** - use `/continue-task` to start working on the created sub-tasks

## Command Logic

1. **MANDATORY Plan Mode Check**: MUST fail immediately if not in plan mode - do not proceed with
   any other logic
2. **Fetch Issue Details**: Use the Linear CLI to get issue information:

   ```bash
   mael linear read-task <issue-id>
   ```

   - If argument looks like an issue ID (e.g., `NORT-489`), use it directly
   - Otherwise, use `list-tasks` to find matching issues by title

3. **Codebase Research**: Use Task tool with Explore subagent(s) to research the codebase:
   - Launch 1-3 Explore agents in parallel for efficient research
   - Examine relevant code files and subsystems mentioned in the issue
   - Review existing patterns and architecture in affected areas
   - Identify dependencies and integration points
   - Understand current implementation state

4. **Interactive Planning**: Use AskUserQuestion tool to guide user through project breakdown
   (informed by codebase research):
   - Project overview and goals (based on what you learned)
   - Sub-systems involved (confirm your understanding from research)
   - Project phases (propose phases based on codebase structure)
   - For each phase: key outcomes and validation approach
   - Build structured sub-task data (array of {title, description} objects)
   - Ensure each phase fits within single Claude Code session (50-200 lines of code)

5. **Write Plan to File**: Write the detailed plan to the plan file (path provided in system
   context):
   - Research findings and understanding of the issue
   - Proposed sub-tasks that will be created in Linear (with titles and descriptions)
   - How sub-tasks map to project phases
   - Include "Create sub-tasks in Linear" as the final execution step

6. **Present Plan**: Call ExitPlanMode with allowedPrompts:
   - `{"tool": "Bash", "prompt": "create subtasks in Linear"}`

7. **After Plan Approval - Create Sub-Tasks**: For each planned phase, create a subtask:
   ```bash
   mael linear create-subtask <parent-id> "<title>" "<description>"
   ```

## Error Cases

- Not in plan mode: "Plan-task command requires plan mode. Please enter plan mode first before using
  this command."
- No matching issue found
- Issue already has sub-tasks

## Sub-Task Structure

Each sub-task created will:

- Be scoped to fit within a single Claude Code context window
- Have a clear title describing the high-level goal
- Include description with:
  - Key outcomes expected
  - Testing/validation approach
  - Brief bullet points of main work
- Be linked as a sub-issue to the parent issue
- Inherit the cycle from the parent issue

### Best Practices for Phase Design

#### Scope Guidelines

- **Each phase = one Claude Code session**: Substantial enough work to validate with testing
- **Code change constraint**: Should result in roughly 50-200 lines of code modified (based on git
  diff stats)
- **High-level goals only**: Avoid prescriptive technical details that may change
- **Testable outcomes**: Each phase should produce something that can be validated
- **Logical dependencies**: Later phases can build on earlier phase outputs

#### Content Guidelines

- **Focus on outcomes over implementation**: What needs to be achieved, not how
- **Leave technical flexibility**: Let implementers make specific technical decisions
- **Include validation criteria**: How to know if the phase succeeded
- **Keep phases substantial**: Avoid tiny tasks that don't warrant full sessions

#### Common Project Types

**New Feature Projects**:

- Core functionality built in backend model with tests
- UI and frontend/backend integration combined (easier to validate endpoints are appropriate)
- For large features, break into functional slices as separate phases

**Refactoring Projects**:

- Analysis and planning should be done during project file creation
- Break down into logical sub-refactors based on the specific refactoring needs
- Validation is primarily "run the tests" to ensure nothing breaks

**System Integration Projects**:

- Break down feature by feature into logical phases
- Each phase includes its own testing and validation
- Integration tests (Playwright) may be appropriate as a final phase

## Examples

### Simple Feature Project (NORT-123: User Profile Enhancement)

**Sub-task 1: Core Avatar System**

```
Title: Build avatar upload and storage system
Description:
- File upload handling with validation
- Image processing and resizing
- Storage integration
- Unit tests for all functionality
```

**Sub-task 2: UI Integration**

```
Title: Add avatar UI to profile pages
Description:
- Upload interface in settings
- Display avatars across application
- Fallback handling for missing avatars
- Component tests and user testing
```

### Complex System Project (NORT-456: Search System Overhaul)

**Sub-task 1: Search Infrastructure**

```
Title: Build new search backend
Description:
- Search engine integration
- Indexing pipeline
- Query processing system
- Comprehensive testing suite
```

**Sub-task 2: Content Migration**

```
Title: Migrate existing content to new search
Description:
- Data migration scripts
- Index population
- Validation and comparison tools
- Performance testing
```

**Sub-task 3: Frontend Integration**

```
Title: Update search UI and experience
Description:
- New search interface
- Results presentation
- Advanced search features
- User experience testing
```

## Implementation Notes

- **Plan mode required**: Command must fail immediately if not in plan mode (same as /continue-task)
- **Research before questions**: Codebase research happens before asking user questions to ensure
  questions are targeted and informed
- **Interactive planning happens in parent agent**: The main slash command handler conducts the
  interactive discussion with the user and builds structured sub-task data
- **Split operations for planning and execution**:
  - Planning phase: Research codebase, ask questions, present plan with proposed sub-tasks
  - Execution phase: Create sub-tasks in Linear after plan approval
- **Final action is writing to Linear**: This command ends with creating sub-tasks in Linear and
  does NOT begin work on any of them - use `/continue-task` to start working on the sub-tasks
- **CLI tool handles**: Issue resolution, sub-task creation, team ID, cycle inheritance, and all
  Linear API usage
- **Plan mode detection**: Check for `Plan mode is active` in system-reminder tags. If not present,
  output error message and stop immediately.
- **Progress tracking**: Use TodoWrite to track sub-task creation progress during execution

## Integration with Existing Workflow

- **Works with `/continue-task`**: After sub-tasks are created, use `/continue-task` to begin
  working on them
- **Planning only**: This command ends with creating sub-tasks in Linear - it does not start work
- **Tracks in Linear**: Project phases visible in Linear alongside other work
- **Cycle integration**: Sub-tasks inherit cycle from parent issue
- **Label management**: Workspace labels added automatically by `/continue-task`
