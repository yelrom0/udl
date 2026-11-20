/**
 * Where the page goes when a plugin says `ctx.navigate(to)`: the shell's router, once it is up
 * (a plugin's own path is not one the shell's typed routes know, so it goes by its path).
 */

let go: (to: string) => void = (to) => {
	window.location.assign(to);
};

/** What the page's history is, as the shell uses it (the router's own `history`). */
export interface History {
	push(path: string): void;
}

/**
 * How the page navigates once it has a router: `to` is a path of the page's (`/sites`,
 * `/p/gmail/inbox`), so the basepath is put in front of it and the router's history moves.
 */
export function routerNavigation(history: History, base = "/ui"): (to: string) => void {
	return (to) => {
		history.push(`${base}${to.startsWith("/") ? to : `/${to}`}`);
	};
}

/** Say how the page navigates (the root route's own, with its router). */
export function onNavigate(handler: (to: string) => void): void {
	go = handler;
}

/** Go to `to` (a path of the page's, `/p/gmail/inbox`). */
export function navigate(to: string): void {
	go(to);
}
