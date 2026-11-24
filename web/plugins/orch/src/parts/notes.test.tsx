import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Notes } from "./notes";

const NOTES = [
	{ at: "2026-11-24T01:00:00Z", author: "claude", note_kind: "review", text: "Reviewed." },
	{ at: "2026-11-24T02:00:00Z", author: "deepseek-a", note_kind: "progress", text: "Pushed." },
];

describe("<Notes>", () => {
	it("draws the timeline, oldest first, by its accessible name", () => {
		render(<Notes notes={NOTES} />);

		const list = screen.getByRole("list", { name: "Notes" });
		const items = within(list).getAllByRole("listitem");
		expect(items).toHaveLength(2);
		expect(items[0]?.textContent).toContain("Reviewed.");
		expect(items[1]?.textContent).toContain("Pushed.");
		// (Who wrote it, and what kind of note it is.)
		expect(items[0]?.textContent).toContain("claude");
		expect(items[0]?.textContent).toContain("review");
	});

	it("says when there are none", () => {
		render(<Notes notes={[]} />);

		expect(screen.getByText("No notes yet.")).toBeTruthy();
	});

	it("adds one, with its kind, and empties the box", async () => {
		const add = vi.fn(async () => {});
		render(<Notes notes={[]} onAdd={add} />);

		fireEvent.change(screen.getByRole("combobox", { name: "Kind" }), {
			target: { value: "decision" },
		});
		fireEvent.change(screen.getByRole("textbox", { name: "Note" }), {
			target: { value: "We chose the board." },
		});
		fireEvent.click(screen.getByRole("button", { name: "Add note" }));

		expect(add).toHaveBeenCalledWith({ note_kind: "decision", text: "We chose the board." });
		expect(await screen.findByRole("textbox", { name: "Note" })).toHaveProperty("value", "");
	});

	it("says nothing to add when the box is empty", () => {
		const add = vi.fn(async () => {});
		render(<Notes notes={[]} onAdd={add} />);

		fireEvent.click(screen.getByRole("button", { name: "Add note" }));

		expect(add).not.toHaveBeenCalled();
	});

	it("is read-only when it is given nothing to add with", () => {
		render(<Notes notes={NOTES} />);

		expect(screen.queryByRole("button", { name: "Add note" })).toBeNull();
	});
});
