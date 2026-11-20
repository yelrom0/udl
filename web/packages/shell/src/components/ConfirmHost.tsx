/**
 * The page's questions (`ctx.confirm`): one at a time, in daisyUI's modal, answered with the
 * buttons the caller named.
 */

import { answer, useQuestion } from "../stores/confirms";
import { ConfirmDialog } from "./ConfirmDialog";

export function ConfirmHost() {
	const question = useQuestion();
	if (question === null) {
		return null;
	}
	return (
		<ConfirmDialog
			open
			title="Are you sure?"
			message={question.question}
			confirmLabel={question.ok}
			cancelLabel={question.cancel}
			onConfirm={() => {
				answer(true);
			}}
			onCancel={() => {
				answer(false);
			}}
		/>
	);
}
