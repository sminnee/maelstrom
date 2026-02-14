import type { Worktree } from "../types/maelstrom";
import { AgentCard } from "./agent-card";
import { ScrollArea } from "./ui/scroll-area";
import { Skeleton } from "./ui/skeleton";

interface AgentListProps {
  worktrees: Worktree[];
  loading: boolean;
}

export function AgentList({ worktrees, loading }: AgentListProps) {
  if (loading) {
    return (
      <div className="p-4 space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (worktrees.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground p-4">
        No agents found
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-4 space-y-3">
        {worktrees.map((wt) => (
          <AgentCard key={wt.folder} worktree={wt} />
        ))}
      </div>
    </ScrollArea>
  );
}
