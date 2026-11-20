// A UI plugin with no framework at all: what a mount is, and nothing more (18.9's e2e proof).
const mount = (target, props) => {
	const card = document.createElement("div");
	card.className = "hello";
	card.textContent = `Hello from the plugin, on ${props.path ?? props.slot}`;
	target.append(card);
	return {
		update(next) {
			card.textContent = `Hello from the plugin, on ${next.path ?? next.slot}`;
		},
		unmount() {
			card.remove();
		},
	};
};

export default {
	routes: { hello: mount },
	widgets: { greeting: mount },
};
