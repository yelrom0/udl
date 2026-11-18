/**
 * ---------------------------
 * - SchemaForm.re-export.test.tsx -
 * ---------------------------
 *
 * The shell's generated pages import `<SchemaForm>` from `src/generated`, and it is the SDK's
 * form (24.4), so udl's own pages and a UI plugin's draw the same one.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SchemaForm } from "./SchemaForm";

describe("the shell's SchemaForm", () => {
	it("is the SDK's, drawing a form from a schema", () => {
		render(
			<SchemaForm
				schema={{ type: "object", properties: { to: { type: "string", title: "To" } } }}
				value={{}}
				onChange={() => {}}
			/>,
		);

		expect(screen.getByLabelText(/To/)).toBeDefined();
	});
});
