// A UI plugin whose page throws while it draws: the shell shows an error card in its place, and
// carries on (18.9's "done when").
export default {
	routes: {
		throw: () => {
			throw new Error("boom throws on purpose");
		},
	},
};
