import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./app";

describe("the shell", () => {
	it("says udl", () => {
		render(<App />);

		expect(screen.getByRole("heading", { level: 1, name: "udl" })).toBeTruthy();
	});
});
