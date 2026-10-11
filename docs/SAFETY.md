# Safety design

Aawiz Voice Check-in is not a medical device, does not diagnose, and does not give medical or
medication advice. These are the guardrails in the code.

## Crisis language

If a check-in note, the context for a suggestion, or anything said to the simulator contains
crisis language (for example "I want to end my life", "kill myself", "no reason to live"):

1. No reflection or suggestion is run on it.
2. Nothing from that check-in is stored, not the note and not the scores. We chose the
   conservative option: a moment like this should not become a data point.
3. The reply is a calm safety message that points to people:

   > I'm really glad you told me. You are not alone, and you deserve support right now. Please
   > contact someone now. In the Netherlands, call 113 or 0800-0113 (free), or chat at 113.nl.
   > In an emergency, call 112.

Resources are configured per region in `server/policy.json` (default `NL`; `US` gives 988 and
911). The phrase list lives in the same file, in English and Dutch, and is matched on whole
words, so "I want to diet" does not trigger it while "I want to die" does.

Ordinary hard days ("today was a hard day", "work is killing me slowly") are handled as normal
check-ins. Tests cover both directions.

**Limits:** a phrase list misses indirect wording. That is why the product never presents
itself as a safety net, and why the message always sends the person to real people.

## Non-clinical language

Tool outputs never use clinical labels. A guard checks every spoken reply against a list of
banned word stems (`diagnos`, `disorder`, `depress`, `anxiety`, `burnout`, `therap`, `symptom`
and more, in `server/policy.json`). If one ever appeared, the reply would be replaced with a
neutral line. A test runs every tool over many fixtures and fails on any banned word.

Reflections describe only the person's own check-ins and use hedged language: "your check-ins
suggest", "lower energy than last week".

## Micro-actions

Suggestions come only from a fixed list of six low-risk actions: 4-7-8 breathing, a short walk,
a glass of water, a 5-minute screen break, writing down one thing that went well, and a message
to a friend. The choice is deterministic from the latest scores (high stress leads to
breathing, low energy to a walk, low mood to a friend, short sleep to a screen break, a good
mood to one good thing, otherwise water), so it can be tested and explained.

## Aawiz mode and Elin's voice

The crisis check runs before any network call. On crisis language in a note, a suggestion's
context or a message for Elin, nothing is sent to Aawiz, and the only text that may go to
ElevenLabs is the fixed safety message. Tests assert zero requests to Aawiz in that case.

Everything Aawiz sends back (the weekly reflection lines, Elin's suggestion, Elin's reply)
passes the same banned-word guard. A reflection line or a suggestion reason that fails it is
dropped; a suggestion title that fails it falls back to the curated list; an Elin reply that
fails it is not spoken, and the assistant says to read it in the Aawiz app instead.

## LLM mode

When the optional LLM brain is used, crisis language is still caught before anything reaches
the model, the system prompt forbids diagnoses and medication advice, and the final reply goes
through the same banned-word guard.
