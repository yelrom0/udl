import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

describe("ConfirmDialog", () => {
	it("asks, and does it when the confirm button is pressed", () => {
		const confirm = vi.fn();
		render(
			<ConfirmDialog
				open
				title="Delete the site?"
				message="Its downloads are kept."
				confirmLabel="Delete"
				onConfirm={confirm}
				onCancel={vi.fn()}
			/>,
		);

		expect(screen.getByText("Delete the site?")).toBeTruthy();
		expect(screen.getByText("Its downloads are kept.")).toBeTruthy();
		fireEvent.click(screen.getByRole("button", { name: "Delete" }));

		expect(confirm).toHaveBeenCalledOnce();
	});

	it("does nothing when cancel is pressed", () => {
		const confirm = vi.fn();
		const cancel = vi.fn();
		render(
			<ConfirmDialog
				open
				title="Cancel the run?"
				message="It stops where it is."
				confirmLabel="Cancel the run"
				onConfirm={confirm}
				onCancel={cancel}
			/>,
		);

		fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

		expect(cancel).toHaveBeenCalledOnce();
		expect(confirm).not.toHaveBeenCalled();
	});

	it("says its own word for cancelling when it is given one", () => {
		render(
			<ConfirmDialog
				open
				title="?"
				message="?"
				confirmLabel="Go"
				cancelLabel="Keep it"
				onConfirm={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);

		expect(screen.getByRole("button", { name: "Keep it" })).toBeTruthy();
	});

	it("is a closed dialog while it is not asked", () => {
		const { container } = render(
			<ConfirmDialog
				open={false}
				title="?"
				message="?"
				confirmLabel="Go"
				onConfirm={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);

		expect(container.querySelector("dialog")?.hasAttribute("open")).toBe(false);
	});
});
