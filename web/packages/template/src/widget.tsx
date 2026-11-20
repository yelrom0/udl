import type { WidgetProps } from "@udl/ui-sdk";

/** The plugin's dashboard widget. */
export function Widget({ slot }: WidgetProps) {
	return (
		<div className="card bg-base-200 rounded-box p-4">
			<h2 className="card-title text-base">A widget</h2>
			<p className="text-sm opacity-70">Drawn into the {slot} slot.</p>
		</div>
	);
}
