/**
 * ----------------
 * - report.test.tsx -
 * ----------------
 *
 * 29.9's import report (`resultViews` for `orch_import_plan`): what the import made, changed,
 * kept, and could not place — the unplaced lines by file and line, with what udl could not read
 * of them.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ImportReportView } from "./report";

/** The operation whose result this is, as the shell hands it over. */
const operation = {
	name: "orch_import_plan",
	title: "Read plan files into udl",
	summary: "The features and tasks the plan files describe.",
	view: "form" as const,
	hints: {},
};

describe("the import's report (29.9)", () => {
	it("says what it made, changed and kept, by key", () => {
		render(
			<ImportReportView
				operation={operation}
				value={{
					created: ["29.1", "29.2"],
					updated: ["29.3"],
					unchanged: ["29.4"],
					kept: ["29.5"],
					unplaced: [],
				}}
			/>,
		);

		expect(screen.getByText("Created 2")).toBeTruthy();
		expect(screen.getByText("29.1")).toBeTruthy();
		expect(screen.getByText("29.2")).toBeTruthy();
		expect(screen.getByText("Updated 1")).toBeTruthy();
		expect(screen.getByText("Kept 1")).toBeTruthy();
	});

	it("lists every line it could not place, with the file, the line and why", () => {
		render(
			<ImportReportView
				operation={operation}
				value={{
					created: [],
					updated: [],
					unchanged: [],
					kept: [],
					unplaced: [
						{
							source: "planning.md",
							line: 8122,
							text: "- [ ] 29.9 something",
							why: "no phase above it",
						},
					],
				}}
			/>,
		);

		expect(screen.getByText("planning.md:8122")).toBeTruthy();
		expect(screen.getByText("- [ ] 29.9 something")).toBeTruthy();
		expect(screen.getByText("no phase above it")).toBeTruthy();
	});

	it("says so plainly when nothing was out of place", () => {
		render(
			<ImportReportView
				operation={operation}
				value={{ created: [], updated: [], unchanged: [], kept: [], unplaced: [] }}
			/>,
		);

		expect(screen.getByText("Every line was placed.")).toBeTruthy();
	});
});
