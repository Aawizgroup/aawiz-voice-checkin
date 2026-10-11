# Privacy design

Aawiz Voice Check-in is built so that the least data possible is kept, for the shortest time,
and only with consent. This page describes what the code does. It is not legal advice.

## What is stored

| Field | When | Why |
|---|---|---|
| Date | Every check-in | To compare this week with last week |
| Mood, energy, stress (1 to 5) | With `scores` consent | The check-in itself |
| Sleep hours (0 to 14) | Optional, with `scores` consent | Context for the micro-action |
| Note (max 280 characters) | Optional, only with `notes` consent | Your own words, if you want to keep them |
| Consent records | When you give or change consent | Proof of what you agreed to and when |

Nothing else: no names, no emails, no device IDs, no location, no audio. The user is known by
a pseudonym that a bearer token maps to.

## Consent

There are two separate scopes:

- `scores`: without it, `log_checkin` stores nothing and says so.
- `notes`: without it, the note is dropped before anything is written. The scores are still
  stored when `scores` consent exists.

Consent can be changed at any time ("change my consent"). Each change is kept as a record.

## Your rights, by voice

- **Know:** `get_privacy_summary` explains what is kept, why, for how long and how to delete it.
- **Access and portability:** `export_my_data` returns every row and consent record as JSON.
- **Erasure:** `delete_my_data` deletes all check-ins and consent records for that pseudonym at
  once. The assistant asks for confirmation first.

## Retention

Check-ins older than 90 days are deleted when the server starts and once a day after that. The
period is set in `server/policy.json`.

## Isolation

Every query is scoped to the caller's pseudonym, taken from the verified bearer token. Tests
check that one user can never read or delete another user's data.

## Logs

The server writes one JSON line per tool call: tool name, pseudonym and time. Note text is
never logged.

## Third parties

- **Scripted mode (default):** no third party. The simulator talks only to your MCP server, and
  the server only to a local SQLite file.
- **LLM mode (optional):** what you say, and the tool results, are sent to the Anthropic API so
  Claude can decide which tools to call. Turn this mode on only if that is acceptable.
- No analytics, no trackers, no advertising, no selling of data.
