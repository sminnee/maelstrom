import { Badge } from "./ui/badge";

interface StatusBadgeProps {
  label: string;
  variant?: "default" | "secondary" | "destructive" | "outline";
}

export function StatusBadge({ label, variant = "secondary" }: StatusBadgeProps) {
  return (
    <Badge variant={variant} className="text-xs">
      {label}
    </Badge>
  );
}
