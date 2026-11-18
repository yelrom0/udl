import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { App } from "./app";
import { startFakingMatchMedia, stopFakingMatchMedia } from "./test-setup";

beforeEach(() => {
	startFakingMatchMedia();
});

afterEach(() => {
	stopFakingMatchMedia();
	delete document.documentElement.dataset.theme;
});

describe("the shell", () => {
	it("says udl", () => {
		render(<App />);

		expect(screen.getByRole("heading", { level: 1, name: "udl" })).toBeTruthy();
	});
});
