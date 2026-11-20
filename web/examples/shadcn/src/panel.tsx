import type { WidgetProps } from "@udl/ui-sdk";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** The plugin's dashboard widget: the same components, in the dashboard's slot. */
export function Panel({ slot }: WidgetProps) {
	return (
		<Card data-testid="shadcn-widget">
			<CardHeader>
				<CardTitle>shadcn/ui</CardTitle>
			</CardHeader>
			<CardContent className="flex flex-col gap-2">
				<p className="text-sm text-muted-foreground">
					A widget drawn by a plugin's own components.
				</p>
				<Button variant="outline" data-testid="shadcn-widget-button">
					{slot}
				</Button>
			</CardContent>
		</Card>
	);
}
