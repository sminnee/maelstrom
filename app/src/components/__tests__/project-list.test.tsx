import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ProjectList } from "../project-list";
import type { Project } from "../../types/maelstrom";

const mockProjects: Project[] = [
  {
    name: "myproject",
    path: "/projects/myproject",
    worktrees: [
      {
        name: "alpha",
        folder: "myproject-alpha",
        path: "/projects/myproject/myproject-alpha",
        branch: "main",
        is_closed: false,
        dirty_files: 0,
        local_commits: 0,
        pr_number: null,
        pr_commits: null,
        pushed_commits: null,
        ide_active: false,
      },
      {
        name: "bravo",
        folder: "myproject-bravo",
        path: "/projects/myproject/myproject-bravo",
        branch: null,
        is_closed: true,
        dirty_files: 0,
        local_commits: 0,
        pr_number: null,
        pr_commits: null,
        pushed_commits: null,
        ide_active: false,
      },
    ],
  },
  {
    name: "other",
    path: "/projects/other",
    worktrees: [],
  },
];

describe("ProjectList", () => {
  it("renders project names", () => {
    render(
      <ProjectList
        projects={mockProjects}
        selectedProject={null}
        onSelect={vi.fn()}
        loading={false}
      />,
    );

    expect(screen.getByText("myproject")).toBeInTheDocument();
    expect(screen.getByText("other")).toBeInTheDocument();
  });

  it("shows active worktree count badge", () => {
    render(
      <ProjectList
        projects={mockProjects}
        selectedProject={null}
        onSelect={vi.fn()}
        loading={false}
      />,
    );

    // myproject has 1 active worktree (alpha is open, bravo is closed)
    expect(screen.getByText("1")).toBeInTheDocument();
    // other has 0 worktrees
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("calls onSelect when clicking a project", async () => {
    const onSelect = vi.fn();
    render(
      <ProjectList
        projects={mockProjects}
        selectedProject={null}
        onSelect={onSelect}
        loading={false}
      />,
    );

    await userEvent.click(screen.getByText("myproject"));
    expect(onSelect).toHaveBeenCalledWith(mockProjects[0]);
  });

  it("shows loading skeletons when loading", () => {
    const { container } = render(
      <ProjectList
        projects={[]}
        selectedProject={null}
        onSelect={vi.fn()}
        loading={true}
      />,
    );

    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0);
  });

  it("shows empty state when no projects", () => {
    render(
      <ProjectList
        projects={[]}
        selectedProject={null}
        onSelect={vi.fn()}
        loading={false}
      />,
    );

    expect(screen.getByText("No projects found")).toBeInTheDocument();
  });
});
