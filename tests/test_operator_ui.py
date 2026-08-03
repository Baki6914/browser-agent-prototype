"""Static safety and executable race tests for the operator resources."""

import json
import subprocess

from browser_agent.operator_ui import OPERATOR_HTML, OPERATOR_JAVASCRIPT


def test_page_contains_required_controls() -> None:
    for identifier in (
        "start-url", "task", "start", "run-id", "status", "question",
        "user-response", "approve", "reject", "secret-fields", "cancel",
        "result", "downloads", "activity", "error", "timeline", "timeline-empty",
    ):
        assert f'id="{identifier}"' in OPERATOR_HTML


def test_javascript_uses_existing_api_and_safe_dom_rendering() -> None:
    assert "api('/runs'" in OPERATOR_JAVASCRIPT
    assert "/responses" in OPERATOR_JAVASCRIPT
    assert "/cancel" in OPERATOR_JAVASCRIPT
    assert "/files/" in OPERATOR_JAVASCRIPT
    assert "textContent" in OPERATOR_JAVASCRIPT
    assert "snapshot.audit_events" in OPERATOR_JAVASCRIPT
    assert "innerHTML" not in OPERATOR_JAVASCRIPT
    assert "credentials:'omit'" in OPERATOR_JAVASCRIPT


def test_browser_storage_external_assets_and_payload_logging_are_absent() -> None:
    combined = OPERATOR_HTML + OPERATOR_JAVASCRIPT
    for forbidden in (
        "localStorage", "sessionStorage", "indexedDB", "IndexedDB",
        "console.log", 'src="http://', 'src="https://',
        'href="http://', 'href="https://', "cdn",
    ):
        assert forbidden not in combined


def test_secrets_are_dynamic_cleared_and_not_global() -> None:
    assert "snapshot.secret_fields" in OPERATOR_JAVASCRIPT
    assert "input.value = ''" in OPERATOR_JAVASCRIPT
    assert OPERATOR_JAVASCRIPT.count("clearSecrets();") >= 3
    assert "state.secretInteractionId !== snapshot.interaction_id" in OPERATOR_JAVASCRIPT
    assert "state = {" in OPERATOR_JAVASCRIPT
    assert "values:" not in OPERATOR_JAVASCRIPT.split("state = {", 1)[1].split("};", 1)[0]


def test_polling_is_bounded_and_non_overlapping() -> None:
    assert "setInterval(poll, 750)" in OPERATOR_JAVASCRIPT
    assert "state.pollPending" in OPERATOR_JAVASCRIPT
    assert "if (!state.runId || state.pollPending || state.submitPending)" in OPERATOR_JAVASCRIPT
    assert "state.submitPending" in OPERATOR_JAVASCRIPT


def test_runtime_race_interaction_and_secret_guards() -> None:
    harness = r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
