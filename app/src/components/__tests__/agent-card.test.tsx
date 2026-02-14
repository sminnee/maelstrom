import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { AgentCard } from "../agent-card";
import type { Worktree } from "../../types/maelstrom";

const baseWorktree: Worktree = {
  name: "alpha",
  folder: "myproject-alpha",
  path: "/projects/myproject/myproject-alpha",
  branch: "feat/test",
  is_closed: false,
  dirty_files: 0,
  local_commits: 0,
  pr_number: null,
  pr_commits: null,
  pushed_commits: null,
  ide_active: false,
};

describe("AgentCard", () => {
  it("renders worktree name and branch", () => {
    render(<AgentCard worktree={baseWorktree} />);

    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("feat/test")).toBeInTheDocument();
  });

  it("shows (closed) for closed worktrees", () => {
    render(
      <AgentCard worktree={{ ...baseWorktree, is_closed: true, branch: null }} />,
    );

    expect(screen.getByText("(closed)")).toBeInTheDocument();
  });

  it("shows dirty files badge", () => {
    render(<AgentCard worktree={{ ...baseWorktree, dirty_files: 3 }} />);

    expect(screen.getByText("3 dirty")).toBeInTheDocument();
  });

  it("shows local commits badge", () => {
    render(<AgentCard worktree={{ ...baseWorktree, local_commits: 2 }} />);

    expect(screen.getByText("2 local")).toBeInTheDocument();
  });

  it("shows PR badge", () => {
    render(
      <AgentCard worktree={{ ...baseWorktree, pr_number: 42, pr_commits: 5 }} />,
    );

    expect(screen.getByText("PR #42")).toBeInTheDocument();
  });

  it("shows pushed commits when no PR", () => {
    render(<AgentCard worktree={{ ...baseWorktree, pushed_commits: 3 }} />);

    expect(screen.getByText("3 pushed")).toBeInTheDocument();
  });

  it("shows IDE active indicator", () => {
    render(<AgentCard worktree={{ ...baseWorktree, ide_active: true }} />);

    expect(screen.getByTitle("IDE active")).toBeInTheDocument();
  });
});
