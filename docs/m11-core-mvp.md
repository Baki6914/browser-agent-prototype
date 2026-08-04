# Milestone 11 Core MVP

## 1. Scope

Milestone 11 Core composes the existing synchronous browser-agent components
into an HTTP-submitted, in-memory MVP run while preserving application-owned
policy, confirmation, secret, completion, trace, and download boundaries.

## 2. Included capabilities

- URL-less and URL-based run creation.
- Chat-shell operator UI with a sticky composer.
- Controlled completion through proposal, **Finish run**, and
  **Continue working**.
- OpenAI-compatible provider requests with `tool_choice="required"`, exactly
  one tool call, and one bounded retry for tool-call cardinality failure.
- Structural provider traces in a separate store, endpoint, and UI.
- Policy-controlled browser actions, trusted confirmation, and exact replay.
- `request_secret`, `TransientSecretStore`, and handle-bound local
  `SecretFormApplier` execution outside model context and serializable history.
- Canonical download evidence, Downloads UI, HTTP retrieval, duplicate-name
  handling, cleanup, and per-run isolation.
- Existing diagnostics and HTTP security headers.

## 3. Explicit non-goals

- A site-specific login state machine.
- Generic automatic login orchestration for every website.
- Keyword-based or universal credential-semantic detection.
- Persistent sessions, restart recovery, databases, multi-worker
  coordination, production authentication, upload, antivirus scanning,
  checksums, quotas, or file-type inspection.
- Arbitrary host/server code execution or default page-context evaluation.

## 4. Security boundaries

Application policy—not model-visible provider guidance—authorizes browser
actions. `browser_fill_form` and `browser_type` continue to require policy
confirmation. A normal `user_input` is not trusted confirmation; only an
application-recorded confirmation can authorize one exact replay.

Real username, password, PIN, or OTP values belonging to a user are requested
through `request_secret` and applied locally. Public demo credentials explicitly
shown by a public demo page may be treated as page content solely for that demo
task and should not be repeated unnecessarily in the completion result.

Provider traces are structural only. They must not contain prompts, page
content, tool arguments, or secret values. Download success is established
only by the canonical `Application recorded download:` evidence emitted by the
application boundary.

## 5. Manual acceptance checklist

- [ ] Start a run without a URL and confirm the agent can request or navigate
  to the target.
- [ ] Complete a login on an approved public demo page using credentials
  explicitly displayed by that page.
- [ ] Confirm `browser_fill_form` pauses for policy confirmation and executes
  exactly once after approval.
- [ ] Confirm the login click receives its own policy confirmation and executes
  exactly once after approval.
- [ ] Observe the post-login page with `browser_snapshot`.
- [ ] Confirm completion first appears as a proposal; exercise both
  **Continue working** and **Finish run**.
- [ ] Download an approved public test file and verify the canonical download
  marker, Downloads card, and HTTP retrieval.
- [ ] Confirm provider traces remain structural and contain no prompt, page
  content, tool arguments, demo credentials, or real secrets.
- [ ] Confirm cleanup and run isolation after completion or cancellation.

## 6. Known limitations

- Perfect login automation is not guaranteed on every website.
- The model may misinterpret page semantics.
- Policy confirmation can add user-visible steps.
- Acceptance with public demo credentials does not prove the security of real
  credentials.
- `request_secret` is designed for real user secrets.
- Manual login and download acceptance are required before final closure.
