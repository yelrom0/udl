/**
 * The composer's completions (29.12): `@` names a task, a feature or a question — the things a
 * chat is asked about — and `/` a command the harness itself understands. Both are pure, so the
 * composer's rules are tested without a DOM.
 */

/** What the caret is in the middle of: the token, where it starts, and what it asks for. */
export interface Completing {
	/** What is typed after the `@` or `/`. */
	readonly token: string;
	/** Where the `@` or `/` is in the text. */
	readonly at: number;
	/** Which completion it is. */
	readonly kind: "@" | "/";
}

/** Whether `character` ends a token (a space, a newline, or the start of the text). */
function endsToken(character: string | undefined): boolean {
	return character === undefined || /\s/.test(character);
}

/**
 * The completion `caret` is in the middle of, when it is in one: the `@…` or `/…` a person is
 * typing. A `/` completes only at the start of the message — the harness reads a command only
 * there — and a word inside another word (`me@example`) is not a completion.
 */
export function completing(text: string, caret: number): Completing | null {
	if (caret < 0 || caret > text.length) {
		return null;
	}
	const before = text.slice(0, caret);
	let start = caret;
	while (start > 0 && !endsToken(before[start - 1])) {
		start -= 1;
	}
	const word = before.slice(start);
	const first = word[0];
	if (first !== "@" && first !== "/") {
		return null;
	}
	// (A completion starts a word: `me@example` is an address, not a task.)
	if (!endsToken(before[start - 1])) {
		return null;
	}
	if (first === "/" && start !== 0) {
		return null;
	}
	return { token: word.slice(1), at: start, kind: first };
}

/** `text` with the completion at `caret` replaced by `insert`, and where the caret then is. */
export function completed(
	text: string,
	caret: number,
	insert: string,
): { readonly text: string; readonly caret: number } {
	const found = completing(text, caret);
	if (found === null) {
		return { text, caret };
	}
	const before = text.slice(0, found.at);
	const after = text.slice(caret);
	// (A space after the key, unless one is already there.)
	const gap = after.startsWith(" ") ? "" : " ";
	return {
		text: `${before}${insert}${gap}${after}`,
		caret: before.length + insert.length + gap.length,
	};
}

/** The keys that could complete `token`, in the order given (all of them when it is empty). */
export function matching(token: string, keys: readonly string[]): string[] {
	if (token === "") {
		return [...keys];
	}
	const wanted = token.toLowerCase();
	return keys.filter((key) => key.toLowerCase().includes(wanted));
}

/** The harness's own commands (the ones udl knows of), for the `/` completion. */
export const COMMANDS: readonly string[] = [
	"/clear",
	"/compact",
	"/cost",
	"/help",
	"/model",
	"/status",
];
