import { describe, expect, it } from "vitest";
import { asError, given, isSaid } from "./api";

describe("given", () => {
	it("hands over what udl answered", () => {
		expect(given({ data: { sites: [] }, response: new Response() })).toEqual({ sites: [] });
	});

	it("throws udl's error, with its hint", () => {
		const thrown = capture(() =>
			given({
				error: { code: "udl_error", message: "No such site", hint: "List them first." },
				response: new Response(null, { status: 404 }),
			}),
		);

		expect(thrown.message).toBe("No such site (List them first.)");
	});

	it("throws udl's error without a hint as it is", () => {
		const thrown = capture(() =>
			given({ error: { code: "udl_error", message: "No such site" }, response: new Response() }),
		);

		expect(thrown.message).toBe("No such site");
	});

	it("says an answer that was neither is one", () => {
		expect(capture(() => given({ response: new Response() })).message).toBe("udl gave no answer");
	});
});

describe("asError", () => {
	it("says what udl answered when the error is not one of its own", () => {
		const thrown = asError({ detail: [{ msg: "not a URL" }] }, new Response(null, { status: 422 }));

		expect(thrown.message).toBe('udl answered 422: {"detail":[{"msg":"not a URL"}]}');
	});

	it("says so when there was no answer at all", () => {
		expect(asError(null).message).toBe("udl answered 0: null");
	});

	it("says a body that is not quite udl's error as what it answered", () => {
		expect(asError({ message: "Bad", hint: 5 }).message).toBe(
			'udl answered 0: {"message":"Bad","hint":5}',
		);
	});
});

describe("isSaid", () => {
	it("knows udl's error from other answers", () => {
		expect(isSaid({ message: "Bad" })).toBe(true);
		expect(isSaid({ message: "Bad", hint: null })).toBe(true);
		expect(isSaid({ message: "Bad", hint: "Why" })).toBe(true);
		expect(isSaid({ message: "Bad", hint: 5 })).toBe(false);
		expect(isSaid({ message: 5 })).toBe(false);
		expect(isSaid({ detail: [] })).toBe(false);
		expect(isSaid("Bad")).toBe(false);
		expect(isSaid(null)).toBe(false);
	});
});

/** The error a function throws (the test's own way of looking at it). */
function capture(act: () => unknown): Error {
	try {
		act();
	} catch (error) {
		if (error instanceof Error) {
			return error;
		}
		throw error;
	}
	throw new Error("nothing was thrown");
}
