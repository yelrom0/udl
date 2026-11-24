/**
 * The page's panels (29.12): a button in the navbar for each one a plugin contributes, and the
 * panel itself at the right of the page while it is open — 400 px over the page, docked beside
 * it on a window 1600 px or wider, and a full-height sheet on a phone.
 *
 * A panel is drawn only while it is open (a closed chat is not polling anything), and one that
 * is popped out is a window of its own (`/ui/panel/<plugin>/<id>`), with the page's copy closed
 * so the two cannot disagree. Which panels are open, and how wide they are, is the person's:
 * `stores/panels` keeps both in this browser.
 */

import type { PanelProps } from "@udl/ui-sdk";
import { type CSSProperties, useEffect, useRef } from "react";
import type { Importer } from "../plugins/load";
import { matchesShortcut, panelUrl, panelWindowName, shortcutOf } from "../plugins/panels";
import type { Contribution, Registry } from "../plugins/registry";
import { shellRegistry } from "../plugins/shell";
import { PluginSlot } from "../plugins/slot";
import {
	closePanel,
	MAX_WIDTH,
	MIN_WIDTH,
	openPanel,
	setPanelWidth,
	usePanels,
	usePanelWidth,
} from "../stores/panels";
import { usePlugins } from "../stores/plugins";
import { Icon } from "./Icon";

/** Every panel the page has, from the registry (the shell's own, or a test's). */
function panelsOf(registry: Registry): readonly Contribution<PanelProps>[] {
	return registry.point("panels").every();
}

/** Open `key`'s panel in a window of its own, and close the page's copy. */
function popOut(key: string): void {
	const url = panelUrl(key);
	if (url === "") {
		return;
	}
	window.open(url, panelWindowName(key), "popup=yes,width=480,height=900");
	closePanel(key);
}

/**
 * The navbar's buttons: one per panel, its title and its icon, and the key that opens it said
 * in its tooltip. A panel that is open says so, and its button closes it.
 */
export function PanelButtons({
	registry = shellRegistry,
}: {
	readonly registry?: Registry;
}): React.ReactElement {
	// (The plugins' manifests, so a button appears once udl's list has been read.)
	usePlugins();
	const open = usePanels();
	return (
		<>
			{panelsOf(registry).map((panel) => {
				const title = panel.title ?? panel.key;
				const shortcut = shortcutOf(panel.shortcut);
				const isOpen = open.includes(panel.key);
				return (
					<button
						key={panel.key}
						type="button"
						className={`btn btn-sm ${isOpen ? "btn-primary" : "btn-ghost"}`}
						aria-pressed={isOpen}
						aria-label={title}
						title={shortcut === null ? title : `${title} (${panel.shortcut ?? ""})`}
						onClick={() => {
							if (isOpen) {
								closePanel(panel.key);
								return;
							}
							openPanel(panel.key);
						}}
					>
						<Icon name={panel.icon} className="size-4" />
						{title}
					</button>
				);
			})}
		</>
	);
}

/** Where a drag of the panel's edge began: the pointer, and the width it was. */
interface Drag {
	readonly x: number;
	readonly width: number;
}

/**
 * The panels themselves: every open one, in the order they were opened, each in its own frame.
 * Nothing is drawn while none is open, and the keys a plugin no longer contributes are ignored.
 */
export function PanelHost({
	registry = shellRegistry,
	importer,
}: {
	readonly registry?: Registry;
	readonly importer?: Importer;
}): React.ReactElement | null {
	// (A plugin registered after the first draw brings its panel's button with it.)
	usePlugins();
	const open = usePanels();
	const width = usePanelWidth();
	const drag = useRef<Drag | null>(null);

	// (Every panel's own key opens and closes it: Ctrl+J for the chat.)
	useEffect(() => {
		const onKey = (event: KeyboardEvent): void => {
			for (const panel of panelsOf(registry)) {
				const shortcut = shortcutOf(panel.shortcut);
				if (shortcut === null || !matchesShortcut(event, shortcut)) {
					continue;
				}
				event.preventDefault();
				if (event.repeat) {
					return;
				}
				if (open.includes(panel.key)) {
					closePanel(panel.key);
				} else {
					openPanel(panel.key);
				}
				return;
			}
		};
		document.addEventListener("keydown", onKey);
		return () => document.removeEventListener("keydown", onKey);
	}, [registry, open]);

	if (open.length === 0) {
		return null;
	}

	const resize = (event: React.PointerEvent<HTMLDivElement>): void => {
		const start = drag.current;
		if (start === null) {
			return;
		}
		// (Dragging the left edge to the left makes the panel wider.)
		setPanelWidth(start.width + (start.x - event.clientX));
	};
	const end = (): void => {
		drag.current = null;
		document.removeEventListener("pointermove", resize as unknown as EventListener);
		document.removeEventListener("pointerup", end);
	};
	const begin = (event: React.PointerEvent<HTMLDivElement>): void => {
		drag.current = { x: event.clientX, width };
		document.addEventListener("pointermove", resize as unknown as EventListener);
		document.addEventListener("pointerup", end);
	};

	return (
		<>
			{open.map((key) => {
				const panel = panelsOf(registry).find((one) => one.key === key);
				if (panel === undefined) {
					// (Its plugin is not in the page any more: there is nothing to draw.)
					return null;
				}
				return (
					<aside
						key={key}
						aria-label={panel.title ?? key}
						data-panel={key}
						className={[
							"flex flex-col bg-base-200 shadow-2xl",
							// (A phone's: the window, all of it.)
							"fixed inset-y-0 right-0 z-30 w-full",
							// (A desktop's: 400 px over the page, and docked beside it when wide.)
							"sm:w-[var(--panel-width)] sm:border-l sm:border-base-300",
							"min-[1600px]:static min-[1600px]:z-auto min-[1600px]:shadow-none",
						].join(" ")}
						style={{ "--panel-width": `${width}px` } as CSSProperties}
					>
						{/* (A separator is what it is, and it takes the keyboard too: the arrow keys
						    move the edge, and its value says where the edge is.) */}
						<hr
							aria-label="Resize the panel"
							aria-orientation="vertical"
							aria-valuenow={width}
							aria-valuemin={MIN_WIDTH}
							aria-valuemax={MAX_WIDTH}
							tabIndex={0}
							className="absolute inset-y-0 left-0 m-0 hidden w-1 cursor-col-resize border-0 hover:bg-primary/40 min-[1600px]:block"
							onPointerDown={begin}
							onKeyDown={(event) => {
								const step = event.key === "ArrowLeft" ? 16 : event.key === "ArrowRight" ? -16 : 0;
								if (step === 0) {
									return;
								}
								event.preventDefault();
								setPanelWidth(width + step);
							}}
						/>
						<PluginSlot
							point="panels"
							lookup={key}
							registry={registry}
							importer={importer}
							props={{
								open: true,
								close: () => closePanel(key),
								popOut: () => popOut(key),
							}}
						/>
					</aside>
				);
			})}
		</>
	);
}
