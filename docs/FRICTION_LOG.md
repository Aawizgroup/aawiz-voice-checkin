# Friction log: Amazon App Dev Hackathon 2026 (Alexa+ track)

Builder: AAwiz B.V. Project: Aawiz Voice Check-in.

## Entry 1
- **Task:** Test our self-hosted MCP server on a real Alexa+ device.
- **Steps:** Read the Alexa+ track page, the resources page and the Amazon Developer documentation; searched for Alexa+ MCP onboarding and account linking.
- **Expected:** A developer path (console, beta or test harness) to register a Streamable HTTP MCP server and try it on an Echo or the Alexa app.
- **Actual:** No public path found; the track points to simulating Alexa+ in a web app.
- **Severity:** High
- **Workaround:** Built a web simulator that acts as the Alexa+ host through a real MCP client, with a live panel of every tools/call.
- **Suggestion:** Publish an Alexa+ MCP test console and a short conformance checklist for hackathon builders.

## Entry 2
- **Task:** Choose an authentication model for per-user data.
- **Steps:** Looked for how Alexa+ authenticates to third-party MCP servers and links user accounts.
- **Expected:** Documentation and a sample for OAuth or account linking with MCP servers.
- **Actual:** Not found.
- **Severity:** Medium
- **Workaround:** Static bearer tokens mapped to pseudonyms; OAuth documented as future work.
- **Suggestion:** Document the auth flow Alexa+ uses with MCP servers and provide a minimal sample server.

## Entry 3
- **Task:** Design spoken responses that fit Alexa+.
- **Steps:** Searched for guidance on response length, latency budgets and how Alexa+ uses tool descriptions.
- **Expected:** Voice design guidelines for MCP-based Alexa+ integrations.
- **Actual:** Not found.
- **Severity:** Medium
- **Workaround:** Our own limits: two sentences for confirmations, at most 35 words for reflections, 10-second timeouts.
- **Suggestion:** Publish voice design guidelines for MCP tools on Alexa+.

## Entry 4
- **Task:** Verify our server with the official MCP Inspector.
- **Steps:** Started the Inspector, chose Streamable HTTP, entered http://localhost:8000/mcp.
- **Expected:** A prompt to add an Authorization header, or a clear 401 message.
- **Actual:** The server correctly answered 401 and the Inspector offered an OAuth sign-in, which a bearer-token server does not use.
- **Severity:** Low
- **Workaround:** The Inspector CLI with --header "Authorization: Bearer <token>".
- **Suggestion:** When a 401 comes without OAuth metadata, suggest adding a header. In hackathon docs, show a bearer-token Inspector example.

## Entry 5
- **Task:** Prove compliance with "spec 2025-11-25 or later".
- **Steps:** Connected with the official MCP Python SDK 2.3.0 client in its default mode.
- **Expected:** A clear way to confirm which protocol version Alexa+ will negotiate.
- **Actual:** The default mode negotiated 2026-07-28; proving 2025-11-25 needed a second, initialize-based connect mode.
- **Severity:** Low
- **Workaround:** The protocol test checks both modes.
- **Suggestion:** State on the track page which protocol versions Alexa+ accepts today.

## Entry 6
- **Task:** Plan the AWS Builder mini-challenge.
- **Steps:** Opened the resources page to request credits.
- **Expected:** Credits available for teams joining mid-hackathon.
- **Actual:** The page said credit codes ran out on 7 October.
- **Severity:** Low
- **Workaround:** Focused on the Alexa+ track and the Open Source mini-challenge.
- **Suggestion:** Show remaining credit availability on the landing page, or reserve a share for late joiners.
