import type { Project } from "../types/maelstrom";
import { Badge } from "./ui/badge";
import { ScrollArea } from "./ui/scroll-area";
import { Skeleton } from "./ui/skeleton";
import { cn } from "../lib/utils";

interface ProjectListProps {
  projects: Project[];
  selectedProject: Project | null;
  onSelect: (project: Project) => void;
  loading: boolean;
}

export function ProjectList({
  projects,
  selectedProject,
  onSelect,
  loading,
}: ProjectListProps) {
  if (loading) {
    return (
      <div className="p-3 space-y-2">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }

  if (projects.length === 0) {
    return (
      <div className="p-3 text-sm text-muted-foreground">
        No projects found
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-1">
        {projects.map((project) => {
          const isSelected = selectedProject?.name === project.name;
          const activeCount = project.worktrees.filter(
            (wt) => !wt.is_closed,
          ).length;

          return (
            <button
              key={project.name}
              type="button"
              onClick={() => onSelect(project)}
              className={cn(
                "w-full flex items-center justify-between rounded-md px-3 py-2 text-sm transition-colors text-left",
                isSelected
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-accent/50",
              )}
            >
              <span className="font-medium truncate">{project.name}</span>
              <Badge variant="secondary" className="ml-2 text-xs shrink-0">
                {activeCount}
              </Badge>
            </button>
          );
        })}
      </div>
    </ScrollArea>
  );
}
