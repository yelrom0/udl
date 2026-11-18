/**
 * "Are you sure?" — for what cannot be undone (a delete, a cancel). daisyUI's modal, opened
 * while `open` is true; the dialog is a real `<dialog>`, so a screen reader announces it.
 */

export interface ConfirmDialogProps {
	readonly open: boolean;
	readonly title: string;
	readonly message: string;
	/** The button that does it ("Delete", "Cancel the run"). */
	readonly confirmLabel: string;
	/** The button that does not, and the dialog's own "Cancel". */
	readonly cancelLabel?: string;
	readonly onConfirm: () => void;
	readonly onCancel: () => void;
}

export function ConfirmDialog({
	open,
	title,
	message,
	confirmLabel,
	cancelLabel = "Cancel",
	onConfirm,
	onCancel,
}: ConfirmDialogProps) {
	return (
		<dialog className="modal" open={open} aria-labelledby="confirm-title">
			<div className="modal-box">
				<h3 id="confirm-title" className="text-lg font-bold">
					{title}
				</h3>
				<p className="py-4">{message}</p>
				<div className="modal-action">
					<button type="button" className="btn" onClick={onCancel}>
						{cancelLabel}
					</button>
					<button type="button" className="btn btn-error" onClick={onConfirm}>
						{confirmLabel}
					</button>
				</div>
			</div>
		</dialog>
	);
}
