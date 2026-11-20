/**
 * Where the page goes when a plugin says `ctx.navigate(to)`: the shell's router, once it is up
 * (a plugin's own path is not one the shell's typed routes know, so it goes by its path).
 */

let go: (to: string) => void = (to) => {
	window.location.assign(to);
};

/** Say how the page navigates (the root route's own, with its router). */
export function onNavigate(handler: (to: string) => void): void {
	go = handler;
}

/** Go to `to` (a path of the page's, `/p/gmail/inbox`). */
export function navigate(to: string): void {
	go(to);
}