class Element {
  constructor(id='') { this.id=id; this.textContent=''; this.value=''; this.hidden=false; this.disabled=false; this.dataset={}; this.children=[]; this.listeners={}; this.name=''; }
  addEventListener(type, fn) { this.listeners[type]=fn; }
  click() { if (!this.disabled && this.listeners.click) return this.listeners.click({preventDefault(){}}); }
  submit() { if (this.listeners.submit) return this.listeners.submit({preventDefault(){}}); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=[...items]; }
  querySelectorAll(selector) {
    const all=[]; const visit=(node)=>{ for (const child of node.children) { if (selector === 'input' && child.tag === 'input') all.push(child); visit(child); } }; visit(this); return all;
  }
}
const ids = %IDS%;
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
globalThis.document = {
  getElementById(id) { return elements[id]; },
  createElement(tag) { const item=new Element(); item.tag=tag; return item; },
  querySelectorAll(selector) { return selector === '#interaction button' ? ['send-response','approve','reject','send-secret'].map(id=>elements[id]).filter(Boolean) : []; }
};
globalThis.crypto = {randomUUID: () => 'synthetic-request-id'};
const intervals=[];
globalThis.setInterval = (fn) => { intervals.push(fn); return intervals.length; };
globalThis.clearInterval = () => {};
const calls=[]; let getInFlight=0, maxGetInFlight=0;
globalThis.fetch = (path, options={}) => new Promise((resolve) => {
  const isGet=!options.method; if (isGet) { getInFlight++; maxGetInFlight=Math.max(maxGetInFlight,getInFlight); }
  calls.push({path, options, finish(status, body) { if (isGet) getInFlight--; resolve({ok:status<400, json:async()=>body}); }});
});
const flush = () => new Promise(resolve => setImmediate(resolve));
const snap = (run_id, version, status, interaction_id=null, fields=[], audit_events=[]) => ({run_id,version,status,step_count:version,interaction_id,question:interaction_id,secret_fields:fields,files:[],audit_events,final_result:null,error:null});
const event = (sequence, summary) => ({sequence,kind:'system',status:'success',summary,tool_name:null,step_number:null,replay_of_step_number:null});
const renderedText = (node) => node.textContent + node.children.map(renderedText).join('');
""".replace("%IDS%", json.dumps([
        "status", "activity", "error", "secret-fields", "downloads", "downloads-wrap",
        "run-id", "question", "interaction", "user-panel", "confirmation-panel",
        "secret-panel", "user-response", "cancel", "start", "result-wrap", "result",
        "start-url", "task", "send-response", "approve", "reject", "send-secret",
        "timeline", "timeline-empty", "timeline-wrap",
    ]))
    scenarios = r"""
