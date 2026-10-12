// Contract regressions in the actual controller with a synthetic DOM; never browser QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const codec = require(process.argv[2]);
const shipped = fs.readFileSync(process.argv[3], 'utf8');
const sourceRevision = 'aabbccddeeff0011';
function classList() {
  const held = new Set();
  return {add(v){held.add(v);},toggle(v,on){if(on) held.add(v);else held.delete(v);},contains(v){return held.has(v);}};
}
function element(id='') {
  return {id,dataset:{},attrs:{},classList:classList(),children:[],offsetParent:{},
    setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},hasAttribute(k){return k in this.attrs;},
    appendChild(v){this.children.push(v);},querySelectorAll(){return this.children;},addEventListener(name,callback){(this.on ||= {})[name]=callback;},
    closest(){return null;},focus(){},getBoundingClientRect(){return {top:0};}};
}
function environment(page, state, options={}) {
  let url = new URL('file:///synthetic/workspace/'+page+codec.search(state)+(options.anchor?'#'+options.anchor:''));
  const events={},docEvents={},frames=[],notice={hidden:true,textContent:''};
  if(options.dismissible){notice.nodes=[];notice.append=(...nodes)=>notice.nodes.push(...nodes);notice.contains=node=>notice.nodes.includes(node);}
  const diagram={open:false,addEventListener(){}};
  const tabs=(options.tabs||[]).map(id=>element(id));
  const card=element(options.card||'e7');card.dataset={selection:card.id,kind:'finding'};
  card.closest=selector=>selector==='.chart-card.is-retired'&&options.retiredCard?card:null;
  const retired=options.retired?element('show-retired'):null;
  if(retired) retired.checked=false;
  const bar=element(),buttons=['','finding'].map(kind=>{
    const b=element();b.dataset.kind=kind;b.closest=()=>b;return b;
  });
  bar.children=buttons;bar.addEventListener=(name,callback)=>{bar[name]=callback;};
  bar.nextElementSibling={matches:()=>true,querySelectorAll:selector=>selector==='li.ev,li.relation-item'?[card]:[]};
  const document={documentElement:{dataset:{root:page.includes('/')?'../':'',page,source:sourceRevision,selection:''},classList:classList()},
    hidden:false,head:{appendChild(){}},getElementById(id){return id===card.id?card:id==='show-retired'?retired:tabs.find(t=>t.id===id)||null;},
    querySelector(selector){if(selector==='.state-notice') return notice;if(selector==='main') return {prepend(){}};
      if(selector==='.filters button.on') return buttons.find(b=>b.classList.contains('on'))||null;return null;},
    querySelectorAll(selector){if(selector==='main > section.tab') return tabs;if(selector==='.filters') return options.filters?[bar]:[];
      if(selector==='a[data-return]') return [back];if(selector==='.topbar, nav.tabs, nav.jump') return options.sticky||[];
      if(selector==='.relation-diagram') return [diagram];return [];},createElement(){return element();},
    addEventListener(name,callback){(docEvents[name] ||= []).push(callback);}};
  const back={href:'../index.html'};
  const history={state:null,replaceState(value,_,path){this.state=value;url=new URL(path,url);},pushState(value,_,path){this.replaceState(value,_,path);}};
  const location={get href(){return url.href;},get pathname(){return url.pathname;},get protocol(){return url.protocol;},get host(){return url.host;},
    get search(){return url.search;},get hash(){return url.hash;},reload(){}};
  const window={scrollY:0,AnalyticsWorkspaceState:codec,scrollTo(_,y){this.scrollY=y;},addEventListener(name,callback){(events[name] ||= []).push(callback);},
    getComputedStyle(el){return el.style;}};
  if('narrow' in options) window.matchMedia=query=>({matches:query==='(max-width: 640px)'&&options.narrow});
  card.getBoundingClientRect=()=>({top:820-window.scrollY});
  vm.runInNewContext(shipped,{window,document,location,history,URL,URLSearchParams,Set,
    requestAnimationFrame:f=>frames.push(f),setInterval(){},setTimeout(){return 1;},clearTimeout(){}});
  while(frames.length) frames.shift()();
  return {window,document,history,notice,tabs,card,bar,buttons,back,retired,diagram,docEvents,
    click(href){const a={href,hasAttribute(){return false;},closest(selector){return selector==='a[href]'?this:card;}};
      docEvents.click.forEach(f=>f({target:a}));return new URL(a.href,url);}};
}
const initial={v:1,page:'index.html',anchor:'timeline',tab:'timeline',filter:'',retired:false,
  selection:'',source:sourceRevision,card:'timeline',offset:72};
