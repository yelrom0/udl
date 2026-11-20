/**
 * --------------
 * - Settings.tsx -
 * --------------
 *
 * A plugin's settings (24.6), in 24.4's `<SchemaForm>` drawn from the schema udl sends: the
 * fields only the config file sets are shown and not editable, a secret is a password box, and
 * Save sends what changed. udl's answer (or its refusal) is said under the form.
 *
 * Since 24.7 the plugin's own UI plugin may draw the panel instead (`settings`): it is given what
 * udl answered, and a `save` that sends it back.
 */

import type { UdlApi } from "@udl/api";
import type { JsonObject } from "@udl/ui-sdk";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Registry } from "../plugins/registry";
import { contributed, shellRegistry } from "../plugins/shell";
import { PluginSlot } from "../plugins/slot";
import { jsonObject, saidBy } from "./run";
import { SchemaForm } from "./SchemaForm";
import type { GeneratedPlugin } from "./spec";
import { useFieldWidgets } from "./widgets";

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
	/** Where the contributions are (a test's own; the shell's when none is given). */
	readonly registry?: Registry;
}

/** What to send: only what the user changed, and never a file-only field. (udl answers with the
 * whole layered model — a file-only field, one the config file sets, the defaults — and it
 * refuses a file-only field in a save; sending an unchanged value back would also shadow the
 * config file from then on. A secret it gave as a mask, untouched, is unchanged, so it goes
 * nowhere: sending it back would store the mask as the secret.) */
function unsent(info: SettingsInfo | null, seed: JsonObject, values: JsonObject): JsonObject {
	const kept: JsonObject = {};
	for (const [field, value] of Object.entries(values)) {
		if (info?.file_only.includes(field) === true) {
			continue;
		}
		if (value === seed[field]) {
			continue;
		}
		kept[field] = value;
	}
	return kept;
}

/** A plugin's settings: its form, and Save. */
export function SettingsTab({ plugin, api, registry = shellRegistry }: SettingsTabProps) {
	const [info, setInfo] = useState<SettingsInfo | null>(null);
	// (What udl answered, whole: a contributed panel is given it as it is.)
	const [answered, setAnswered] = useState<JsonObject | null>(null);
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
	// (Every render, before any return: a hook after one would change the count.)
	const widgets = useFieldWidgets(registry);

	useEffect(() => {
		live.current = true;
		return () => {
			live.current = false;
		};
	}, []);

	useEffect(() => {
		void (async () => {
			try {
				const said = await api.call("get_plugin_settings", undefined, {
					name: plugin.name,
				});
				if (live.current) {
					setAnswered(jsonObject(said));
					setInfo({
						values: jsonObject(said.values),
						json_schema: jsonObject(said.json_schema),
						file_only: said.file_only,
						secrets: said.secrets,
					});
					// (The form starts with what udl runs with, not only the schema's defaults.)
					seed.current = jsonObject(said.values);
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

	// (The plugin's own panel, when it has one: it is given what udl answered, and the save.)
	if (contributed("settings", plugin.name, registry)) {
		return answered === null ? (
			<output className="loading loading-spinner" aria-label="Loading the settings" />
		) : (
			<PluginSlot
				point="settings"
				lookup={plugin.name}
				registry={registry}
				props={{
					plugin: plugin.name,
					info: answered,
					save: async (values) => {
						await api.call("set_plugin_settings", { values }, { name: plugin.name });
					},
				}}
			/>
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
					widgets={widgets}
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
