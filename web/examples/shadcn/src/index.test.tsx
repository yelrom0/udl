/**
 * The example's own guard: what its manifest contributes is what its entry has, and both
 * contributions draw. (shadcn's own components are its library's, so they are not counted here.)
 */

import type { JsonValue } from "@udl/ui-sdk";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import manifest from "../manifest.json";
import { Gallery } from "./gallery";
import plugin from "./index";
import { Panel } from "./panel";
import { ResultView } from "./resultView";

let target: HTMLDivElement | null = null;

/** `element`, drawn into a fresh div, as a plugin's mount draws it into the shell's. */
function draw(element: React.ReactElement): HTMLDivElement {
	target = document.createElement("div");
	document.body.append(target);
	const root = createRoot(target);
	act(() => root.render(element));
	return target;
}

afterEach(() => {
	target?.remove();
	target = null;
});

describe("the shadcn example", () => {
	it("names a mount for every contribution its manifest declares", () => {
		expect(Object.keys(plugin.routes ?? {})).toEqual(manifest.contributes.routes);
		expect(Object.keys(plugin.widgets ?? {})).toEqual(
			manifest.contributes.widgets.map((widget) => widget.id),
		);
		expect(Object.keys(plugin.resultViews ?? {})).toEqual(manifest.contributes.resultViews);
	});

	it("draws its page, with shadcn's card and buttons", () => {
		const drawn = draw(<Gallery path="gallery" params={{}} search={{}} />);

		expect(drawn.textContent).toContain("shadcn/ui, in a udl UI plugin");
		expect(drawn.querySelector("[data-testid='shadcn-card']")).not.toBeNull();
		expect(drawn.querySelector("[data-testid='shadcn-button']")?.textContent).toBe("Default");
	});

	it("draws a list's result with its own card, not udl's table", () => {
		const drawn = draw(
			<ResultView
				operation={{
					name: "fake_list_things",
					title: "List the things",
					summary: "",
					view: "list",
					hints: {},
				}}
				value={{
					items: [{ account: "fake:work", item: { id: "t1", text: "The first thing" } }],
				}}
			/>,
		);

		expect(drawn.textContent).toContain("Drawn by shadcn/ui, not by udl.");
		expect(drawn.textContent).toContain("The first thing");
		expect(drawn.querySelector("[data-testid='shadcn-list']")).not.toBeNull();
	});

	it("draws no thing it cannot read", () => {
		const operation = {
			name: "fake_list_things",
			title: "List the things",
			summary: "",
			view: "list" as const,
			hints: {},
		};
		const values: JsonValue[] = [
			// (Not an answer at all, and an answer whose items are not a list.)
			null,
			{ items: "nope" },
			// (Items that are not objects, an item that is not one, one with no text, a bare thing.)
			{
				items: [null, "x", { item: "x" }, { item: null }, { item: { id: "t2" } }, { id: "t3" }],
			},
		];

		for (const value of values) {
			const drawn = draw(<ResultView operation={operation} value={value} />);

			expect(drawn.querySelectorAll("li")).toHaveLength(0);
		}
	});

	it("draws its widget, with the slot it was given", () => {
		const drawn = draw(
			<Panel
				slot="dashboard"
				plugin={{ name: "shadcn", version: "0.1.0", baseUrl: "/ui/plugins/shadcn/" }}
			/>,
		);

		expect(drawn.textContent).toContain("shadcn/ui");
		expect(drawn.querySelector("[data-testid='shadcn-widget-button']")?.textContent).toBe(
			"dashboard",
		);
	});
});
