import type { ResultViewProps } from "@udl/ui-sdk";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** One thing a list answered, as much of it as this draws: the account it came from is part of
 * its identity (every account lists the same ids). */
interface Thing {
	readonly account: string;
	readonly id: string;
	readonly text: string;
}

/** The fake's list, drawn with shadcn's card instead of udl's table (24.8's proof). */
export function ResultView({ operation, value }: ResultViewProps) {
	return (
		<Card data-testid="shadcn-list">
			<CardHeader>
				<CardTitle>{operation.title}</CardTitle>
			</CardHeader>
			<CardContent className="flex flex-col gap-2">
				<p className="text-sm text-muted-foreground">Drawn by shadcn/ui, not by udl.</p>
				<ul className="list-disc pl-4 text-sm">
					{thingsOf(value).map((thing) => (
						<li key={`${thing.account}:${thing.id}`}>{thing.text}</li>
					))}
				</ul>
			</CardContent>
		</Card>
	);
}

/** The things in a fan-out list's answer (`{items: [{account, item: {id, text}}]}`), as many as
 * it can read: udl runs the list on each account and says which account each thing came from. */
function thingsOf(value: unknown): Thing[] {
	if (typeof value !== "object" || value === null) {
		return [];
	}
	const items = (value as { items?: unknown }).items;
	if (!Array.isArray(items)) {
		return [];
	}
	return items.flatMap((found: unknown): Thing[] => {
		if (typeof found !== "object" || found === null) {
			return [];
		}
		const { account, item } = found as { account?: unknown; item?: unknown };
		if (typeof account !== "string" || typeof item !== "object" || item === null) {
			return [];
		}
		const one = item as Record<string, unknown>;
		return typeof one.id === "string" && typeof one.text === "string"
			? [{ account, id: one.id, text: one.text }]
			: [];
	});
}
