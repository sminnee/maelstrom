import type { Worktree } from "../types/maelstrom";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { StatusBadge } from "./status-badge";
import { cn } from "../lib/utils";

interface AgentCardProps {
  worktree: Worktree;
}

export function AgentCard({ worktree }: AgentCardProps) {
  const isClosed = worktree.is_closed;

  return (
    <Card className={cn("transition-colors", isClosed && "opacity-50")}>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center justify-between text-base">
          <span className="font-semibold capitalize">{worktree.name}</span>
          {worktree.ide_active && (
            <span className="h-2 w-2 rounded-full bg-green-500" title="IDE active" />
          )}
        </CardTitle>
        <p className="text-sm text-muted-foreground font-mono">
          {isClosed ? "(closed)" : worktree.branch ?? "(detached)"}
        </p>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-1.5">
          {worktree.dirty_files > 0 && (
            <StatusBadge
              label={`${worktree.dirty_files} dirty`}
              variant="destructive"
            />
          )}
          {worktree.local_commits > 0 && (
            <StatusBadge
              label={`${worktree.local_commits} local`}
              variant="secondary"
            />
          )}
          {worktree.pr_number != null && (
            <StatusBadge
              label={`PR #${worktree.pr_number}`}
              variant="default"
            />
          )}
          {worktree.pr_number == null && worktree.pushed_commits != null && worktree.pushed_commits > 0 && (
            <StatusBadge
              label={`${worktree.pushed_commits} pushed`}
              variant="outline"
            />
          )}
        </div>
      </CardContent>
    </Card>
  );
}
