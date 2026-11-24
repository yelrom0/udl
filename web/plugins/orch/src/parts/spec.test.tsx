import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SpecCard } from "./spec";

describe("<SpecCard>", () => {
	it("draws its spec as markdown, and says so when there is none", () => {
		const { rerender } = render(<SpecCard spec={"## What it is\n\nA board."} title="Spec" />);

		expect(screen.getByRole("heading", { name: "What it is" })).toBeTruthy();

		rerender(<SpecCard spec="" title="Spec" />);
		expect(screen.getByText("No spec yet.")).toBeTruthy();
	});

	it("is read-only when it is given nothing to save with", () => {
		render(<SpecCard spec="text" title="Spec" />);

		expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
	});

	it("edits in place, and saves what was typed", async () => {
		const save = vi.fn(async () => {});
		render(<SpecCard onSave={save} spec="old" title="Spec" />);

		fireEvent.click(screen.getByRole("button", { name: "Edit" }));
		const box = screen.getByRole("textbox", { name: "Spec" });
		expect((box as HTMLTextAreaElement).value).toBe("old");

		fireEvent.change(box, { target: { value: "new" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		expect(save).toHaveBeenCalledWith("new");
		// (Back to what it says: the save's own re-read is what draws the new text.)
		expect(await screen.findByRole("button", { name: "Edit" })).toBeTruthy();
	});

	it("leaves it alone when the edit is cancelled, or nothing changed", () => {
		const save = vi.fn(async () => {});
		render(<SpecCard onSave={save} spec="old" title="Spec" />);

		fireEvent.click(screen.getByRole("button", { name: "Edit" }));
		fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
		expect(save).not.toHaveBeenCalled();
		expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();

		fireEvent.click(screen.getByRole("button", { name: "Edit" }));
		fireEvent.click(screen.getByRole("button", { name: "Save" }));
		expect(save).not.toHaveBeenCalled();
	});

	it("says what udl answered when the save was refused", async () => {
		const save = vi.fn(async () => {
			throw new Error("a spec may not be that long");
		});
		render(<SpecCard onSave={save} spec="old" title="Spec" />);

		fireEvent.click(screen.getByRole("button", { name: "Edit" }));
		fireEvent.change(screen.getByRole("textbox", { name: "Spec" }), {
			target: { value: "new" },
		});
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		expect(await screen.findByText(/a spec may not be that long/)).toBeTruthy();
		// (Still editing: what was typed is not lost.)
		expect(screen.getByRole("textbox", { name: "Spec" })).toBeTruthy();
	});
});
