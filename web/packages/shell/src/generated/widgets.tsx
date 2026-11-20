/**
 * --------------
 * - widgets.tsx -
 * --------------
 *
 * A UI plugin's own field widgets (24.7), for 24.4's `<SchemaForm>`: the fields whose
 * `x-udl-ui.widget` names one it contributes are drawn by it, in its shadow root, like any other
 * contribution — a field a plugin's own page needs and udl's form has no control for.
 */

import type { FieldWidgetProps } from "@udl/ui-sdk";
import type { ReactNode } from "react";
import { useMemo } from "react";
import type { Registry } from "../plugins/registry";
import { shellRegistry } from "../plugins/shell";
import { PluginSlot } from "../plugins/slot";

/** `registry`'s field widgets, by the name a field asks for (a form passes this to
 * `<SchemaForm widgets={…}>`). */
export function useFieldWidgets(
	registry: Registry = shellRegistry,
): Readonly<Record<string, (props: FieldWidgetProps) => ReactNode>> {
	return useMemo(() => {
		const found: Record<string, (props: FieldWidgetProps) => ReactNode> = {};
		for (const contribution of registry.point("fieldWidgets").every()) {
			found[contribution.key] = (props: FieldWidgetProps) => (
				<PluginSlot point="fieldWidgets" props={props} registry={registry} />
			);
		}
		return found;
	}, [registry]);
}
