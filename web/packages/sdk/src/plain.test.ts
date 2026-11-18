/**
 * ----------------
 * - plain.test.ts -
 * ----------------
 *
 * The mount contract without React, as a plugin in any framework (or none) does it: a mount is
 * a function of the target, the props and the context, which returns `update` and `unmount`.
 * The SDK's types let a plain-DOM mount be passed where a mount is expected.
 */

import { describe, expect, it } from "vitest";
import type { Mount, UIContext, WidgetProps } from "./index";

const mount: Mount<WidgetProps> = (target, props) => {
	const draw = (who: string) => {
		target.textContent = who;
	};
	draw(props.plugin.name);
	return {
		update: (next) => draw(next.plugin.name),
		unmount: () => {
			target.textContent = "";
		},
	};
};

const context: UIContext = {
	api: {},
	plugin: { name: "gmail", version: "0.1.0", baseUrl: "/ui/plugins/gmail/" },
	navigate: () => {},
	toast: () => {},
	confirm: async () => true,
	call: async () => null,
	events: { subscribe: () => () => {} },
	theme: {
		mode: "light",
		name: "udl",
		tokens: {},
		onChange: () => () => {},
	},
	stores: {},
};

describe("a plain-DOM mount", () => {
	it("draws, updates and unmounts, as the contract says", () => {
		const target = document.createElement("div");

		const mounted = mount(target, { slot: "dashboard", plugin: context.plugin }, context);
		expect(target.textContent).toBe("gmail");

		mounted.update({ slot: "dashboard", plugin: { ...context.plugin, name: "slack" } });
		expect(target.textContent).toBe("slack");

		mounted.unmount();
		expect(target.textContent).toBe("");
	});
});
