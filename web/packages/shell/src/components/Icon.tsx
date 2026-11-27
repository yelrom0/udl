/**
 * The icons the shell draws, by the name a manifest gives (`message-square`): a handful of
 * lucide's paths, inline, so the shell needs no icon package of its own. A name the shell does
 * not have draws nothing — a plugin's icon is a hint, never the button's meaning.
 */

/** Each icon's own path, in lucide's 24 × 24 grid. */
const PATHS: Readonly<Record<string, string>> = {
	// (lucide's `message-square`.)
	"message-square": "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
	// (lucide's `panel-right`.)
	"panel-right": "M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 9l-3 3 3 3M4 3h4v18H4z",
	// (lucide's `terminal`.)
	terminal: "m4 17 6-6-6-6M12 19h8",
	// (lucide's `circle-help`.)
	"circle-help":
		"M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01",
	// (lucide's `activity`.)
	activity: "M22 12h-4l-3 9L9 3l-3 9H2",
};

/** `name`'s icon (`null` when the shell does not have it). */
export function Icon({
	name,
	className,
}: {
	readonly name?: string | undefined;
	readonly className?: string | undefined;
}): React.ReactElement | null {
	const path = name === undefined ? undefined : PATHS[name];
	if (path === undefined) {
		return null;
	}
	return (
		<svg
			aria-hidden="true"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
			className={className}
		>
			<path d={path} />
		</svg>
	);
}
