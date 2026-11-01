// Drives the real page script in jsdom against a running stand-in server (tools/standin_server.py) and checks the
// interactions: live steps, the retry, the answer, the citation and its page, feedback, review, tables and find. Use a COPY
// of the database, because it records questions, feedback and a review request. jsdom has no layout, so the proof line's
// geometry, hover and dragging are not covered here; those were checked in a real browser.
//   cd tools && npm i jsdom@24 && node dom_check.mjs http://127.0.0.1:8778/
import {JSDOM} from 'jsdom';
const base=process.argv[2]||'http://127.0.0.1:8778/';
const out=[]; const ok=(name,cond,extra='')=>{out.push((cond?'PASS ':'FAIL ')+name+(extra?'  '+extra:''));};
const dom=await JSDOM.fromURL(base+'?user=bob',{runScripts:'dangerously',resources:'usable',pretendToBeVisual:true,
  beforeParse(w){w.fetch=(u,o={})=>fetch(new URL(u,base),{...o,signal:undefined});w.TextDecoder=TextDecoder;w.matchMedia=()=>({matches:true});
    w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(v){this.returnValue=v||'';this.open=false;this.dispatchEvent(new w.Event('close'))};}});
const w=dom.window,d=w.document;
const wait=async(fn,ms=20000)=>{const t=Date.now();while(Date.now()-t<ms){if(fn())return true;await new Promise(r=>setTimeout(r,40))}return false};
const click=el=>el.dispatchEvent(new w.MouseEvent('click',{bubbles:true}));
const steps=()=>[...d.querySelectorAll('.turn:last-child .step-text')].map(s=>s.textContent);
const q=d.getElementById('q');
const go=()=>d.getElementById('go').getAttribute('aria-label');
ok('the reports load',await wait(()=>d.querySelectorAll('.report input').length>=5));
const desk=d.getElementById('desk');
ok('it starts with only the question box',desk.classList.contains('no-pane')&&desk.classList.contains('rail-closed')&&d.getElementById('work').classList.contains('is-empty'));
ok('suggestions are offered',await wait(()=>d.querySelectorAll('#suggestions button').length===3));
click(d.querySelectorAll('#suggestions button')[1]);
ok('a suggestion fills the box, sets the mode and gets out of the way',q.value.length>10&&d.getElementById('modeLabel').textContent==='Table across reports'&&d.getElementById('suggestions').hidden);
click(d.getElementById('askMode'));
ok('the side panel opens on request',(click(d.getElementById('stripHistory')),!desk.classList.contains('rail-closed')));
click(d.getElementById('railClose'));
// ask: the steps arrive one at a time
q.value='What did the board approve for the research assistant pilot for analysts?';
d.getElementById('composer').requestSubmit();
ok('the search step appears first',await wait(()=>steps()[0]?.startsWith('Searched')));
ok('the button becomes Stop while working',go()==='Stop');
ok('the box has left the start screen',!d.getElementById('work').classList.contains('is-empty'));
ok('the first draft fails its check and says why',await wait(()=>steps().some(s=>s.includes('1 removed'))));
ok('the retry is announced',await wait(()=>steps().some(s=>s.startsWith('First draft failed'))));
ok('the fabricated claim is never shown',!d.getElementById('thread').textContent.includes('Output doubled'));
ok('the answer arrives',await wait(()=>d.querySelector('.turn:last-child .claim')));
ok('the steps fold into one line',/^Checked in/.test(d.querySelector('.turn:last-child .activity summary').textContent)&&!d.querySelector('.turn:last-child .activity').open);
ok('the button returns to Ask',go()==='Ask');
ok('the page panel arrives with the quote marked',await wait(()=>d.querySelector('#ptext mark'))&&!desk.classList.contains('no-pane'));
ok('the citation shows as active',d.querySelector('.cite').getAttribute('aria-pressed')==='true');
ok('no label claims more than the check does',!/verified/i.test(d.getElementById('thread').textContent));
// feedback and review
click(d.querySelector('[aria-label="Helpful"]'));
ok('a rating is thanked',await wait(()=>d.querySelector('.actions-note')?.textContent.includes('Thanks')));
click([...d.querySelectorAll('.actions button')].find(b=>b.textContent==='Ask someone to review'));
d.querySelector('.actions form').requestSubmit();
ok('a review says nobody was notified',await wait(()=>/Nobody is notified/.test(d.querySelector('.turn:last-child').textContent)));
// ticks limit the search
const boxes=[...d.querySelectorAll('.report input')];
click(boxes[0]);
ok('unticking updates the scope at once',d.getElementById('scopeLabel').textContent===`${boxes.length-1} of ${boxes.length} reports`);
ok('the two lists of ticks agree',[...d.querySelectorAll(`[data-tick="${boxes[0].dataset.tick}"]`)].every(b=>!b.checked));
click(boxes[0]);
// tables
click([...d.querySelectorAll('.actions button')].find(b=>b.textContent.startsWith('Ask across')));
ok('a table appears with one column per report',await wait(()=>d.querySelectorAll('.matrix thead th').length===boxes.length+1));
ok('its cells settle',await wait(()=>/answered/.test(d.querySelector('.table-state')?.textContent||''),60000),d.querySelector('.table-state')?.textContent);
// deleting asks first, and Cancel keeps it
const tables=()=>d.querySelectorAll('.table-head').length;
const bin=()=>d.querySelector('.table-head [aria-label="Delete this table"]');
const dialog=d.getElementById('confirm');
const before=tables();
click(bin());
ok('deleting opens a dialog that names the table',await wait(()=>dialog.open)&&d.getElementById('confirmTitle').textContent.includes('Delete the table'));
ok('Cancel holds the focus',d.activeElement===d.getElementById('confirmCancel'));
click(d.getElementById('confirmCancel'));
ok('cancelling keeps the table',await wait(()=>!dialog.open)&&tables()===before);
click(bin());
await wait(()=>dialog.open);
click(d.getElementById('confirmOk'));
ok('confirming deletes the table',await wait(()=>tables()===before-1));
// find, no AI
click(d.getElementById('findBtn'));
ok('find mode is shown in the box',d.getElementById('modeLabel').textContent==='Exact words'&&go()==='Find');
q.value='growth';
d.getElementById('composer').requestSubmit();
ok('find lists marked results',await wait(()=>d.querySelector('.hit mark')));
click(d.querySelector('.hit'));
ok('a result opens its page with the words marked',await wait(()=>d.querySelector('#ptext mark')));
// the history lists the question, and a saved answer reopens
ok('history lists the question',await wait(()=>[...d.querySelectorAll('#savedList .saved-row')].some(r=>r.textContent.includes('research assistant pilot'))));
console.log(out.join('\n')); console.log(out.filter(x=>x.startsWith('FAIL')).length+' failed of '+out.length); w.close(); process.exit(0);
