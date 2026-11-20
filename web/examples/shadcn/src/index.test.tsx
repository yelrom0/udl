/**
 * The example's own guard: what its manifest contributes is what its entry has, and both
 * contributions draw. (shadcn's own components are its library's, so they are not counted here.)
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import manifest from "../manifest.json";
import { Gallery } from "./gallery";
import plugin from "./index";
import { Panel } from "./panel";

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
	});

	it("draws its page, with shadcn's card and buttons", () => {
		const drawn = draw(<Gallery path="gallery" params={{}} search={{}} />);

		expect(drawn.textContent).toContain("shadcn/ui, in a udl UI plugin");
		expect(drawn.querySelector("[data-testid='shadcn-card']")).not.toBeNull();
		expect(drawn.querySelector("[data-testid='shadcn-button']")?.textContent).toBe("Default");
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
