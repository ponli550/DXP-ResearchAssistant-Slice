// Drives the real page script in jsdom against a running stand-in server (tools/standin_server.py) and checks the
// interactions: source cards, nav, Show how it was found, expand all, feedback and review. Use a COPY of the database,
// because it records feedback and a review request. jsdom has no layout, so camera maths and hover are not covered.
//   cd tools && npm i jsdom@24 && node dom_check.mjs http://127.0.0.1:8767/
import {JSDOM} from 'jsdom';
const base=process.argv[2]||'http://127.0.0.1:8767/';
const q=encodeURIComponent('What did the board approve for the research assistant pilot for analysts?');
const out=[]; const ok=(name,cond,extra='')=>{out.push((cond?'PASS ':'FAIL ')+name+(extra?'  '+extra:'')); };
const dom=await JSDOM.fromURL(base+`?user=bob&q=${q}&run=1&reveal=0`,{runScripts:'dangerously',resources:'usable',pretendToBeVisual:true,
  beforeParse(w){w.fetch=(u,o)=>fetch(new URL(u,base),o);w.matchMedia=()=>({matches:false});
    w.IntersectionObserver=class{observe(){}disconnect(){}};w.Element.prototype.scrollIntoView=function(){this.__scrolled=true};}});
const w=dom.window,d=w.document;
const wait=async(fn,ms=8000)=>{const t=Date.now();while(Date.now()-t<ms){if(fn())return true;await new Promise(r=>setTimeout(r,50))}return false};
const click=el=>el.dispatchEvent(new w.MouseEvent('click',{bubbles:true}));
ok('page answered',await wait(()=>d.querySelector('#n-ans ol li')));
ok('canvas has 5 nodes',d.querySelectorAll('#canvas .node').length===5);
ok('removed claim shown in Checks',[...d.querySelectorAll('#n-chk .badge')].some(b=>b.textContent==='Removed'));
ok('kept claim shown as Quote on page',[...d.querySelectorAll('#n-chk .badge')].some(b=>b.textContent.startsWith('Quote on page')));
ok('Removed row never says verified',!/verified/i.test(d.querySelector('#n-chk').textContent));
// source card click toggles open (the touch/keyboard path of hover-expand)
const card=d.querySelector('#n-src .src'); click(card); ok('source card opens on click',card.classList.contains('open')&&card.getAttribute('aria-expanded')==='true'); click(card); ok('source card closes again',!card.classList.contains('open'));
// nav click focuses a node
const navBtn=[...d.querySelectorAll('#spy button')].find(b=>b.textContent==='Checks'); click(navBtn); ok('nav click moves the camera to Checks',w.eval('current && current.id')==='n-chk');
// show how it was found
const how=[...d.querySelectorAll('#n-ans button.chip')].find(b=>b.textContent==='Show how it was found'); ok('answer has a Show how it was found link',!!how);
click(how);
ok('switches to the steps tab',d.getElementById('steps').hidden===false&&d.getElementById('stage').hidden===true&&d.getElementById('tab-steps').getAttribute('aria-selected')==='true');
const first=d.getElementById('st-0'); ok('search step is expanded',first.querySelector('.stepbody').hidden===false);
const flashed=[...first.querySelectorAll('.src')].filter(c=>c.classList.contains('flash')); ok('the cited source card is highlighted',flashed.length===1&&flashed[0].classList.contains('open'),flashed[0]?.dataset.key);
// expand/collapse all steps
const toggle=d.querySelector('#steplist .toggle'); const before=toggle.textContent; click(toggle); const after=toggle.textContent;
ok('expand all toggles the label',before.startsWith('Expand')&&after.startsWith('Collapse'),before+' / '+after);
click(toggle); ok('collapse all hides step bodies',[...d.querySelectorAll('#steplist .stepbody')].every(b=>b.hidden));
ok('rail has one button per step plus question and conclusion',d.querySelectorAll('#rail button').length===d.querySelectorAll('#steplist .step').length);
// back to canvas
click(d.getElementById('tab-canvas')); ok('back to canvas',d.getElementById('stage').hidden===false&&d.getElementById('steps').hidden===true);
// thumbs up -> feedback recorded; review button -> request recorded
click([...d.querySelectorAll('#n-ans .fb button')].find(b=>b.textContent.includes('Helpful')&&!b.textContent.includes('Not')));
ok('thumbs up shows thanks',await wait(()=>d.querySelector('#n-ans .fb')?.textContent.includes('Thanks')));
click([...d.querySelectorAll('#n-next button')].find(b=>b.textContent==='Create review request'));
ok('review request recorded and says nobody was notified',await wait(()=>/recorded/.test(d.getElementById('n-next').textContent)&&/Nobody has been notified/.test(d.getElementById('n-next').textContent)));
console.log(out.join('\n')); console.log(out.filter(x=>x.startsWith('FAIL')).length+' failed of '+out.length); w.close(); process.exit(0);
