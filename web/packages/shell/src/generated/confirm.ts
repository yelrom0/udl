/**
 * -----------------
 * - confirm.ts -
 * -----------------
 *
 * What a destructive operation's `confirm_field` must say before its button enables. udl names
 * the field but not what it must equal (a card's operation may want `owner/repo#number`, which no
 * field holds), so a generated page asks for the one thing it can: the name of the thing the
 * operation acts on — the value of the request's `name`, `key`, `account`, `site`, `repo` or
 * `id` field. An operation that names none of those has the typing itself for a confirmation.
 */

import type { JsonObject } from "@udl/ui-sdk";

/** The fields a request names the thing it acts on with, the likeliest first. */
export const TARGETS: readonly string[] = ["name", "key", "account", "site", "repo", "id"];

/** What a confirm field must say, and whether it says it. */
export interface Confirmation {
	/** The thing's name, as the form holds it (`null`: any text confirms). */
	readonly target: string | null;
	readonly given: string;
	readonly ok: boolean;
}

/** What `confirm` must say, given the form's `values`. */
export function confirmation(
	confirm: string,
	values: JsonObject,
	fields: readonly string[],
): Confirmation {
	const named = TARGETS.find((name) => name !== confirm && fields.includes(name));
	const given = typeof values[confirm] === "string" ? values[confirm] : "";
	if (named === undefined) {
		// (Nothing to repeat: typing anything at all is the confirmation.)
		return { target: null, given, ok: given.trim() !== "" };
	}
	const value = values[named];
	const target = typeof value === "string" ? value : "";
	// (A name that is not there yet cannot be repeated, and "" does not confirm anything.)
	return { target, given, ok: target !== "" && given === target };
}
