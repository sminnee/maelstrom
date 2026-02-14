import { Card, CardContent } from "./ui/card";

export function DetailPanel() {
  return (
    <div className="flex items-center justify-center h-full p-8">
      <Card className="max-w-md w-full">
        <CardContent className="pt-6 text-center">
          <h2 className="text-lg font-semibold mb-2">Coming Soon</h2>
          <p className="text-muted-foreground text-sm">
            Agent details will appear here in a future update.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
