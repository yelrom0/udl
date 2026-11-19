/**
 * ----------------
 * - react.test.ts -
 * ----------------
 *
 * The mount contract, over React: `react(Component)` draws the component into the target with
 * the props it is given, draws it again with the ones `update` brings, and unmounts the root.
 * (The same contract with a plain-DOM mount is `plain.test.ts`'s, in this package's tests.)
 */

import { describe, expect, it } from "vitest";
import type { UIContext } from "./index";
import { react } from "./react";

function Greeting(props: { who: string }) {
	return `Hello, ${props.who}!`;
}

const context: UIContext = {
	api: {},
	plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
	navigate: () => {},
	toast: () => {},
	confirm: async () => true,
	call: async () => null,
	events: { subscribe: () => () => {} },
	theme: { mode: "light", name: "udl", tokens: {}, onChange: () => () => {} },
	stores: {},
};

describe("react(Component)", () => {
	it("draws the component into the target, with its props and the context", () => {
		const target = document.createElement("div");

		const mounted = react(Greeting)(target, { who: "ada" }, context);

		expect(target.textContent).toBe("Hello, ada!");
		mounted.unmount();
	});

	it("draws it again when update brings other props", () => {
		const target = document.createElement("div");

		const mounted = react(Greeting)(target, { who: "ada" }, context);
		mounted.update({ who: "grace" });

		expect(target.textContent).toBe("Hello, grace!");
		mounted.unmount();
	});

	it("empties the target when it unmounts", () => {
		const target = document.createElement("div");

		const mounted = react(Greeting)(target, { who: "ada" }, context);
		mounted.unmount();

		expect(target.innerHTML).toBe("");
	});
});
