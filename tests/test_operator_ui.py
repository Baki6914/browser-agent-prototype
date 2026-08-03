"""Static safety and executable race tests for the operator resources."""

import json
import subprocess

from browser_agent.operator_ui import (
    OPERATOR_CSS, OPERATOR_HTML, OPERATOR_JAVASCRIPT,
    PROVIDER_TRACE_HTML, PROVIDER_TRACE_JAVASCRIPT,
)


def test_page_contains_required_controls() -> None:
    for identifier in (
        "task", "start", "run-id", "status", "question",
        "user-response", "approve", "reject", "secret-fields", "cancel",
        "result", "downloads", "activity", "error", "timeline", "timeline-empty",
        "conversation", "diagnostics",
        "diagnostics-panel", "toggle-diagnostics", "diagnostics-follow",
        "completion-panel", "completion-proposal", "completion-feedback",
        "finish-run", "continue-working",
        "composer", "start-composer", "composer-state",
        "provider-trace-link",
    ):
        assert f'id="{identifier}"' in OPERATOR_HTML
    assert 'id="start-url"' not in OPERATOR_HTML
    assert "Start URL" not in OPERATOR_HTML


def test_javascript_uses_existing_api_and_safe_dom_rendering() -> None:
    assert "api('runs'" in OPERATOR_JAVASCRIPT
    assert "/responses" in OPERATOR_JAVASCRIPT
    assert "/cancel" in OPERATOR_JAVASCRIPT
    assert "/files/" in OPERATOR_JAVASCRIPT
    assert "textContent" in OPERATOR_JAVASCRIPT
    assert "snapshot.audit_events" in OPERATOR_JAVASCRIPT
    assert "innerHTML" not in OPERATOR_JAVASCRIPT
    assert "credentials:'same-origin'" in OPERATOR_JAVASCRIPT
    assert "type:'completion'" in OPERATOR_JAVASCRIPT
    assert "approved:true" in OPERATOR_JAVASCRIPT
    assert "approved:false,feedback" in OPERATOR_JAVASCRIPT
    assert "event.key==='Enter'&&!event.shiftKey" in OPERATOR_JAVASCRIPT
    assert "body:JSON.stringify({task})" in OPERATOR_JAVASCRIPT
    assert "start_url" not in OPERATOR_JAVASCRIPT
    assert "message('user',task)" in OPERATOR_JAVASCRIPT


def test_chat_shell_and_responsive_diagnostics_layout() -> None:
    assert 'class="conversation-scroll"' in OPERATOR_HTML
    assert 'class="start-card"' not in OPERATOR_HTML
    assert "position:sticky;bottom:0" in OPERATOR_CSS
    assert "grid-template-columns:minmax(0,1fr) minmax(340px,var(--drawer))" in OPERATOR_CSS
    assert ".columns.diagnostics-closed{grid-template-columns:minmax(0,1fr)}" in OPERATOR_CSS
    assert "@media(max-width:700px)" in OPERATOR_CSS
    assert "position:fixed;inset:0" in OPERATOR_CSS
    assert "padding-left:2.4rem" in OPERATOR_CSS
    assert "overflow:auto" in OPERATOR_CSS
    assert "setDiagnosticsOpen(byId('diagnostics-panel').hidden)" in OPERATOR_JAVASCRIPT


def test_provider_trace_is_a_separate_safe_screen() -> None:
    assert 'src="/provider-trace.js"' in PROVIDER_TRACE_HTML
    assert 'href="/"' in PROVIDER_TRACE_HTML
    assert "/provider-traces" in PROVIDER_TRACE_JAVASCRIPT
    assert "setInterval(poll,1000)" in PROVIDER_TRACE_JAVASCRIPT
    assert "textContent" in PROVIDER_TRACE_JAVASCRIPT
    assert "innerHTML" not in PROVIDER_TRACE_JAVASCRIPT
    assert "raw prompt" not in PROVIDER_TRACE_HTML.lower()
    assert 'id="diagnostics-panel"' in OPERATOR_HTML


