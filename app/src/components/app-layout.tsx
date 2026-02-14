import type { Project } from "../types/maelstrom";
import { ProjectList } from "./project-list";
import { AgentList } from "./agent-list";
import { DetailPanel } from "./detail-panel";
import { Button } from "./ui/button";
import { RefreshCw } from "lucide-react";

interface AppLayoutProps {
  projects: Project[];
  selectedProject: Project | null;
  onSelectProject: (project: Project) => void;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

export function AppLayout({
  projects,
  selectedProject,
  onSelectProject,
  loading,
  error,
  onRefresh,
}: AppLayoutProps) {
  if (error) {
    return (
      <div className="flex items-center justify-center h-screen bg-background p-8">
        <div className="text-center max-w-md">
          <h2 className="text-lg font-semibold mb-2">Failed to load data</h2>
          <p className="text-sm text-muted-foreground mb-4">{error}</p>
          <Button onClick={onRefresh} variant="outline">
            <RefreshCw className="h-4 w-4 mr-2" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-background">
      {/* Project sidebar */}
      <div className="w-56 border-r flex flex-col shrink-0">
        <div className="p-3 border-b flex items-center justify-between">
          <span className="font-semibold text-sm">Projects</span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onRefresh}
            disabled={loading}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
        <ProjectList
          projects={projects}
          selectedProject={selectedProject}
          onSelect={onSelectProject}
          loading={loading}
        />
      </div>

      {/* Agent list */}
      <div className="w-80 border-r flex flex-col shrink-0">
        <div className="p-3 border-b">
          <span className="font-semibold text-sm">
            {selectedProject
              ? `${selectedProject.name} — Agents`
              : "Agents"}
          </span>
        </div>
        <AgentList
          worktrees={selectedProject?.worktrees ?? []}
          loading={loading}
        />
      </div>

      {/* Detail panel */}
      <div className="flex-1 flex flex-col min-w-0">
        <DetailPanel />
      </div>
    </div>
  );
}
