"""Dependency-free, import-safe resources for the local operator panel."""

from __future__ import annotations

from types import MappingProxyType


SECURITY_HEADERS = MappingProxyType({
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
})

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)

OPERATOR_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Browser Agent Operator</title><link rel="stylesheet" href="/operator.css"></head>
<body><main>
<header><p class="eyebrow">LOCAL OPERATOR</p><h1>Browser Agent</h1><p>Start a task, supervise meaningful actions, and keep secrets out of the model conversation.</p></header>
<section class="card" aria-labelledby="start-title"><h2 id="start-title">New run</h2>
<label for="start-url">Start URL</label><input id="start-url" type="url" required placeholder="https://example.com/" autocomplete="url">
<label for="task">Task</label><textarea id="task" required rows="4" placeholder="Describe the outcome you want"></textarea>
<button id="start" type="button">Start run</button></section>
<section class="card run" aria-live="polite"><div class="run-head"><div><span class="label">Run</span><strong id="run-id">None</strong></div><span id="status" class="status">idle</span></div>
<div id="activity" class="activity">Ready to start.</div><div id="error" class="error" role="alert"></div>
<div id="interaction" hidden><h2>Operator action</h2><p id="question"></p>
<div id="user-panel" hidden><label for="user-response">Response</label><textarea id="user-response" rows="3"></textarea><button id="send-response" type="button">Send response</button></div>
<div id="confirmation-panel" hidden><button id="approve" type="button">Approve</button><button id="reject" class="secondary" type="button">Reject</button></div>
<form id="secret-panel" hidden autocomplete="on"><div id="secret-fields"></div><button id="send-secret" type="submit">Submit securely</button></form></div>
<button id="cancel" class="danger" type="button" disabled>Cancel run</button>
<div id="result-wrap" hidden><h2>Final result</h2><pre id="result"></pre></div>
<div id="timeline-wrap"><h2>Action timeline</h2><p id="timeline-empty">No actions recorded yet.</p><ol id="timeline" aria-live="polite" aria-label="Action timeline"></ol></div>
<div id="downloads-wrap" hidden><h2>Downloads</h2><ul id="downloads"></ul></div></section>
</main><script src="/operator.js" defer></script></body></html>"""

OPERATOR_JAVASCRIPT = r"""'use strict';
(() => {
  const byId = (id) => document.getElementById(id);
  const terminal = new Set(['finished','failed','cancelled','step_limit_reached','decision_source_exhausted']);
  const state = { runId: null, generation: 0, latestVersion: -1, timer: null, pollPending: false, submitPending: false, secretInteractionId: null, userInteractionId: null, active: false };
  const status = byId('status'), activity = byId('activity'), error = byId('error');
  const requestId = () => (globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  const clearSecrets = () => byId('secret-fields').querySelectorAll('input').forEach((input) => { input.value = ''; });
  const showError = (message) => { error.textContent = message || ''; };
  async function api(path, options) {
    const response = await fetch(path, {credentials:'omit', ...(options || {})});
    let body = null;
    try { body = await response.json(); } catch (_) { throw new Error('The server returned an unreadable response.'); }
    if (!response.ok) throw new Error(body && body.error && body.error.message ? body.error.message : 'Request failed.');
    return body;
  }
  function renderDownloads(snapshot) {
    const list = byId('downloads'); list.replaceChildren();
    (snapshot.files || []).forEach((file) => {
      const item = document.createElement('li'), link = document.createElement('a');
      link.textContent = `${file.filename} (${file.size} bytes)`;
      link.href = `/runs/${encodeURIComponent(snapshot.run_id)}/files/${encodeURIComponent(file.file_id)}`;
      item.append(link); list.append(item);
    });
    byId('downloads-wrap').hidden = !(snapshot.files || []).length;
  }
  function renderTimeline(snapshot) {
    const events = Array.isArray(snapshot.audit_events) ? snapshot.audit_events : [];
    const nodes = [];
    const kinds = new Set(['system','tool','user_input','confirmation','secret']);
    const statuses = new Set(['info','success','pending','approved','rejected','failed','cancelled']);
    const containsControlOrFormat = (value) => /[\p{Cc}\p{Cf}]/u.test(value);
    const safeString = (value, limit) => typeof value === 'string' && value.trim().length > 0 && value.length <= limit && !containsControlOrFormat(value);
    const positiveInteger = (value) => Number.isSafeInteger(value) && value > 0;
    events.forEach((entry) => {
      if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) return;
      if (!positiveInteger(entry.sequence) || !safeString(entry.kind, 50) || !kinds.has(entry.kind) || !safeString(entry.status, 50) || !statuses.has(entry.status) || !safeString(entry.summary, 200)) return;
      if (entry.tool_name !== null && entry.tool_name !== undefined && !safeString(entry.tool_name, 100)) return;
      if (entry.step_number !== null && entry.step_number !== undefined && !positiveInteger(entry.step_number)) return;
      if (entry.replay_of_step_number !== null && entry.replay_of_step_number !== undefined && !positiveInteger(entry.replay_of_step_number)) return;
      const item = document.createElement('li');
      const heading = document.createElement('strong');
      heading.textContent = `${entry.sequence}. ${entry.status}: ${entry.summary}`;
      item.append(heading);
      const metadata = [];
      if (entry.tool_name != null) metadata.push(`tool ${entry.tool_name}`);
      if (entry.step_number != null) metadata.push(`step ${entry.step_number}`);
      if (entry.replay_of_step_number != null) metadata.push(`replay of step ${entry.replay_of_step_number}`);
      if (metadata.length) {
        const detail = document.createElement('span');
        detail.textContent = ` — ${metadata.join(', ')}`;
        item.append(detail);
      }
      nodes.push(item);
    });
    byId('timeline').replaceChildren(...nodes);
    byId('timeline-empty').hidden = nodes.length !== 0;
  }
  function renderSecrets(snapshot) {
    const container = byId('secret-fields'); container.replaceChildren();
    (snapshot.secret_fields || []).forEach((field) => {
      const label = document.createElement('label'), input = document.createElement('input');
      label.textContent = field === 'otp' ? 'One-time code' : field[0].toUpperCase() + field.slice(1);
      input.name = field; input.required = true; input.type = field === 'username' ? 'text' : 'password';
      input.autocomplete = field === 'username' ? 'username' : (field === 'password' ? 'current-password' : 'one-time-code');
      label.append(input); container.append(label);
    });
  }
  function current(selection, snapshot) {
    return state.runId === selection.runId && state.generation === selection.generation && snapshot.run_id === selection.runId && Number.isInteger(snapshot.version) && snapshot.version > state.latestVersion;
  }
  function render(snapshot, selection) {
    if (!current(selection, snapshot)) return false;
    state.latestVersion = snapshot.version; byId('run-id').textContent = snapshot.run_id; status.textContent = snapshot.status;
    activity.textContent = `${snapshot.status} · ${snapshot.step_count} step${snapshot.step_count === 1 ? '' : 's'}`;
    showError(snapshot.error ? snapshot.error.message : ''); byId('question').textContent = snapshot.question || '';
    const waiting = snapshot.status.startsWith('awaiting_'); byId('interaction').hidden = !waiting;
    byId('user-panel').hidden = snapshot.status !== 'awaiting_user';
    byId('confirmation-panel').hidden = snapshot.status !== 'awaiting_confirmation';
    byId('secret-panel').hidden = snapshot.status !== 'awaiting_secret';
    if (snapshot.status === 'awaiting_user') {
      if (state.userInteractionId !== snapshot.interaction_id) byId('user-response').value = '';
      state.userInteractionId = snapshot.interaction_id;
    } else { byId('user-response').value = ''; state.userInteractionId = null; }
    if (snapshot.status === 'awaiting_secret') {
      if (state.secretInteractionId !== snapshot.interaction_id) {
        clearSecrets(); renderSecrets(snapshot); state.secretInteractionId = snapshot.interaction_id;
      }
    } else { clearSecrets(); state.secretInteractionId = null; }
    state.active = !terminal.has(snapshot.status); byId('cancel').disabled = !state.active; byId('start').disabled = state.active;
    byId('result-wrap').hidden = snapshot.final_result == null; byId('result').textContent = snapshot.final_result || '';
    renderTimeline(snapshot); renderDownloads(snapshot);
    if (!state.active && state.timer) { clearInterval(state.timer); state.timer = null; }
    return true;
  }
  async function poll() {
    if (!state.runId || state.pollPending || state.submitPending) return;
    const selection = {runId:state.runId,generation:state.generation}; state.pollPending = true;
    try { const snapshot = await api(`/runs/${encodeURIComponent(selection.runId)}`); render(snapshot, selection); }
    catch (e) { if (state.runId === selection.runId && state.generation === selection.generation) showError(e.message); }
    finally { state.pollPending = false; }
  }
  function startPolling() { if (state.timer) clearInterval(state.timer); state.timer = setInterval(poll, 750); poll(); }
  async function submitInteraction(payloadFactory) {
    if (state.submitPending || !state.runId) return;
    const selection = {runId:state.runId,generation:state.generation};
    const interactionId = byId('interaction').dataset.interactionId;
    if (!interactionId) return;
    state.submitPending = true; document.querySelectorAll('#interaction button').forEach((button) => { button.disabled = true; });
    try { const payload = payloadFactory(interactionId); clearSecrets(); const snapshot = await api(`/runs/${encodeURIComponent(selection.runId)}/responses`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); if (render(snapshot, selection)) startPolling(); }
    catch (e) { if (state.runId === selection.runId && state.generation === selection.generation) showError(e.message); }
    finally { clearSecrets(); state.submitPending = false; document.querySelectorAll('#interaction button').forEach((button) => { button.disabled = false; }); }
  }
  const originalRender = render;
  render = (snapshot, selection) => { if (!current(selection, snapshot)) return false; byId('interaction').dataset.interactionId = snapshot.interaction_id || ''; return originalRender(snapshot, selection); };
  byId('start').addEventListener('click', async () => {
    if (state.submitPending) return; if (state.active) { showError('Cancel or wait for the active run to finish before starting another.'); return; } showError(''); state.submitPending = true;
    try { const snapshot = await api('/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start_url:byId('start-url').value,task:byId('task').value})}); state.generation += 1; state.runId = snapshot.run_id; state.latestVersion = -1; state.userInteractionId = null; state.secretInteractionId = null; byId('interaction').hidden = true; byId('result-wrap').hidden = true; byId('downloads-wrap').hidden = true; byId('timeline').replaceChildren(); byId('timeline-empty').hidden = false; activity.textContent = ''; showError(''); const selection = {runId:state.runId,generation:state.generation}; render(snapshot, selection); startPolling(); }
    catch (e) { showError(e.message); } finally { state.submitPending = false; }
  });
  byId('send-response').addEventListener('click', () => submitInteraction((interaction_id) => ({type:'user_input',request_id:requestId(),interaction_id,text:byId('user-response').value})));
  byId('approve').addEventListener('click', () => submitInteraction((interaction_id) => ({type:'confirmation',request_id:requestId(),interaction_id,approved:true})));
  byId('reject').addEventListener('click', () => submitInteraction((interaction_id) => ({type:'confirmation',request_id:requestId(),interaction_id,approved:false})));
  byId('secret-panel').addEventListener('submit', (event) => { event.preventDefault(); submitInteraction((interaction_id) => { const values = {}; byId('secret-fields').querySelectorAll('input').forEach((input) => { values[input.name] = input.value; }); return {type:'secret',request_id:requestId(),interaction_id,values}; }); });
  byId('cancel').addEventListener('click', async () => { if (state.submitPending || !state.runId) return; const selection = {runId:state.runId,generation:state.generation}; state.submitPending = true; try { const snapshot = await api(`/runs/${encodeURIComponent(selection.runId)}/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestId()})}); render(snapshot, selection); } catch(e) { if (state.runId === selection.runId && state.generation === selection.generation) showError(e.message); } finally { state.submitPending = false; } });
})();"""

OPERATOR_CSS = """:root{color-scheme:light;--ink:#14221d;--muted:#61706a;--paper:#f5f3ec;--card:#fff;--accent:#176b52;--line:#d8ded8;--danger:#a22f32}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,sans-serif}main{max-width:850px;margin:auto;padding:clamp(18px,4vw,50px)}header{margin-bottom:28px}h1{font-size:clamp(2rem,7vw,4rem);line-height:1;margin:.15em 0}.eyebrow,.label{color:var(--accent);font-weight:700;letter-spacing:.12em;font-size:.78rem}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:clamp(18px,4vw,30px);margin:18px 0;box-shadow:0 8px 28px #183d2b0c}label{display:block;font-weight:650;margin:14px 0 6px}input,textarea{width:100%;font:inherit;padding:11px;border:1px solid #aebbb4;border-radius:8px}button{font:inherit;font-weight:700;border:0;border-radius:8px;padding:11px 16px;margin:14px 8px 0 0;background:var(--accent);color:white;cursor:pointer}button.secondary{background:#53615b}button.danger{background:var(--danger)}button:disabled{opacity:.5;cursor:not-allowed}.run-head{display:flex;align-items:center;justify-content:space-between;gap:15px}.run-head strong{display:block;overflow-wrap:anywhere}.status{background:#e2eee8;color:#15513f;border-radius:999px;padding:5px 11px}.activity{color:var(--muted);margin:18px 0}.error{color:var(--danger);min-height:1.5em}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f1;padding:14px;border-radius:8px}#timeline{padding-left:1.4rem}#timeline li{margin:.55rem 0;overflow-wrap:anywhere}#timeline li span{color:var(--muted)}a{color:var(--accent)}[hidden]{display:none!important}@media(max-width:520px){.run-head{align-items:flex-start;flex-direction:column}button{width:100%;margin-right:0}}"""
