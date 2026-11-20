/**
 * The page's questions (`ctx.confirm`): one at a time, in daisyUI's modal, answered with the
 * buttons the caller named.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ask, confirms } from "../stores/confirms";
import { ConfirmHost } from "./ConfirmHost";

beforeEach(() => {
	confirms.setState({ asked: [] });
});

describe("the page's questions", () => {
	it("shows nothing when nothing is asked", () => {
		const { container } = render(<ConfirmHost />);

		expect(container.querySelector("dialog")).toBeNull();
	});

	it("shows the question, with the buttons the caller named", () => {
		void ask("Delete the site?", { ok: "Delete", cancel: "Keep it" });

		render(<ConfirmHost />);

		expect(screen.getByText("Delete the site?")).toBeTruthy();
		expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "Keep it" })).toBeTruthy();
	});

	it("answers yes with the button that does it, and lets the dialog go", async () => {
		const said = ask("Delete the site?", { ok: "Delete" });
		render(<ConfirmHost />);

		fireEvent.click(screen.getByRole("button", { name: "Delete" }));

		await expect(said).resolves.toBe(true);
		expect(screen.queryByRole("dialog")).toBeNull();
	});

	it("answers no with the cancel button", async () => {
		const said = ask("Delete the site?");
		render(<ConfirmHost />);

		fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

		await expect(said).resolves.toBe(false);
	});

	it("answers the next question after the first", async () => {
		const first = ask("First?");
		const second = ask("Second?");
		render(<ConfirmHost />);

		fireEvent.click(screen.getByRole("button", { name: "OK" }));

		await expect(first).resolves.toBe(true);
		expect(screen.getByText("Second?")).toBeTruthy();
		fireEvent.click(screen.getByRole("button", { name: "OK" }));
		await expect(second).resolves.toBe(true);
	});
});