// Selecting on the second list snapshots that actual list/filter/card, even after index entry.
const listState=codec.next(initial,'lists/timeline-1.html');
const list=environment('lists/timeline-1.html',listState,{filters:true});
list.bar.click({target:list.buttons[1]});list.window.scrollY=700;
const detailURL=list.click('file:///synthetic/workspace/relations/selected-0.html#record');
const selected=codec.decode(detailURL.search).state;
assert.equal(selected.origin.page,'lists/timeline-1.html');
assert.equal(selected.origin.card,'e7');assert.equal(selected.origin.filter,'finding');
assert.equal(selected.origin.selection,'e7');assert.equal(selected.origin.offset,120);
assert.equal(selected.origin.origin,undefined);
const refreshed=environment('relations/selected-0.html',selected,{anchor:'record'});
const returned=codec.decode(new URL(refreshed.back.href,detailURL).search).state;
assert.deepEqual(returned,selected.origin);
const descendant=codec.next(selected,'runs/old.html','code');
assert.deepEqual(descendant.origin,selected.origin);
// Invalid saved page must leave an active usable tab and visible notice, not an empty index.
const wrong={...initial,page:'context.html'};
const fallback=environment('index.html',wrong,{tabs:['overview','timeline']});
assert.equal(fallback.tabs.filter(t=>t.classList.contains('on')).length,1);
assert.equal(fallback.tabs[0].classList.contains('on'),true);
assert.equal(fallback.notice.hidden,false);assert.match(fallback.notice.textContent,/another Workspace page/);
assert.equal(fallback.history.state.page,'index.html');
// Direct retired selection is revealed; page-to-page chart browsing carries its toggle state.
const direct=environment('lists/charts-1.html',{...initial,page:'lists/charts-1.html'},
  {anchor:'retired-card',card:'retired-card',retired:true,retiredCard:true});
assert.equal(direct.retired.checked,true);
assert.equal(direct.document.documentElement.classList.contains('show-retired'),true);
assert.equal(codec.next({...initial,retired:true},'lists/charts-0.html').retired,true);
assert.equal(codec.next({...initial,page:'lists/charts-0.html',retired:true},'lists/charts-1.html').retired,true);
// A newly opened anchor clears the measured sticky bars instead of landing under them.
const sticky=[{style:{position:'sticky',top:'0px'},offsetHeight:45},{style:{position:'sticky',top:'45px'},offsetHeight:40},
  {style:{position:'static',top:'auto'},offsetHeight:300}];
const opened=environment('runs/old.html',codec.next(initial,'runs/old.html','e7'),{anchor:'e7',sticky});
assert.equal(opened.window.scrollY,820-(45+40+12));
// A shown notice stays dismissible wherever the restored position lands.
const shown=environment('index.html',wrong,{tabs:['overview','timeline'],dismissible:true});
const dismiss=shown.notice.nodes.find(n=>n.className==='notice-close');
assert.equal(shown.notice.hidden,false);
dismiss.on.click();
assert.equal(shown.notice.hidden,true);
// Escape inside the notice closes it instead of leaving the page.
shown.notice.hidden=false;shown.back.click=()=>assert.fail('Escape in the notice left the page');
shown.docEvents.keydown.forEach(f=>f({key:'Escape',target:dismiss}));
assert.equal(shown.notice.hidden,true);
// The diagram opens by default only where the CSS keeps its three columns.
assert.equal(environment('relations/selected-0.html',selected,{narrow:false}).diagram.open,true);
assert.equal(environment('relations/selected-0.html',selected,{narrow:true}).diagram.open,false);
// A vanished saved card is reported and never replaced by scrolling to another position.
const vanished=environment('lists/timeline-1.html',{...listState,card:'gone',offset:40});
assert.equal(vanished.notice.hidden,false);assert.match(vanished.notice.textContent,/no longer in this page/);
assert.equal(vanished.window.scrollY,0);
