// Contract regressions in the actual controller with a synthetic DOM; never browser QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const codec = require(process.argv[2]);
// The kit's words as a page has them: the runtime and both catalogs beside workspace.js (#96).
const path = require('node:path');
const assets = path.dirname(process.argv[3]);
const I18n = require(path.join(assets, 'workspace-i18n.js'));
const ko = JSON.parse(fs.readFileSync(path.join(assets, 'i18n', 'ko.json'), 'utf8'));
I18n.register({en: JSON.parse(fs.readFileSync(path.join(assets, 'i18n', 'en.json'), 'utf8')), ko});
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
  if(options.dismissible){notice.nodes=[];notice.append=(...nodes)=>notice.nodes.push(...nodes);notice.contains=node=>notice.nodes.includes(node);
    // Like the DOM: the text is the nodes' text, and setting it replaces them.
    Object.defineProperty(notice,'textContent',{get(){return this.nodes.map(n=>typeof n==='string'?n:n.textContent||'').join('');},
      set(v){this.nodes=v?[v]:[];}});}
  const diagram={open:false,addEventListener(){}};
  // The build bar the language menu goes into, and the reader's storage: an object, or 'blocked'.
  const build=element();build.prepend=(...nodes)=>build.children.unshift(...nodes);
  const main={prepend(nav){main.nav=nav;}};
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
  const document={documentElement:{dataset:{root:page.includes('/')?'../':'',page,source:sourceRevision,selection:''},classList:classList(),
      attrs:{lang:'en'},setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return k in this.attrs?this.attrs[k]:null;}},
    hidden:false,head:{appendChild(){}},getElementById(id){return id===card.id?card:id==='show-retired'?retired:tabs.find(t=>t.id===id)||null;},
    querySelector(selector){if(selector==='.state-notice') return notice;if(selector==='main') return main;
      if(selector==='.topbar .build') return options.build?build:null;
      if(selector==='.filters button.on') return buttons.find(b=>b.classList.contains('on'))||null;return null;},
    querySelectorAll(selector){if(selector==='main > section.tab') return tabs;if(selector==='.filters') return options.filters?[bar]:[];
      if(selector==='a[data-return]') return [back];if(selector==='.topbar, nav.tabs, nav.jump') return options.sticky||[];
      if(selector==='.relation-diagram') return [diagram];return [];},createElement(){return element();},
    createTextNode(text){return {textContent:text};},
    addEventListener(name,callback){(docEvents[name] ||= []).push(callback);}};
  const back={href:'../index.html'};
  const history={state:null,replaceState(value,_,path){this.state=value;url=new URL(path,url);},pushState(value,_,path){this.replaceState(value,_,path);}};
  const location={get href(){return url.href;},get pathname(){return url.pathname;},get protocol(){return url.protocol;},get host(){return url.host;},
    get search(){return url.search;},get hash(){return url.hash;},reload(){}};
  const window={scrollY:0,AnalyticsWorkspaceState:codec,AnalyticsI18n:I18n,
    get localStorage(){if(options.storage==='blocked') throw new Error('SecurityError');return options.storage;},scrollTo(_,y){this.scrollY=y;},addEventListener(name,callback){(events[name] ||= []).push(callback);},
    getComputedStyle(el){return el.style;}};
  if('narrow' in options) window.matchMedia=query=>({matches:query==='(max-width: 640px)'&&options.narrow});
  card.getBoundingClientRect=()=>({top:820-window.scrollY});
  vm.runInNewContext(shipped,{window,document,location,history,URL,URLSearchParams,Set,
    requestAnimationFrame:f=>frames.push(f),setInterval(){},setTimeout(){return 1;},clearTimeout(){}});
  while(frames.length) frames.shift()();
  return {window,document,history,notice,tabs,card,bar,buttons,back,retired,diagram,docEvents,events,build,main,
    click(href){const a={href,hasAttribute(){return false;},closest(selector){return selector==='a[href]'?this:card;}};
      docEvents.click.forEach(f=>f({target:a}));return new URL(a.href,url);},
    reset(href){const a={href,hasAttribute(name){return name==='data-reset';},closest(selector){return selector==='a[href]'?this:card;}};
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
// --- the reader's language (#96): a working storage's choice first, else the URL state's ---
function memory(held={}){return {held,getItem:k=>(k in held?held[k]:null),setItem(k,v){held[k]=String(v);}};}
const detail='file:///synthetic/workspace/relations/';
// Storage blocked: the URL's language is shown, and links, the return link and a reset carry it.
const koState={...selected,lang:'ko'};
const korean=environment('relations/selected-0.html',koState,{anchor:'record',storage:'blocked',build:true});
assert.equal(I18n.language,'ko');
assert.equal(korean.document.documentElement.attrs.lang,'ko');
assert.equal(codec.decode(korean.click('file:///synthetic/workspace/runs/old.html#code').search).state.lang,'ko');
assert.equal(codec.decode(new URL(korean.back.href,detail).search).state.lang,'ko');
assert.equal(codec.decode(korean.reset(detail+'selected-0.html').search).state.lang,'ko');
// The menu, built by the script: each language in its own language, the page's selected.
const [menu]=korean.build.children;
const select=menu.children[1];
assert.deepEqual(select.children.map(o=>[o.value,o.attrs.lang,!!o.selected]),[['en','en',false],['ko','ko',true]]);
// Switching to English: <html lang>, the URL and the return link drop the language; a reset is empty.
select.value='en';select.on.change();
assert.equal(I18n.language,'en');
assert.equal(korean.document.documentElement.attrs.lang,'en');
assert.equal(korean.history.state.lang,undefined);
assert.equal(codec.decode(new URL(korean.back.href,detail).search).state.lang,undefined);
assert.equal(korean.reset(detail+'selected-0.html').search,'');
// And back to Korean where storage is blocked: the URL state carries it from now on.
select.value='ko';select.on.change();
assert.equal(korean.history.state.lang,'ko');
assert.equal(codec.decode(new URL(korean.back.href,detail).search).state.lang,'ko');
// A working storage's choice wins over the URL's, and a choice made on the page is kept there.
const stored=memory({'analytics-language':'en'});
const english=environment('relations/selected-0.html',koState,{anchor:'record',storage:stored,build:true});
assert.equal(I18n.language,'en');
assert.equal(codec.decode(english.click('file:///synthetic/workspace/runs/old.html#code').search).state.lang,undefined);
const picker=english.build.children[0].children[1];
picker.value='ko';picker.on.change();
assert.equal(stored.held['analytics-language'],'ko');
assert.equal(I18n.language,'ko');
// Shown again from the back-forward cache after another page chose English: it follows the choice.
stored.held['analytics-language']='en';
english.events.pageshow.forEach(f=>f({persisted:true}));
assert.equal(I18n.language,'en');
assert.equal(english.document.documentElement.attrs.lang,'en');
// A choice made in another tab reaches an open page too; another storage key does not.
stored.held['analytics-language']='ko';
english.events.storage.forEach(f=>f({key:'analytics-live'}));
assert.equal(I18n.language,'en');
english.events.storage.forEach(f=>f({key:'analytics-language'}));
assert.equal(I18n.language,'ko');
// A notice the script adds later is in the page's language, its Dismiss button too.
const noticed=environment('index.html',wrong,{tabs:['overview','timeline'],dismissible:true,
  storage:memory({'analytics-language':'ko'})});
const said=noticed.notice.nodes.find(n=>n.attrs&&n.attrs['data-i18n']==='notice.state_other_page');
assert.equal(said.textContent,ko['notice.state_other_page']);
assert.equal(noticed.notice.nodes.find(n=>n.className==='notice-close').textContent,ko['action.dismiss']);
// The section navigation the script builds names each section, and itself, in the page's language.
assert.deepEqual(noticed.main.nav.children.map(a=>[a.dataset.tab,a.textContent]),
  [['overview',ko['tab.overview']],['timeline',ko['tab.timeline']]]);
assert.equal(noticed.main.nav.attrs['aria-label'],ko['nav.sections']);
// A page opened directly (no origin) where storage is blocked: its own return link carries the
// language too, and Escape follows that link.
const opened2=environment('runs/old.html',{...initial,page:'runs/old.html',anchor:'',card:'',lang:'ko'},{storage:'blocked',build:true});
assert.equal(I18n.language,'ko');
const home=new URL(opened2.back.href,'file:///synthetic/workspace/runs/old.html');
assert.equal(home.pathname,'/synthetic/workspace/index.html');
assert.equal(codec.decode(home.search).state.lang,'ko');
const openedMenu=opened2.build.children[0].children[1];
openedMenu.value='en';openedMenu.on.change();
assert.equal(new URL(opened2.back.href,'file:///synthetic/workspace/runs/old.html').search,'');
// Back and Forward where storage is blocked: each history entry's language, as on load.
const travel=environment('index.html',{...initial,lang:'ko'},{storage:'blocked',build:true,tabs:['overview','timeline']});
assert.equal(I18n.language,'ko');
travel.events.popstate.forEach(f=>f({state:{...initial}}));
assert.equal(I18n.language,'en');
assert.equal(travel.document.documentElement.attrs.lang,'en');
travel.events.popstate.forEach(f=>f({state:{...initial,lang:'ko'}}));
assert.equal(I18n.language,'ko');
// A stored choice holds whatever the entry says.
const kept=environment('index.html',{...initial},{storage:memory({'analytics-language':'ko'}),build:true});
kept.events.popstate.forEach(f=>f({state:{...initial}}));
assert.equal(I18n.language,'ko');
