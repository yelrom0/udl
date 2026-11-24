/**
 * The composer's completions (29.12): `@` a key, `/` a command — and what a person's typing is
 * not one.
 */

import { describe, expect, it } from "vitest";
import { COMMANDS, completed, completing, matching } from "./keys";

describe("the @ completion", () => {
	it("is the token being typed, from its @", () => {
		expect(completing("look at @29.1", 13)).toEqual({ token: "29.1", at: 8, kind: "@" });
		expect(completing("@", 1)).toEqual({ token: "", at: 0, kind: "@" });
	});

	it("is in the middle of a sentence too", () => {
		expect(completing("why is @29.1 stuck?", 12)).toEqual({ token: "29.1", at: 7, kind: "@" });
	});

	it("is not an email address", () => {
		expect(completing("mail me@example.com", 19)).toBeNull();
	});

	it("is not a word that has no @ at all", () => {
		expect(completing("plain text", 10)).toBeNull();
	});

	it("is not past the end of the text", () => {
		expect(completing("@29.1", 9)).toBeNull();
		expect(completing("@29.1", -1)).toBeNull();
	});
});

describe("the / completion", () => {
	it("is the command being typed at the start of the message", () => {
		expect(completing("/comp", 5)).toEqual({ token: "comp", at: 0, kind: "/" });
	});

	it("is not a command in the middle of a message", () => {
		expect(completing("please run /comp", 16)).toBeNull();
	});

	it("is not a path", () => {
		expect(completing("in /work/udl", 12)).toBeNull();
	});
});

describe("completing a token", () => {
	it("replaces what was typed with the key, and a space after it", () => {
		const typed = "why is @29 s";
		const done = completed(typed, 10, "@29.1");

		expect(done.text).toBe("why is @29.1 s");
		expect(done.caret).toBe(12);
	});

	it("leaves the text alone when there is nothing to complete", () => {
		expect(completed("plain", 5, "@29.1")).toEqual({ text: "plain", caret: 5 });
	});

	it("keeps what came after the caret, and does not add a second space", () => {
		const done = completed("a @29 b", 5, "@29.1");

		expect(done.text).toBe("a @29.1 b");
		expect(done.caret).toBe(7);
	});
});

describe("what matches a token", () => {
	const keys = ["29.1", "29.10", "28.4"];

	it("is every key when nothing is typed", () => {
		expect(matching("", keys)).toEqual(keys);
	});

	it("is the keys that hold what was typed, in any case", () => {
		expect(matching("29.1", keys)).toEqual(["29.1", "29.10"]);
		expect(matching("Q-8", ["q-8", "Q-9"])).toEqual(["q-8"]);
	});

	it("is nothing when nothing holds it", () => {
		expect(matching("zz", keys)).toEqual([]);
	});
});

describe("the harness's commands", () => {
	it("are the ones udl knows of, each with its slash", () => {
		expect(COMMANDS.length).toBeGreaterThan(3);
		for (const command of COMMANDS) {
			expect(command.startsWith("/")).toBe(true);
			expect(command).not.toContain(" ");
		}
	});
});
