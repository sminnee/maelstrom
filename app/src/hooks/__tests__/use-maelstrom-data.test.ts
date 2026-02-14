import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useMaelstromData } from "../use-maelstrom-data";
import { invoke } from "@tauri-apps/api/core";

const mockInvoke = vi.mocked(invoke);

const mockData = {
  projects: [
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
      ],
    },
  ],
};

describe("useMaelstromData", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches data on mount and selects first project", async () => {
    mockInvoke.mockResolvedValue(mockData);

    const { result } = renderHook(() => useMaelstromData());

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.data).toEqual(mockData);
    expect(result.current.selectedProject?.name).toBe("myproject");
    expect(result.current.error).toBeNull();
  });

  it("handles errors gracefully", async () => {
    mockInvoke.mockRejectedValue(new Error("mael not found"));

    const { result } = renderHook(() => useMaelstromData());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe("mael not found");
    expect(result.current.data).toBeNull();
  });
});
