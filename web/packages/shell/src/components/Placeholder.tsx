/**
 * A page Phases 19 and 20 fill: it says what it will be, so the shell's every route is
 * reachable now and the nav's shape is real.
 */

export interface PlaceholderProps {
	readonly title: string;
	/** What the page will do, and which task builds it. */
	readonly note: string;
}

export function Placeholder({ title, note }: PlaceholderProps) {
	return (
		<section className="my-6">
			<h1 className="text-2xl font-bold">{title}</h1>
			<p className="mt-2 opacity-70">{note}</p>
		</section>
	);
}
