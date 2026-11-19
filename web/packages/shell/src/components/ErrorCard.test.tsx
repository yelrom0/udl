import { fireEvent, render, screen } from "@testing-library/react";
import { UdlError } from "@udl/api";
import { describe, expect, it, vi } from "vitest";
import { describe as describeError, ErrorCard } from "./ErrorCard";

describe("ErrorCard", () => {
	it("says udl's message and hint", () => {
		const error = new UdlError(
			{ code: "does_not_exist", message: "No such site", hint: "List the sites first." },
			404,
		);

		render(<ErrorCard error={error} />);

		expect(screen.getByRole("alert")).toBeTruthy();
		expect(screen.getByText("No such site")).toBeTruthy();
		expect(screen.getByText("List the sites first.")).toBeTruthy();
	});

	it("shows no hint when udl gave none", () => {
		render(
			<ErrorCard error={new UdlError({ code: "does_not_exist", message: "No such site" }, 404)} />,
		);

		expect(screen.getByText("No such site")).toBeTruthy();
		expect(document.querySelectorAll("p")).toHaveLength(1);
	});

	it("says an error's own message, and nothing it does not have", () => {
		render(<ErrorCard error={new Error("the network is down")} />);

		expect(screen.getByText("the network is down")).toBeTruthy();
		expect(describeError(new Error("x")).hint).toBeNull();
	});

	it("says what a value that is not an error is", () => {
		expect(describeError("something odd").message).toBe("something odd");
	});

	it("tries again when it is asked to", () => {
		const retry = vi.fn();
		render(<ErrorCard error={new Error("nope")} onRetry={retry} />);

		fireEvent.click(screen.getByRole("button", { name: "Try again" }));

		expect(retry).toHaveBeenCalledOnce();
	});

	it("offers no button when there is nothing to try", () => {
		render(<ErrorCard error={new Error("nope")} />);

		expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
	});
});
