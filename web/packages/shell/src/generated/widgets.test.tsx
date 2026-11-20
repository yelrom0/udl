/**
 * -----------------
 * - widgets.test.tsx -
 * -----------------
 *
 * A UI plugin's own field widgets (24.7): a field that asks for one by name — a name udl has no
 * control for — is drawn by the plugin, in its own root, inside 24.4's `<SchemaForm>`.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { SchemaForm } from "@udl/ui-sdk/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Registry } from "../plugins/registry";
import { startFakingMatchMedia, stopFakingMatchMedia } from "../test-setup";
import { useFieldWidgets } from "./widgets";

const IDENTITY = { name: "templar", version: "0.1.0", baseUrl: "/ui/plugins/templar/" };

/** A form of one field, drawn with `registry`'s widgets. */
function Form({ registry }: { registry: Registry }) {
	const widgets = useFieldWidgets(registry);
	return (
		<SchemaForm
			schema={{
				type: "object",
				properties: { tint: { type: "string", "x-udl-ui": { widget: "color", label: "Tint" } } },
			}}
			value={{}}
			onChange={() => undefined}
			widgets={widgets}
		/>
	);
}

beforeEach(startFakingMatchMedia);
afterEach(stopFakingMatchMedia);

describe("a plugin's field widget", () => {
	it("draws the field that asks for it", async () => {
		const registry = new Registry();
		registry.point("fieldWidgets").add("color", {
			plugin: IDENTITY,
			key: "color",
			mount: (target, props) => {
				target.textContent = `own colour box for ${props.field.label}`;
				return { update: () => undefined, unmount: () => undefined };
			},
		});

		render(<Form registry={registry} />);

		expect(await screen.findByText("own colour box for Tint")).toBeDefined();
		// (udl's own box is not drawn beside it.)
		expect(screen.queryByLabelText(/^Tint/)).toBeNull();
	});

	it("leaves the field to udl when no plugin has that widget", async () => {
		render(<Form registry={new Registry()} />);

		// (A name udl has no control for falls back to the field's type: a text box.)
		await waitFor(() => expect(screen.getByLabelText(/^Tint/)).toBeDefined());
		expect(screen.getByLabelText(/^Tint/)).toHaveProperty("tagName", "INPUT");
	});
});
