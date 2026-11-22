import type { PullState } from "./model";

/** What `<LinkChip>` is given: what it points at, and what is known about it. */
export interface LinkChipProps {
	/** The link's kind (26.4's `LinkKind`). */
	readonly kind: string;
	/** Its canonical URL (`udl:` for an internal kind). */
	readonly url: string;
	/** What it is called, when it has been seen. */
	readonly title?: string | undefined;
	/** What is known about it (for a pull: its checks and its review). */
	readonly state?: PullState | undefined;
}

/** The plugin's own pages, by the kind a `udl:` URL names. */
const OWN: Readonly<Record<string, string>> = {
	task: "tasks",
	feature: "features",
	question: "questions",
};

/** The last part of `url`'s path (`…/pull/78` → `78`). */
function tail(url: string): string {
	const parts = url.split("/").filter((part) => part !== "");
	return parts[parts.length - 1] ?? url;
}

/** A pull's or an issue's number, from its URL. */
function numberOf(url: string): string {
	const found = /\/(?:pull|issues)\/(\d+)/.exec(url);
	return found?.[1] ?? tail(url);
}

/** Where `url` lives (`example.com`), or the URL itself when it is not one. */
function hostOf(url: string): string {
	try {
		return new URL(url).host;
	} catch {
		return url;
	}
}

/** A branch's name, from its URL (`…/tree/feat/29.2` → `feat/29.2`): it may hold slashes. */
function branchOf(url: string): string {
	return /\/tree\/(.+)$/.exec(url)?.[1] ?? tail(url);
}

/** A file's path, from its URL (`…/blob/main/src/x.py` → `src/x.py`). */
function fileOf(url: string): string {
	return /\/blob\/[^/]+\/(.+)$/.exec(url)?.[1] ?? tail(url);
}

/** A `udl:` URL's own page: `udl:task/udl/29.2` → `/ui/p/orch/tasks/29.2`. */
function ownPage(kind: string, url: string): string {
	const key = tail(url);
	return `/ui/p/orch/${OWN[kind] ?? "tasks"}/${encodeURIComponent(key)}`;
}

/** Whether the chip's URL is the shell's own (`udl:`): its page is drawn, not opened. */
function isOwn(url: string): boolean {
	return url.startsWith("udl:");
}

/** The dot a pull's checks are shown as: pass, fail, pending, or none. */
function checksOf(checks: string | undefined): string {
	return checks === "pass" || checks === "fail" || checks === "pending" ? checks : "none";
}

/** The mark a review's word is shown as: approved, changes, pending, or none. */
const REVIEW_MARKS: Readonly<Record<string, string>> = {
	approved: "✓",
	changes: "✎",
	pending: "…",
	none: "",
};

/** What a chip says, by its kind: a key, a number, a SHA, a name, or a title. */
function labelOf({ kind, url, title }: LinkChipProps): string {
	switch (kind) {
		case "pull":
		case "issue":
			return `#${numberOf(url)}`;
		case "commit":
			return tail(url).slice(0, 7);
		case "branch":
			return branchOf(url);
		case "file":
			return fileOf(url);
		case "task":
		case "feature":
		case "question":
			return tail(url);
		case "ci_run":
			return `run ${tail(url)}`;
	}
	// (A doc, a URL, a stitch screen: what it is called, or where it lives.)
	if (title !== undefined && title !== "") {
		return title;
	}
	return hostOf(url);
}

/** The kinds whose label is a key, a SHA or a name: always monospace (Q-8). */
const MONO: readonly string[] = [
	"pull",
	"issue",
	"commit",
	"branch",
	"file",
	"ci_run",
	"task",
	"feature",
	"question",
];

/**
 * A link, in one look per kind (26.4's kinds): a pull shows its number, its checks' dot and the
 * review bot's mark, and is a real link to GitHub; a task, a feature and a question point at the
 * plugin's own pages; a commit, a branch and a file show their name in mono.
 */
export function LinkChip({ kind, url, title, state }: LinkChipProps) {
	const label = labelOf({ kind, url, title, state });
	const own = isOwn(url);
	const checks = kind === "pull" ? checksOf(state?.checks) : "";
	const review = kind === "pull" ? (REVIEW_MARKS[state?.review ?? "none"] ?? "") : "";
	return (
		<a
			className="badge badge-outline gap-1 border-base-content/20 align-middle"
			href={own ? ownPage(kind, url) : url}
			title={title !== undefined && title !== "" ? title : undefined}
			{...(own ? {} : { target: "_blank", rel: "noopener noreferrer" })}
		>
			<span className={MONO.includes(kind) ? "font-mono" : undefined}>{label}</span>
			{checks !== "" && (
				<span
					className={`checks-${checks} size-2 rounded-full ${
						checks === "pass"
							? "bg-success"
							: checks === "fail"
								? "bg-error"
								: checks === "pending"
									? "bg-warning"
									: "bg-base-content/20"
					}`}
					role="img"
					aria-label={`checks ${checks}`}
				/>
			)}
			{review !== "" && (
				<span role="img" aria-label={`review ${state?.review ?? ""}`}>
					{review}
				</span>
			)}
			{!own && (
				<span aria-hidden="true" className="opacity-60">
					↗
				</span>
			)}
		</a>
	);
}
