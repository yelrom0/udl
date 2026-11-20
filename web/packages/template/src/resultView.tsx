import type { ResultViewProps } from "@udl/ui-sdk";

/** One operation's result, drawn by this plugin (24.7's `resultViews`). */
export function ResultView({ operation, value }: ResultViewProps) {
	return (
		<div className="card bg-base-200 rounded-box p-4">
			<h2 className="card-title text-base">{operation.title}, drawn by this plugin</h2>
			<pre className="overflow-x-auto text-xs">{JSON.stringify(value, null, 2)}</pre>
		</div>
	);
}
