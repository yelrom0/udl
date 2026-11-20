/**
 * -------------------
 * - confirm.test.ts -
 * -------------------
 *
 * A destructive operation's confirm field: the name of the thing it acts on, or (when the
 * request names nothing) any text at all.
 */

import { describe, expect, it } from "vitest";
import { confirmation } from "./confirm";

describe("a confirm field", () => {
	it("must equal the name the request gives the thing", () => {
		const fields = ["key", "confirm"];

		expect(confirmation("confirm", { key: "sites", confirm: "" }, fields).ok).toBe(false);
		expect(confirmation("confirm", { key: "sites", confirm: "site" }, fields).ok).toBe(false);
		expect(confirmation("confirm", { key: "sites", confirm: "sites" }, fields)).toEqual({
			target: "sites",
			given: "sites",
			ok: true,
		});
	});

	it("takes the first name the request has, in the order the page knows them", () => {
		// (`account` before `site`, whichever order the fields are in.)
		const fields = ["site", "account", "confirm"];

		expect(confirmation("confirm", { site: "s", account: "a", confirm: "a" }, fields).ok).toBe(
			true,
		);
	});

	it("is the typing itself when the request names nothing to repeat", () => {
		expect(confirmation("confirm", { confirm: "" }, ["confirm"]).ok).toBe(false);
		expect(confirmation("confirm", { confirm: "  " }, ["confirm"]).ok).toBe(false);
		expect(confirmation("confirm", { confirm: "yes" }, ["confirm"])).toEqual({
			target: null,
			given: "yes",
			ok: true,
		});
	});

	it("does not count a name that is not there yet", () => {
		// (The target field is empty: there is nothing to repeat, so any text does not do.)
		expect(confirmation("confirm", { name: "", confirm: "x" }, ["name", "confirm"]).ok).toBe(false);
	});
});
