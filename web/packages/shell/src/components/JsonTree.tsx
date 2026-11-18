/**
 * JSON from udl — a plugin's settings, an event's data — shown as text: React escapes what a
 * text node holds, so nothing in it can run, and `JSON.stringify` keeps it readable.
 */

export interface JsonTreeProps {
	readonly value: unknown;
	/** What it is, said above it (a label a screen reader reads too). */
	readonly label?: string;
}

/** The value as indented JSON, or what could not be printed as one. */
export function asText(value: unknown): string {
	try {
		return JSON.stringify(value, null, 2) ?? "null";
	} catch {
		// (A circular value, or one whose `toJSON` throws: say so rather than break the page.)
		return "(not printable as JSON)";
	}
}

export function JsonTree({ value, label }: JsonTreeProps) {
	return (
		<figure className="max-w-full">
			{label === undefined ? null : (
				<figcaption className="mb-1 text-xs opacity-60">{label}</figcaption>
			)}
			<pre className="overflow-x-auto rounded-box bg-base-200 p-3 text-xs">{asText(value)}</pre>
		</figure>
	);
}
