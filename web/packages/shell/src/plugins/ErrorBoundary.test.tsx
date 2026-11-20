/**
 * The boundary a slot's contributions draw inside: a render error is a card in that place, not
 * a blank page, and what it caught is said once.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

/** A component that throws when it draws (a plugin's own bug, as React sees it). */
function Broken(): never {
	throw new Error("its own bug");
}

describe("a slot's error boundary", () => {
	it("draws what it is given when nothing fails", () => {
		render(
			<ErrorBoundary>
				<p>the contribution</p>
			</ErrorBoundary>,
		);

		expect(screen.getByText("the contribution")).toBeTruthy();
	});

	it("draws an error card when its child throws", () => {
		// (React says so on the console too: a test's own noise, not the page's.)
		const said = vi.spyOn(console, "error").mockImplementation(() => {});

		render(
			<ErrorBoundary>
				<Broken />
			</ErrorBoundary>,
		);

		expect(screen.getByRole("alert")).toHaveProperty(
			"textContent",
			expect.stringContaining("its own bug"),
		);
		said.mockRestore();
	});

	it("says what it caught, when it is given somewhere to say it", () => {
		const said = vi.spyOn(console, "error").mockImplementation(() => {});
		const heard = vi.fn();

		render(
			<ErrorBoundary onError={heard}>
				<Broken />
			</ErrorBoundary>,
		);

		expect(heard).toHaveBeenCalledWith(expect.objectContaining({ message: "its own bug" }));
		said.mockRestore();
	});
});
