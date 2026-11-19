import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { asText, JsonTree } from "./JsonTree";

describe("JsonTree", () => {
	it("shows the value as indented JSON", () => {
		render(<JsonTree value={{ a: 1, b: [2, 3] }} label="the event's data" />);

		const shown = screen.getByText(/"a": 1/);
		expect(shown.tagName).toBe("PRE");
		expect(shown.textContent).toBe('{\n  "a": 1,\n  "b": [\n    2,\n    3\n  ]\n}');
		expect(screen.getByText("the event's data")).toBeTruthy();
	});

	it("shows HTML in the value as text, never as markup", () => {
		const hostile = { html: "<img src=x onerror=alert(1)>" };

		render(<JsonTree value={hostile} />);

		expect(screen.getByText(/<img src=x onerror=alert\(1\)>/).tagName).toBe("PRE");
		expect(document.querySelector("img")).toBeNull();
	});

	it("says a value that cannot be printed is not printable", () => {
		const circular: Record<string, unknown> = {};
		circular.self = circular;

		expect(asText(circular)).toBe("(not printable as JSON)");
		render(<JsonTree value={circular} />);
		expect(screen.getByText("(not printable as JSON)")).toBeTruthy();
	});

	it("prints undefined as null, as JSON does", () => {
		expect(asText(undefined)).toBe("null");
	});

	it("has no label when it is not given one", () => {
		const { container } = render(<JsonTree value={1} />);

		expect(container.querySelector("figcaption")).toBeNull();
	});
});
