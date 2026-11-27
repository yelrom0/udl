/**
 * The chat's transcript as it is drawn (29.12): what the person said, what the harness answered,
 * and each tool it ran as one line. The events are the ones an agent's session keeps, so the
 * reading is shared — but a chat's are not grouped by turn, so a `said` starts the next block.
 */

import type { Spoken } from "../model";

/** One thing drawn in the transcript. */
export type Block =
	| { readonly kind: "said"; readonly text: string }
	| { readonly kind: "text"; readonly text: string }
	| {
			readonly kind: "tool";
			readonly name: string;
			readonly summary: string;
			/** Whether it worked (null: it has not finished). */
			readonly ok: boolean | null;
	  }
	| { readonly kind: "note"; readonly text: string };

/** What a turn's end says, when it says anything a reader needs (an error, or a stop). */
function endedOf(event: Spoken): string {
	if (event.kind !== "turn_ended" || event.reason === "done") {
		return "";
	}
	return event.text !== "" ? event.text : event.reason;
}

/**
 * The transcript's blocks: the person's words, the harness's text (a run of deltas is one
 * paragraph), each tool call with how it ended, and the notes a failure or a limit makes.
 */
export function blocksOf(events: readonly Spoken[]): Block[] {
	const blocks: Block[] = [];
	let text = "";
	interface Tool {
		readonly name: string;
		readonly summary: string;
		readonly ok: boolean | null;
	}
	let tool: Tool | null = null;
	const flushText = (): void => {
		if (text !== "") {
			blocks.push({ kind: "text", text });
			text = "";
		}
	};
	const flushTool = (): void => {
		if (tool !== null) {
			blocks.push({ kind: "tool", ...tool });
			tool = null;
		}
	};
	for (const event of events) {
		switch (event.kind) {
			case "said":
				flushText();
				flushTool();
				blocks.push({ kind: "said", text: event.text });
				break;
			case "text_delta":
				flushTool();
				text += event.text;
				break;
			case "tool_started":
				flushText();
				flushTool();
				tool = { name: event.name, summary: event.summary, ok: null };
				break;
			case "tool_finished": {
				// (A result belongs to the call before it; one with no call of its own is a line.)
				if (tool !== null && tool.name === event.name) {
					tool = { name: tool.name, summary: tool.summary, ok: event.ok };
				} else {
					flushText();
					flushTool();
					blocks.push({
						kind: "tool",
						name: event.name,
						summary: "",
						ok: event.ok,
					});
				}
				flushTool();
				break;
			}
			case "runner_failed":
				flushText();
				flushTool();
				blocks.push({ kind: "note", text: event.message });
				break;
			case "runner_limited":
				flushText();
				flushTool();
				blocks.push({
					kind: "note",
					text:
						event.until === null
							? "It is out of quota."
							: `It is out of quota until ${event.until}.`,
				});
				break;
			case "turn_ended": {
				const said = endedOf(event);
				if (said !== "") {
					flushText();
					flushTool();
					blocks.push({ kind: "note", text: said });
				}
				break;
			}
			default:
				// (What a turn cost is in the chat's own line, not in its conversation.)
				break;
		}
	}
	flushText();
	flushTool();
	return blocks;
}
