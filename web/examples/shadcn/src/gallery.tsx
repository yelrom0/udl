import type { RouteProps } from "@udl/ui-sdk";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/** The plugin's page: shadcn/ui's button and card, styled by its own Tailwind build. */
export function Gallery({ path }: RouteProps) {
	return (
		<div className="flex flex-col gap-6 p-6">
			<h1 className="text-2xl font-bold text-foreground">shadcn/ui, in a udl UI plugin</h1>
			<Card data-testid="shadcn-card">
				<CardHeader>
					<CardTitle>A card of its own</CardTitle>
					<CardDescription>
						Tailwind 4, scanning only this plugin's files, with shadcn's tokens mapped onto
						daisyUI's.
					</CardDescription>
				</CardHeader>
				<CardContent className="flex gap-2">
					<Button data-testid="shadcn-button">Default</Button>
					<Button variant="secondary">Secondary</Button>
					<Button variant="outline">Outline</Button>
				</CardContent>
			</Card>
			<p className="text-sm text-muted-foreground" data-testid="shadcn-page">
				This page is {path}.
			</p>
		</div>
	);
}
