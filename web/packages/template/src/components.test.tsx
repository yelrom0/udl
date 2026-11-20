/**
 * The template's page and widget, drawn: a starting point that does not render is not one.
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { Page } from "./page";
import { ResultView } from "./resultView";
import { Widget } from "./widget";

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

describe("the template's contributions", () => {
	it("draws its page, with the route it was given", () => {
		const drawn = draw(<Page path="index" params={{}} search={{}} />);

		expect(drawn.textContent).toContain("Hello from a UI plugin");
		expect(drawn.textContent).toContain("This page is index.");
		expect(drawn.querySelector(".btn")).not.toBeNull();
	});

	it("draws its widget, with the slot it was given", () => {
		const drawn = draw(
			<Widget
				slot="dashboard"
				plugin={{ name: "template", version: "0.1.0", baseUrl: "/ui/plugins/template/" }}
			/>,
		);

		expect(drawn.textContent).toContain("A widget");
		expect(drawn.textContent).toContain("Drawn into the dashboard slot.");
	});

	it("draws a result it was asked for, in place of udl's table", () => {
		const drawn = draw(
			<ResultView
				operation={{
					name: "count_things",
					title: "Count the things",
					summary: "Counts them.",
					view: "list",
					hints: { read_only: true },
				}}
				value={{ things: 3 }}
			/>,
		);

		expect(drawn.textContent).toContain("Count the things, drawn by this plugin");
		expect(drawn.textContent).toContain('"things": 3');
	});
});
