/**
 * --------
 * - react -
 * --------
 *
 * `react(Component)`: the mount contract over React, for a plugin built with React (the shell's
 * own framework, but a plugin may use any). React is this entry's peer dependency only, so a
 * plugin that doesn't use it never pulls it in.
 */

import type { ReactNode } from "react";
import { createElement } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import type { Mount, UIContext } from "./index";

/** A component a plugin mounts: its props, and the shell's context (as `ctx`). */
export type Component<P> = (props: P & { ctx: UIContext }) => ReactNode;

/**
 * `Component`, as a `Mount`: it is drawn into the target with its props and the context, drawn
 * again when `update` brings other props, and unmounted when `unmount` is called.
 */
export function react<P extends object>(Component: Component<P>): Mount<P> {
	return (target, props, ctx) => {
		const root = createRoot(target);
		const draw = (next: P): void => {
			// (Flushed, so the target is drawn when mount and update return: the contract is the
			// same for a plain-DOM mount and a React one, and the shell's tests need no waiting.)
			flushSync(() => {
				root.render(createElement(Component, { ...next, ctx }));
			});
		};
		draw(props);
		return {
			update: draw,
			unmount: () => {
				root.unmount();
			},
		};
	};
}
