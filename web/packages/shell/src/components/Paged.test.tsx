import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Paged } from "./Paged";

describe("Paged", () => {
	it("says which page it is on", () => {
		render(<Paged page={2} pages={5} onChange={vi.fn()} />);

		expect(screen.getByText("Page 2 of 5")).toBeTruthy();
	});

	it("moves to the next and the previous page", () => {
		const changed = vi.fn();
		render(<Paged page={2} pages={5} onChange={changed} />);

		fireEvent.click(screen.getByRole("button", { name: "Next" }));
		fireEvent.click(screen.getByRole("button", { name: "Previous" }));

		expect(changed.mock.calls).toEqual([[3], [1]]);
	});

	it("cannot go before the first page or after the last", () => {
		const { rerender } = render(<Paged page={1} pages={1} onChange={vi.fn()} />);

		expect(screen.getByRole("button", { name: "Previous" }).hasAttribute("disabled")).toBe(true);
		expect(screen.getByRole("button", { name: "Next" }).hasAttribute("disabled")).toBe(true);

		rerender(<Paged page={3} pages={3} onChange={vi.fn()} />);
		expect(screen.getByRole("button", { name: "Next" }).hasAttribute("disabled")).toBe(true);
		expect(screen.getByRole("button", { name: "Previous" }).hasAttribute("disabled")).toBe(false);
	});
});