def test_interactions_downloads_and_no_free_form_message_endpoint() -> None:
    assert 'class="interaction-card downloads-card"' in OPERATOR_HTML
    assert 'id="confirmation-panel"' in OPERATOR_HTML
    assert 'id="completion-panel"' in OPERATOR_HTML
    assert 'id="secret-panel"' in OPERATOR_HTML
    assert "state.seenFiles.has(file.file_id)" in OPERATOR_JAVASCRIPT
    assert "state.finalShown" in OPERATOR_JAVASCRIPT
    assert "dataset.renderedId" in OPERATOR_JAVASCRIPT
    assert "link.href=`runs/${encodeURIComponent(snapshot.run_id)}/files/" in OPERATOR_JAVASCRIPT
    assert "/messages" not in OPERATOR_JAVASCRIPT
    assert "state.completionKeys.has(key)" in OPERATOR_JAVASCRIPT
    assert "state.completionTexts.has(snapshot.final_result)" in OPERATOR_JAVASCRIPT
    assert "state.completionKeys.clear()" in OPERATOR_JAVASCRIPT
    assert "message('agent',snapshot.completion_proposal||'')" not in OPERATOR_JAVASCRIPT


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
    assert "state.secretInteractionId!==snapshot.interaction_id" in OPERATOR_JAVASCRIPT
    assert "state={" in OPERATOR_JAVASCRIPT
    assert "values:" not in OPERATOR_JAVASCRIPT.split("state={", 1)[1].split("};", 1)[0]


def test_polling_is_bounded_and_non_overlapping() -> None:
    assert "setInterval(poll, 750)" in OPERATOR_JAVASCRIPT
    assert "state.pollPending" in OPERATOR_JAVASCRIPT
    assert "if (!state.runId || state.pollPending || state.submitPending)" in OPERATOR_JAVASCRIPT
    assert "state.submitPending" in OPERATOR_JAVASCRIPT


def test_runtime_race_interaction_and_secret_guards() -> None:
    harness = r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
