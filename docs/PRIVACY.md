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

- **Scripted mode with typing (default):** no third party. The simulator talks only to your MCP
  server, and the server only to a local SQLite file.
- **Voice input:** the simulator uses the browser's Web Speech API. In Chrome, speech is sent to
  Google to be transcribed. Typing avoids this.
- **LLM mode (optional):** what you say, and the tool results, are sent to the Anthropic API so
  Claude can decide which tools to call. Turn this mode on only if that is acceptable.
- **Nebius mode (optional):** your message and the tool results (for example your scores and
  the weekly reflection) are sent to Nebius Token Factory, where an NVIDIA open model generates
  the reply and decides which tools to call. Crisis language is caught first and is never sent.
  Recent turns of the conversation are sent along so the model has context.
- **Public demo (`DEMO_PUBLIC=true`):** synthetic data only. Each visitor gets a random
  pseudonym with an invented 14-day history; everything that pseudonym holds is deleted after
  two hours and nothing survives a restart. No Aawiz account is involved. If the host has set
  an ElevenLabs key, replies are spoken in Elin's voice: the reply text (which can repeat
  scores a visitor typed) is sent to ElevenLabs in the United States, up to a daily character
  budget; above it, and without a key, the browser's own voice is used. The page says so.
  The page asks visitors not to enter real personal information.
- **Elin's voice (optional):** the assistant's reply text, and nothing else, is sent to
  ElevenLabs in the United States to be spoken. Your words and notes are never sent. On crisis
  language, only the fixed safety message is spoken.
- **Aawiz mode (optional):** check-ins (as one named mood) and the messages you send to Elin
  are stored by Aawiz, on Microsoft Azure in the North Europe region (Ireland) according to the
  API's public host name, under the Aawiz privacy policy. Aawiz sends chat text to its language
  model provider to write Elin's replies. Energy, stress, sleep and notes stay in the local
  mirror only. Crisis language is never sent to Aawiz.
- Our server never receives or stores audio.
- No analytics, no trackers, no advertising, no selling of data.

## Aawiz mode and your rights

`export_my_data` and `delete_my_data` work on the local mirror. They never touch the Aawiz
account. The privacy summary and the delete answer say so out loud: your Aawiz account and
everything Elin knows is managed in the Aawiz app, where you can delete your account at any
time. The login session is kept in `data/aawiz_session.json` (git-ignored, owner-only
permissions): a refresh token and a session token, never printed or logged.
