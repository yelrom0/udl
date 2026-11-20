import type { RouteProps } from "@udl/ui-sdk";

/** The plugin's page: daisyUI's classes, drawn in the plugin's own shadow root. (The shell gives
 * it the context too, as `ctx`, if it wants it: this page needs only its route.) */
export function Page({ path }: RouteProps) {
	return (
		<div className="flex flex-col gap-4 p-6">
			<h1 className="text-2xl font-bold">Hello from a UI plugin</h1>
			<p className="text-sm opacity-70">This page is {path}.</p>
			<div className="card bg-base-200 rounded-box p-4">
				<p>A card, in the page's theme.</p>
				<button type="button" className="btn btn-primary btn-sm mt-2">
					A daisyUI button
				</button>
			</div>
		</div>
	);
}