class Element {
  constructor(id='') { this.id=id; this.textContent=''; this.value=''; this.hidden=false; this.disabled=false; this.dataset={}; this.children=[]; this.listeners={}; this.name=''; this.scrollTop=0; this.scrollHeight=0; this.clientHeight=0; this.attributes={}; this.classes=new Set(); this.classList={toggle:(name,force)=>{if(force===undefined)force=!this.classes.has(name);if(force)this.classes.add(name);else this.classes.delete(name);return force;},contains:name=>this.classes.has(name)}; }
  addEventListener(type, fn) { (this.listeners[type]||(this.listeners[type]=[])).push(fn); }
  dispatch(type, event={}) { for (const fn of this.listeners[type]||[]) fn(event); }
  click() { if (!this.disabled) return this.dispatch('click',{preventDefault(){}}); }
  submit() { return this.dispatch('submit',{preventDefault(){}}); }
  setAttribute(name,value) { this.attributes[name]=String(value); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=[...items]; }
  querySelectorAll(selector) {
    const all=[]; const visit=(node)=>{ for (const child of node.children) { if (selector === 'input' && child.tag === 'input') all.push(child); visit(child); } }; visit(this); return all;
  }
}
const ids = %IDS%;
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
elements['toggle-diagnostics'].textContent='Hide diagnostics';
elements['diagnostics-follow'].hidden=true;
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
globalThis.fetch = (path, options={}) => new Promise((resolve, reject) => {
  const isGet=!options.method; if (isGet) { getInFlight++; maxGetInFlight=Math.max(maxGetInFlight,getInFlight); }
  calls.push({path, options, finish(status, body, contentType='application/json') { if (isGet) getInFlight--; const raw=typeof body==='string'?body:JSON.stringify(body); resolve({ok:status<400,status,headers:{get:()=>contentType},text:async()=>raw}); }, fail(error) { if (isGet) getInFlight--; reject(error); }});
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
        "timeline", "timeline-empty", "timeline-wrap", "conversation", "diagnostics",
        "diagnostics-panel", "toggle-diagnostics", "diagnostics-follow", "columns",
        "completion-panel", "completion-proposal", "completion-feedback",
        "finish-run", "continue-working",
        "start-composer", "composer-state", "composer",
    ]))
    scenarios = r"""
(async () => {
  assert(!elements['diagnostics-panel'].hidden,'diagnostics panel was not initially open');
  assert(elements['toggle-diagnostics'].textContent==='Hide diagnostics','initial diagnostics toggle label was wrong');
  elements['toggle-diagnostics'].click();
  assert(elements['diagnostics-panel'].hidden && elements['toggle-diagnostics'].textContent==='Show diagnostics','diagnostics panel did not close');
  elements['toggle-diagnostics'].click();
  assert(!elements['diagnostics-panel'].hidden && elements['toggle-diagnostics'].textContent==='Hide diagnostics','diagnostics panel did not reopen');
  elements.diagnostics.scrollHeight=120; elements.diagnostics.clientHeight=40;
  elements.diagnostics.scrollTop=80; elements.diagnostics.dispatch('scroll');
  assert(elements['diagnostics-follow'].hidden,'auto-follow switched off while at the bottom');
  elements.start.click(); calls[0].finish(202, snap('run-a',0,'awaiting_user','a1')); await flush(); await flush(); intervals.at(-1)(); await flush();
  assert(renderedText(elements.conversation).includes('a1'),'agent question missing from conversation');
  assert(calls.length===2 && calls[1].path==='runs/run-a','initial poll missing');
  assert(!elements['diagnostics-panel'].hidden && elements['diagnostics-follow'].hidden,'new run did not reset diagnostics state');
  elements['user-response'].value='   '; elements['user-response'].dispatch('keydown',{key:'Enter',shiftKey:false,preventDefault(){}});
  assert(calls.length===2,'blank response was submitted');
  elements['user-response'].value='shift answer'; elements['user-response'].dispatch('keydown',{key:'Enter',shiftKey:true,preventDefault(){throw new Error('Shift+Enter was prevented');}});
  assert(calls.length===2,'Shift+Enter submitted a response');
  elements['user-response'].value='old answer'; elements['user-response'].dispatch('keydown',{key:'Enter',shiftKey:false,preventDefault(){}}); elements['user-response'].dispatch('keydown',{key:'Enter',shiftKey:false,preventDefault(){}});
  assert(calls.length===3,'interaction was not submitted');
  calls[2].finish(202,snap('run-a',2,'finished')); await flush(); await flush();
  assert(elements.status.textContent==='finished','older version overwrote newer snapshot');
  assert(renderedText(elements.conversation).includes('old answer'),'normal response missing from conversation');

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
  assert(renderedText(elements.diagnostics).includes('timeline B'),'newer timeline B was not rendered');
  intervals.at(-1)(); await flush(); calls[5].finish(200,snap('run-b',1,'failed',null,[],[event(1,'timeline A')])); await flush(); await flush();
  assert(renderedText(elements.diagnostics).includes('timeline B') && !renderedText(elements.diagnostics).includes('timeline A'),'equal-version timeline replaced B');
  assert(!renderedText(elements.diagnostics).includes('method: GET'),'routine successful GET polling produced diagnostics noise');
  assert(elements.status.textContent==='awaiting_user','equal-version response changed status');
  intervals.at(-1)(); await flush(); calls[6].finish(200,snap('run-b',0,'failed',null,[],[event(1,'lower timeline')])); await flush(); await flush();
  assert(!renderedText(elements.diagnostics).includes('lower timeline'),'lower-version timeline rendered');
  elements['user-response'].value='stale normal response'; intervals.at(-1)(); await flush();
  const malformed=[
    null,'text',{},event(12,'valid timeline'),
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
  const timelineText=renderedText(elements.diagnostics);
  assert(timelineText.includes('valid timeline'),'valid event beside malformed events was lost');
  for (const unsafe of ['UNKNOWN_','STATUS_CONTROL_','CONTROL_MARKER','BIDI','TOOL','MALFORMED_','SSSSSS','TTTTTT']) assert(!timelineText.includes(unsafe),'malformed event text rendered');
  assert(!timelineText.includes('undefined') && !timelineText.includes('[object Object]') && !timelineText.includes('{\"'),'raw or coerced data rendered');

  intervals.at(-1)(); await flush(); calls[8].finish(200,snap('run-b',3,'awaiting_secret','s1',['password'],[event(13,'terminal-safe history')])); await flush(); await flush();
  let input=elements['secret-fields'].querySelectorAll('input')[0]; input.value='never-print-this';
  elements['secret-panel'].submit(); elements['secret-panel'].submit();
  assert(calls.length===10,'duplicate interaction submission was not prevented');
  assert(input.value==='','secret was not cleared immediately');
  assert(!renderedText(elements.conversation).includes('never-print-this') && !renderedText(elements.diagnostics).includes('never-print-this'),'secret leaked into UI');
  calls[9].finish(500,{error:{message:'synthetic failure'}}); await flush(); await flush();
  assert(input.value==='','secret was not cleared after failed submission');

  intervals.at(-1)(); await flush(); calls[10].finish(200,snap('run-b',4,'awaiting_secret','s2',['password'],[event(13,'terminal-safe history')])); await flush(); await flush();
  input=elements['secret-fields'].querySelectorAll('input')[0]; input.value='never-print-this-either'; elements['secret-panel'].submit();
  assert(input.value==='','secret was not cleared before successful submission');
  calls[11].finish(202,snap('run-b',5,'finished',null,[],[event(13,'terminal-safe history')])); await flush(); await flush();
  assert(input.value==='','secret was not cleared after successful submission');
  assert(renderedText(elements.conversation).includes('Secure information submitted.'),'safe secret acknowledgement missing');
  assert(renderedText(elements.diagnostics).includes('terminal-safe history'),'terminal timeline was cleared');
  elements.start.click();
  calls[12].finish(202,snap('run-c',0,'finished',null,[],[event(1,'new-run history')])); await flush(); await flush();
  assert(renderedText(elements.diagnostics).includes('new-run history') && !renderedText(elements.diagnostics).includes('terminal-safe history'),'new run timeline was not reset and isolated');
  elements.diagnostics.scrollHeight=300; elements.diagnostics.clientHeight=100; elements.diagnostics.scrollTop=200;
  elements.diagnostics.dispatch('scroll');
  elements.start.click(); calls[13].finish(500,{error:{message:'synthetic follow check'}}); await flush(); await flush();
  assert(elements.diagnostics.scrollTop===300,'new diagnostic did not auto-follow to latest');
  elements.diagnostics.scrollTop=75; elements.diagnostics.dispatch('scroll');
  assert(!elements['diagnostics-follow'].hidden,'scrolling up did not reveal Jump to latest');
  elements.diagnostics.scrollHeight=450; const preservedScroll=elements.diagnostics.scrollTop;
  elements.start.click();
  assert(elements.diagnostics.scrollTop===preservedScroll,'diagnostic position changed before the next log');
  calls[14].finish(500,'PRIVATE RAW BODY','text/plain'); await flush(); await flush();
  assert(elements.diagnostics.scrollTop===preservedScroll,'new log moved diagnostics while auto-follow was off');
  elements['diagnostics-follow'].click();
  assert(elements.diagnostics.scrollTop===elements.diagnostics.scrollHeight && elements['diagnostics-follow'].hidden,'Jump to latest did not restore auto-follow');
  assert(maxGetInFlight===1,'polling requests overlapped');

  const unreadable=renderedText(elements.diagnostics);
  assert(unreadable.includes('status: 500') && unreadable.includes('contentType: text/plain') && unreadable.includes('length: 16') && unreadable.includes('parseError: SyntaxError'),'safe non-JSON diagnostics missing');
  assert(!unreadable.includes('PRIVATE RAW BODY'),'raw response body leaked');
  elements.start.click(); calls[15].finish(422,{error:{code:'validation_error',message:'Request validation failed.'}}); await flush(); await flush();
  assert(renderedText(elements.diagnostics).includes('validation_error') && renderedText(elements.diagnostics).includes('Request validation failed.'),'FastAPI envelope diagnostics missing');
  elements.start.click(); calls[16].fail(new TypeError('PRIVATE NETWORK DETAIL')); await flush(); await flush();
  assert(renderedText(elements.diagnostics).includes('Network') && renderedText(elements.diagnostics).includes('TypeError'),'network diagnostics missing');
  assert(!renderedText(elements.diagnostics).includes('PRIVATE NETWORK DETAIL'),'network detail leaked');
})().catch(error => { process.stderr.write(error.message); process.exitCode=1; });
"""
    completed = subprocess.run(
        ["node", "-e", harness + OPERATOR_JAVASCRIPT + scenarios],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert completed.returncode == 0, completed.stderr
