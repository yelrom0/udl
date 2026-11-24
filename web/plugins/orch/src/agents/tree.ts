import type { UIContext } from "@udl/ui-sdk";
import { messageOf } from "../json";
import { type AgentInfo, chainOf, isAbove } from "../model";

/** What a drag carries: an agent's name (the tree is the only thing that drags one). */
let carried: string | null = null;

/** Carry `name` from now until the drop, or nothing at all (`null`) when the drag ends. */
export function carryAgent(name: string | null): void {
	carried = name;
}

/** What is being carried, when anything is. */
export function carryingAgent(): string | null {
	return carried;
}

/**
 * What a drop of `dragged` onto `onto` does (29.6): a manager takes reports, and nothing may
 * report to something that already reports up through it. `onto` is null for the humans — the
 * root every agent may report to.
 *
 * Returns the reason it was refused, when it was, and null when it was written (or when there
 * was nothing to write). A refusal udl itself makes is given back as it said it.
 */
export async function dropped(
	ctx: UIContext,
	agents: readonly AgentInfo[],
	dragged: string,
	onto: string | null,
): Promise<string | null> {
	carried = null;
	if (dragged === onto || dragged === "") {
		return null;
	}
	if (onto !== null) {
		const target = agents.find((one) => one.agent.name === onto)?.agent;
		if (target === undefined) {
			return null;
		}
		if (target.role !== "manager") {
			return `${target.name} is ${article(target.role)}: only a manager takes reports.`;
		}
		// (A manager reporting to something below it would make the chain a ring.)
		if (isAbove(agents, dragged, onto)) {
			return `That would make a loop: ${dragged} already reports up through ${onto}.`;
		}
	}
	try {
		await ctx.call("orch_set_agent", { name: dragged, manager: onto ?? "" });
		return null;
	} catch (failure) {
		return messageOf(failure);
	}
}

/** `role` with the article a person would say it with. */
function article(role: string): string {
	return role === "implementer" || role === "manager" ? `an ${role}` : `a ${role}`;
}

/** The agents that report to the humans: the roots of the tree, by name. An agent whose manager
 * is not among them is drawn there too — a name that is not there is not a place to hide one. */
export function rootsOf(agents: readonly AgentInfo[]): string[] {
	const names = new Set(agents.map((one) => one.agent.name));
	return agents
		.filter((one) => one.agent.manager === null || !names.has(one.agent.manager))
		.map((one) => one.agent.name)
		.sort();
}

/** The agents that report to `name`, by name. */
export function reportsOf(agents: readonly AgentInfo[], name: string): string[] {
	return agents
		.filter((one) => one.agent.manager === name)
		.map((one) => one.agent.name)
		.sort();
}

/** Where an agent sits, for a page that says it in a line: its chain from the top down, so a
 * person reads it the way they read a path (`Top › Boss`). */
export function whereOf(agents: readonly AgentInfo[], name: string): string {
	const chain = chainOf(agents, name);
	return chain.length === 0 ? "the humans" : [...chain].reverse().join(" › ");
}
