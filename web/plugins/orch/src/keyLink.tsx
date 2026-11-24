/** What `<KeyLink>` is given: what kind of key it is, and the key. */
export interface KeyLinkProps {
	/** What the key names: a task, a feature or a question (the pages the plugin draws). */
	readonly kind: "task" | "feature" | "question";
	readonly label: string;
	/** What it is called, when the page knows: the chip's tooltip. */
	readonly title?: string | undefined;
}

/** The page a key of each kind is drawn at. */
const PAGES: Readonly<Record<KeyLinkProps["kind"], string>> = {
	task: "tasks",
	feature: "features",
	question: "questions",
};

/**
 * A key, as a link to its own page (29.5): `29.2` is a task's, `29` a feature's, `Q-8` a
 * question's — mono, as every key is (Q-8), so a page that names another page can be followed.
 */
export function KeyLink({ kind, label, title }: KeyLinkProps) {
	return (
		<a
			className="link link-hover font-mono"
			href={`/ui/p/orch/${PAGES[kind]}/${encodeURIComponent(label)}`}
			title={title !== undefined && title !== "" ? title : undefined}
		>
			{label}
		</a>
	);
}
