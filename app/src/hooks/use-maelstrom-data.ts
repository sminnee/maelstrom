import { useCallback, useEffect, useState } from "react";
import { listAll } from "../commands/list-all";
import type { ListAllResponse, Project } from "../types/maelstrom";

interface MaelstromState {
  data: ListAllResponse | null;
  selectedProject: Project | null;
  loading: boolean;
  error: string | null;
}

export function useMaelstromData() {
  const [state, setState] = useState<MaelstromState>({
    data: null,
    selectedProject: null,
    loading: true,
    error: null,
  });

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const data = await listAll();
      setState((prev) => ({
        ...prev,
        data,
        loading: false,
        selectedProject:
          prev.selectedProject ??
          (data.projects.length > 0 ? data.projects[0] : null),
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err.message : String(err),
      }));
    }
  }, []);

  const selectProject = useCallback((project: Project) => {
    setState((prev) => ({ ...prev, selectedProject: project }));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { ...state, refresh, selectProject };
}