(async () => {
  elements.start.click(); calls[0].finish(202, snap('run-a',0,'awaiting_user','a1')); await flush(); await flush(); intervals.at(-1)(); await flush();
  assert(calls.length===2 && calls[1].path==='/runs/run-a','initial poll missing');
  elements['user-response'].value='old answer'; elements['send-response'].click();
  assert(calls.length===3,'interaction was not submitted');
  calls[2].finish(202,snap('run-a',2,'finished')); await flush(); await flush();
  assert(elements.status.textContent==='finished','older version overwrote newer snapshot');

  elements.start.click(); calls[3].finish(202,snap('run-b',0,'running')); await flush(); await flush();
  assert(elements['run-id'].textContent==='run-b','new run was not selected');
  assert(calls.length===4,'new polling overlapped old polling');
  calls[1].finish(200,snap('run-a',1,'awaiting_user','obsolete')); await flush(); await flush();
  assert(elements['run-id'].textContent==='run-b' && elements.status.textContent==='running','old run overwrote new run');
  intervals.at(-1)(); await flush(); assert(calls.length===5,'poll did not resume');
  const beforeStart=calls.length; elements.start.click();
  assert(calls.length===beforeStart,'active run was replaced by Start');
  assert(elements.start.disabled,'Start was not clearly disabled for active run');
  calls[4].finish(200,snap('run-b',1,'awaiting_user','b1',[],[event(1,'timeline B')])); await flush(); await flush();
  assert(renderedText(elements.timeline).includes('timeline B'),'newer timeline B was not rendered');
  intervals.at(-1)(); await flush(); calls[5].finish(200,snap('run-b',1,'failed',null,[],[event(1,'timeline A')])); await flush(); await flush();
  assert(renderedText(elements.timeline).includes('timeline B') && !renderedText(elements.timeline).includes('timeline A'),'equal-version timeline replaced B');
  assert(elements.status.textContent==='awaiting_user','equal-version response changed status');
  intervals.at(-1)(); await flush(); calls[6].finish(200,snap('run-b',0,'failed',null,[],[event(1,'lower timeline')])); await flush(); await flush();
  assert(!renderedText(elements.timeline).includes('lower timeline'),'lower-version timeline rendered');
  elements['user-response'].value='stale normal response'; intervals.at(-1)(); await flush();
  const malformed=[
    null,'text',{},event(1,'valid timeline'),
    {sequence:2,kind:'unknown',status:'success',summary:'UNKNOWN_KIND_MARKER'},
    {sequence:3,kind:'system',status:'unknown',summary:'UNKNOWN_STATUS_MARKER'},
    {sequence:4,kind:'system',status:'success\n',summary:'STATUS_CONTROL_MARKER'},
    {sequence:5,kind:'system',status:'success',summary:'SUMMARY\nCONTROL_MARKER'},
    {sequence:6,kind:'system',status:'success',summary:'BIDI\u2066MARKER'},
    {sequence:7,kind:'tool',status:'success',summary:'bad tool',tool_name:'TOOL\u0000MARKER'},
    {sequence:8,kind:'system',status:'success',summary:'S'.repeat(201)},
    {sequence:9,kind:'tool',status:'success',summary:'bad long tool',tool_name:'T'.repeat(101)},
    {sequence:10,kind:'system',status:'success',summary:'bad object',tool_name:{secret:'MALFORMED_SECRET_MARKER'}},
    {sequence:11,kind:'system',status:'success',summary:'bad step',step_number:0}
  ];
  calls[7].finish(200,snap('run-b',2,'awaiting_user','b2',[],malformed)); await flush(); await flush();
  assert(elements['user-response'].value==='','new awaiting_user retained stale text');
  const timelineText=renderedText(elements.timeline);
  assert(timelineText.includes('valid timeline'),'valid event beside malformed events was lost');
  for (const unsafe of ['UNKNOWN_','STATUS_CONTROL_','CONTROL_MARKER','BIDI','TOOL','MALFORMED_','SSSSSS','TTTTTT']) assert(!timelineText.includes(unsafe),'malformed event text rendered');
  assert(!timelineText.includes('undefined') && !timelineText.includes('[object Object]') && !timelineText.includes('{\"'),'raw or coerced data rendered');

  intervals.at(-1)(); await flush(); calls[8].finish(200,snap('run-b',3,'awaiting_secret','s1',['password'],[event(1,'terminal-safe history')])); await flush(); await flush();
  let input=elements['secret-fields'].querySelectorAll('input')[0]; input.value='never-print-this';
  elements['secret-panel'].submit(); elements['secret-panel'].submit();
  assert(calls.length===10,'duplicate interaction submission was not prevented');
  assert(input.value==='','secret was not cleared immediately');
  calls[9].finish(500,{error:{message:'synthetic failure'}}); await flush(); await flush();
  assert(input.value==='','secret was not cleared after failed submission');

  intervals.at(-1)(); await flush(); calls[10].finish(200,snap('run-b',4,'awaiting_secret','s2',['password'],[event(1,'terminal-safe history')])); await flush(); await flush();
  input=elements['secret-fields'].querySelectorAll('input')[0]; input.value='never-print-this-either'; elements['secret-panel'].submit();
  assert(input.value==='','secret was not cleared before successful submission');
  calls[11].finish(202,snap('run-b',5,'finished',null,[],[event(1,'terminal-safe history')])); await flush(); await flush();
  assert(input.value==='','secret was not cleared after successful submission');
  assert(renderedText(elements.timeline).includes('terminal-safe history'),'terminal timeline was cleared');
  elements.start.click();
  calls[12].finish(202,snap('run-c',0,'finished',null,[],[event(1,'new-run history')])); await flush(); await flush();
  assert(renderedText(elements.timeline).includes('new-run history') && !renderedText(elements.timeline).includes('terminal-safe history') && elements.timeline.children.length===1,'new run timeline was not reset and isolated');
  assert(maxGetInFlight===1,'polling requests overlapped');
})().catch(error => { process.stderr.write(error.message); process.exitCode=1; });
"""
    completed = subprocess.run(
        ["node", "-e", harness + OPERATOR_JAVASCRIPT + scenarios],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert completed.returncode == 0, completed.stderr
