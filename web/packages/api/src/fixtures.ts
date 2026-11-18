// Generated from openapi.json by scripts/generate.mjs: do not edit.
import type { components } from "./schema";

/** A sample of each list the API answers, by the operation that answers it: what FakeUdl
 * answers with until a test says otherwise. */
export const fixtures = {
	list_accounts: {
		"accounts": [
			{
				"access": [],
				"account": "text",
				"connected_at": "2026-01-01T00:00:00Z",
				"created_at": "2026-01-01T00:00:00Z",
				"enabled": false,
				"id": "text",
				"identity": {
					"display": "text",
					"id": "text"
				},
				"integration": "text",
				"last_error": {
					"code": "udl_error",
					"message": "text"
				},
				"method": "oauth",
				"name": "text",
				"options": {},
				"pending_until": "2026-01-01T00:00:00Z",
				"status": "pending"
			}
		]
	} satisfies components["schemas"]["AccountList"],
	list_actions: {
		"actions": [
			{
				"created_at": "2026-01-01T00:00:00Z",
				"description": "",
				"enabled": true,
				"id": "text",
				"max_concurrent": 1,
				"name": "text",
				"options_schema": {},
				"plugin": "text",
				"retries": 0,
				"retry_timeouts": false,
				"timeout": "5m",
				"updated_at": "2026-01-01T00:00:00Z",
				"used_by": []
			}
		]
	} satisfies components["schemas"]["ActionList"],
	list_challenges: {
		"challenges": [
			{
				"batches": [],
				"kind": "cloudflare",
				"since": "2026-01-01T00:00:00Z",
				"site": "text",
				"urls": []
			}
		]
	} satisfies components["schemas"]["ChallengeList"],
	list_cookies: {
		"sites": [
			{
				"cookies": [],
				"site": "text"
			}
		]
	} satisfies components["schemas"]["CookieList"],
	list_databases: {
		"databases": [
			{
				"connected": false,
				"healthy": false,
				"name": "text",
				"plugin": "text",
				"primary": false,
				"priority": 0,
				"read_only": false,
				"url": "text"
			}
		]
	} satisfies components["schemas"]["DatabaseList"],
	list_downloads: {
		"downloads": [
			{
				"id": "text",
				"site_key": "text",
				"size_bytes": 0,
				"started_at": "2026-01-01T00:00:00Z",
				"status": "queued",
				"url": "text"
			}
		]
	} satisfies components["schemas"]["DownloadList"],
	list_event_types: {
		"types": [
			{
				"example": {},
				"fields": [],
				"json_schema": {},
				"name": "text",
				"source": "text",
				"summary": "text"
			}
		]
	} satisfies components["schemas"]["EventTypeList"],
	list_events: {
		"events": [
			{
				"account_id": "text",
				"caused_by": "text",
				"data": {},
				"dedupe_key": "text",
				"depth": 0,
				"dispatched_at": "2026-01-01T00:00:00Z",
				"dropped_reason": "text",
				"id": "text",
				"occurred_at": "2026-01-01T00:00:00Z",
				"source": "text",
				"status": "pending",
				"stored_at": "2026-01-01T00:00:00Z",
				"subject": "text",
				"type": "text"
			}
		]
	} satisfies components["schemas"]["EventList"],
	list_fetches: {
		"fetches": [
			{
				"counts": {},
				"created_at": "2026-01-01T00:00:00Z",
				"finished_at": "2026-01-01T00:00:00Z",
				"id": "text",
				"label": "text",
				"status": "queued"
			}
		]
	} satisfies components["schemas"]["FetchBatchList"],
	list_hosts: {
		"hosts": [
			{
				"enabled": true,
				"id": "text",
				"name": "text",
				"transport": "local",
				"work_dir": "text"
			}
		]
	} satisfies components["schemas"]["HostList"],
	list_plugins: {
		"plugin_dirs": [
			"text"
		],
		"plugins": [
			{
				"available": false,
				"kind": "text",
				"name": "text",
				"origin": "text",
				"source": "builtin"
			}
		],
		"problems": [
			{
				"error": "text",
				"kind": "text",
				"origin": "text"
			}
		]
	} satisfies components["schemas"]["PluginList"],
	list_runs: {
		"runs": [
			{
				"action": "text",
				"attempt": 0,
				"error": {
					"code": "udl_error",
					"message": "text"
				},
				"event_id": "text",
				"event_type": "text",
				"finished_at": "2026-01-01T00:00:00Z",
				"id": "text",
				"next_try_at": "2026-01-01T00:00:00Z",
				"plugin": "text",
				"queued_at": "2026-01-01T00:00:00Z",
				"skipped_reason": "text",
				"started_at": "2026-01-01T00:00:00Z",
				"status": "queued",
				"subscription": "text",
				"summary": "text"
			}
		]
	} satisfies components["schemas"]["RunList"],
	list_schedules: {
		"schedules": [
			{
				"actions": [],
				"created_at": "2026-01-01T00:00:00Z",
				"data": {},
				"description": "text",
				"enabled": false,
				"id": "text",
				"jitter": "text",
				"last_run_at": "2026-01-01T00:00:00Z",
				"misfire": "skip",
				"misfire_grace": "text",
				"name": "text",
				"next_run_at": "2026-01-01T00:00:00Z",
				"next_times": [],
				"spec": {
					"cron": "text"
				},
				"spec_text": "text",
				"subscription": {
					"actions": [],
					"created_at": "2026-01-01T00:00:00Z",
					"description": "",
					"enabled": true,
					"events": [],
					"filter_text": "text",
					"id": "text",
					"name": "text",
					"updated_at": "2026-01-01T00:00:00Z"
				},
				"timezone": "text",
				"updated_at": "2026-01-01T00:00:00Z",
				"zone": "text"
			}
		]
	} satisfies components["schemas"]["ScheduleList"],
	list_scripts: {
		"dir": "text",
		"exists": false,
		"scripts": [
			{
				"executable": false,
				"modified": "2026-01-01T00:00:00Z",
				"name": "text",
				"size": 0
			}
		]
	} satisfies components["schemas"]["ScriptList"],
	list_sites: {
		"sites": [
			{
				"key": "text"
			}
		]
	} satisfies components["schemas"]["SiteList"],
	list_subscriptions: {
		"subscriptions": [
			{
				"actions": [],
				"created_at": "2026-01-01T00:00:00Z",
				"description": "",
				"enabled": true,
				"events": [],
				"filter_text": "text",
				"id": "text",
				"name": "text",
				"updated_at": "2026-01-01T00:00:00Z"
			}
		]
	} satisfies components["schemas"]["SubscriptionList"],
};
