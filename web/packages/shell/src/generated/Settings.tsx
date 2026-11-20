/**
 * --------------
 * - Settings.tsx -
 * --------------
 *
 * A plugin's settings (24.6), in 24.4's `<SchemaForm>` drawn from the schema udl sends: the
 * fields only the config file sets are shown and not editable, a secret is a password box, and
 * Save sends what changed. udl's answer (or its refusal) is said under the form.
 */

import type { UdlApi } from "@udl/api";
import type { JsonObject } from "@udl/ui-sdk";
import { useCallback, useEffect, useRef, useState } from "react";
import { jsonObject, saidBy } from "./run";
import { SchemaForm } from "./SchemaForm";
import type { GeneratedPlugin } from "./spec";

/** What udl says a plugin's settings are (`PluginSettingsInfo`). */
interface SettingsInfo {
	readonly values: JsonObject;
	readonly json_schema: JsonObject;
	readonly file_only: readonly string[];
	readonly secrets: readonly string[];
}

export interface SettingsTabProps {
	readonly plugin: GeneratedPlugin;
	readonly api: UdlApi;
}

/** What to send: `values`, without the secrets udl gave as a mask and the page did not change
 * (sending one back would store the mask as the secret). */
function unsent(info: SettingsInfo | null, seed: JsonObject, values: JsonObject): JsonObject {
	const kept: JsonObject = {};
	for (const [field, value] of Object.entries(values)) {
		if (info?.secrets.includes(field) === true && value === seed[field]) {
			continue;
		}
		kept[field] = value;
	}
	return kept;
}

/** A plugin's settings: its form, and Save. */
export function SettingsTab({ plugin, api }: SettingsTabProps) {
	const [info, setInfo] = useState<SettingsInfo | null>(null);
	const [values, setValues] = useState<JsonObject>({});
	const [said, setSaid] = useState<{ message: string; hint: string | null } | null>(null);
	const [saved, setSaved] = useState(false);
	// (The form is drawn again after a save: a secret's box keeps what was typed in it, and what
	// was saved is not the page's to keep on screen.)
	const [round, setRound] = useState(0);
	// (What udl said the settings are: a secret not typed into again is left as it is, not sent
	// back as the mask udl gave it.)
	const seed = useRef<JsonObject>({});
	const [busy, setBusy] = useState(false);
	const live = useRef(true);

	useEffect(() => {
		live.current = true;
		return () => {
			live.current = false;
		};
	}, []);

	useEffect(() => {
		void (async () => {
			try {
				const answered = await api.call("get_plugin_settings", undefined, {
					name: plugin.name,
				});
				if (live.current) {
					setInfo({
						values: jsonObject(answered.values),
						json_schema: jsonObject(answered.json_schema),
						file_only: answered.file_only,
						secrets: answered.secrets,
					});
					// (The form starts with what udl runs with, not only the schema's defaults.)
					seed.current = jsonObject(answered.values);
					setValues(seed.current);
				}
			} catch (error) {
				if (live.current) {
					setSaid(saidBy(error));
				}
			}
		})();
	}, [api, plugin.name]);

	const save = useCallback(async () => {
		setBusy(true);
		setSaved(false);
		setSaid(null);
		try {
			const answered = await api.call(
				"set_plugin_settings",
				{ values: unsent(info, seed.current, values) },
				{ name: plugin.name },
			);
			if (live.current) {
				setInfo({
					values: jsonObject(answered.values),
					json_schema: jsonObject(answered.json_schema),
					file_only: answered.file_only,
					secrets: answered.secrets,
				});
				// (What is saved is not drawn back: a secret's value is never the page's to keep.)
				seed.current = jsonObject(answered.values);
				setValues(seed.current);
				setRound((times) => times + 1);
				setSaved(true);
			}
		} catch (error) {
			if (live.current) {
				setSaid(saidBy(error));
			}
		} finally {
			if (live.current) {
				setBusy(false);
			}
		}
	}, [api, info, plugin.name, values]);

	if (said !== null && info === null) {
		return (
			<div role="alert" className="alert alert-error">
				<span>{said.message}</span>
			</div>
		);
	}

	return (
		<div className="flex flex-col gap-4">
			{info === null ? (
				<output className="loading loading-spinner" aria-label="Loading the settings" />
			) : (
				<SchemaForm
					key={round}
					schema={info.json_schema}
					value={values}
					onChange={setValues}
					readOnly={info.file_only}
					secret={info.secrets}
					disabled={busy}
				/>
			)}
			<div className="card-actions items-center">
				<button
					type="button"
					className="btn btn-primary btn-sm"
					disabled={busy || info === null}
					onClick={() => void save()}
				>
					Save
				</button>
				{saved && <output className="text-sm text-success">Saved</output>}
			</div>
			{said !== null && info !== null && (
				<div role="alert" className="alert alert-error">
					<span>
						{said.message}
						{said.hint !== null && <span className="block text-xs opacity-80">{said.hint}</span>}
					</span>
				</div>
			)}
		</div>
	);
}
