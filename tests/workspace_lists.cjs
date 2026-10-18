// #97's script behaviour with a synthetic DOM (never browser QA): the way back named by where it
// goes, plotly read only when a figure shows, and the narrow screen's page menu.
// node workspace_lists.cjs <workspace-state.js> <workspace.js>
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const codec = require(process.argv[2]);
const assets = path.dirname(process.argv[3]);
const I18n = require(path.join(assets, 'workspace-i18n.js'));
const en = JSON.parse(fs.readFileSync(path.join(assets, 'i18n', 'en.json'), 'utf8'));
const ko = JSON.parse(fs.readFileSync(path.join(assets, 'i18n', 'ko.json'), 'utf8'));
I18n.register({en, ko});
const shipped = fs.readFileSync(process.argv[3], 'utf8');
const source = 'aabbccddeeff0011';

function classList() {
  const held = new Set();
  return {add(v){held.add(v);},remove(v){held.delete(v);},toggle(v,on){if(on===undefined?!held.has(v):on) held.add(v);else held.delete(v);return held.has(v);},
    contains(v){return held.has(v);}};
}
function element(tag='span') {
  const el = {localName:tag,tagName:tag.toUpperCase(),dataset:{},attrs:{},classList:classList(),children:[],offsetParent:{},textContent:'',
    setAttribute(k,v){this.attrs[k]=String(v);},getAttribute(k){return k in this.attrs?this.attrs[k]:null;},removeAttribute(k){delete this.attrs[k];},
    hasAttribute(k){return k in this.attrs;},appendChild(v){this.children.push(v);v.parent=this;return v;},append(...v){v.forEach(c=>this.appendChild(c));},
    replaceChildren(...v){this.children=v;},querySelectorAll(){return [];},querySelector(){return null;},
    addEventListener(name,callback){(this.on ||= {})[name]=callback;},closest(){return null;},focus(){this.focused=true;},
    matches(){return false;},getBoundingClientRect(){return {top:0};}};
  return el;
}
// A way back as the generator writes it: an arrow, then the kit's words in a <k-w>.
function backLink(home, english, homeKey) {
  const words = element('k-w');
  words.textContent = english;
  words.replaceWith = replacement => { link.parts[1] = replacement; };
  const link = {href:home,dataset:{},parts:[{textContent:'←'},words],
    getAttribute(k){return k==='href'?home:k==='data-back'?homeKey:null;},
    get innerHTML(){return this.parts.map(p=>p.localName==='k-w'?`<k-w>${p.textContent}</k-w>`:p.textContent).join(' ');},
    set innerHTML(v){this.parts=[{textContent:'←'},Object.assign(element('k-w'),{textContent:/<k-w>(.*)<\/k-w>/.exec(v)[1],replaceWith:words.replaceWith})];},
    querySelector(selector){
      if(selector==='[data-back-words]') return this.parts.find(p=>p.attrs&&'data-back-words' in p.attrs)||null;
      if(selector===I18n.WORD) return this.parts.find(p=>p.localName==='k-w')||null;
      return null;},
    append(v){this.parts.push(v);},
    words(){return this.parts.slice(1).map(p=>p.textContent).join(' ');}};
  return link;
}
function page(name, state, options={}) {
  let url = new URL('file:///synthetic/workspace/'+name+(state?codec.search(state):''));
  const listeners={},docListeners={},frames=[],head=[];
  const back = backLink(options.home||'../index.html#insights', options.homeWords||'Back to Insights', 'back.tab.insights');
  const topbar = element('header'), pageMenu = element('div');
  topbar.insertBefore=(node)=>{topbar.inserted=node;};
  const figures=(options.figures||[]).map(n=>{const f=element('div');f.dataset.fig='fig'+n;return f;});
  const notice={hidden:true,textContent:''};
  const html={dataset:{root:name.includes('/')?'../':'',page:name,source,selection:'',...(options.plotly?{plotly:'../assets/plotly.min.js?v=1'}:{})},
    classList:classList(),attrs:{lang:'en'},style:{props:{},setProperty(k,v){this.props[k]=v;}},
    setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return k in this.attrs?this.attrs[k]:null;}};
  const document={documentElement:html,hidden:false,head:{appendChild(node){head.push(node);}},
    getElementById(id){if(typeof id!=='string') return null;if(id==='page-menu') return options.menu?pageMenu:null;if(id.startsWith('fig')) return {textContent:'{"data":[],"layout":{}}'};return null;},
    querySelector(selector){if(selector==='.state-notice') return notice;if(selector==='main') return {prepend(){}};
      if(selector==='.topbar') return options.menu?topbar:null;if(selector==='a[data-return]') return back;return null;},
    querySelectorAll(selector){if(selector==='a[data-return]') return [back];if(selector==='.plotly-chart[data-fig]') return figures;return [];},
    createElement(tag){return element(tag);},createTextNode(text){return {textContent:text};},
    addEventListener(name,callback){(docListeners[name] ||= []).push(callback);}};
  const history={state:null,replaceState(value,_,p){this.state=value;url=new URL(p,url);},pushState(value,_,p){this.replaceState(value,_,p);}};
  const location={get href(){return url.href;},get pathname(){return url.pathname;},get protocol(){return url.protocol;},
    get host(){return url.host;},get search(){return url.search;},get hash(){return url.hash;},reload(){}};
  const window={scrollY:0,AnalyticsWorkspaceState:codec,AnalyticsI18n:I18n,localStorage:options.storage||null,
    scrollTo(){},addEventListener(name,callback){(listeners[name] ||= []).push(callback);},getComputedStyle(){return {};},
    matchMedia:query=>({matches:query==='(max-width: 40em)'&&!!options.narrow})};
  vm.runInNewContext(shipped,{window,document,location,history,URL,URLSearchParams,Set,Promise,
    requestAnimationFrame:f=>frames.push(f),setInterval(){},setTimeout(){return 1;},clearTimeout(){}});
  while(frames.length) frames.shift()();
  return {window,document,back,head,figures,topbar,pageMenu,docListeners,html,listeners,
    flush(){while(frames.length) frames.shift()();}};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  // --- the way back names where it goes ---------------------------------------------------------
  const browsing = {v:1,page:'index.html',anchor:'insight-e2',tab:'insights',filter:'',retired:false,selection:'',source,card:'insight-e2',offset:0};
  const cases = [
    [{...browsing,page:'index.html',tab:'timeline'}, 'back.tab.timeline', 'Back to Timeline'],
    [{...browsing,page:'index.html',tab:'charts'}, 'back.tab.charts', 'Back to Visualizations'],
    [{...browsing,page:'lists/kind-finding-2.html',tab:'overview'}, 'back.list.kind', 'Back to the list'],
    [{...browsing,page:'lists/feedback-1.html',tab:'overview'}, 'back.list.feedback', 'Back to Workflow feedback'],
    [{...browsing,page:'lists/insights-0.html',tab:'overview'}, 'back.tab.insights', 'Back to Insights'],
    // The Analysis map tab and the open items' list (#98, Codex review R08).
    [{...browsing,page:'index.html',tab:'map'}, 'back.tab.map', 'Back to Analysis map'],
    [{...browsing,page:'lists/open-1.html',tab:'overview'}, 'back.list.open', 'Back to the open items'],
  ];
  // A page opened directly is the origin of the pages a reader opens from it (codec.next):
  // the way back names it too (Codex review of #97, R3).
  for (const [page, key, english] of [['runs/r.html', 'back.page.run', 'Back to the run'],
      ['data/t.html', 'back.page.data', 'Back to the dataset'], ['context.html', 'back.page.context', 'Back to Context history'],
      ['relations/bb-0.html', 'back.page.relations', 'Back to the relations']]) {
    const held = {...browsing, page, tab:'overview', anchor:'', card:''};
    const state = codec.next(held, 'relations/aa-0.html', 'record');
    assert.equal(state.origin.page, page);
    cases.push([held, key, english]);
  }
  for (const [origin, key, english] of cases) {
    const state = codec.next(origin, 'relations/aa-0.html', 'record');
    const shown = page('relations/aa-0.html', state);
    const words = shown.back.querySelector('[data-back-words]');
    assert.ok(words, key);
    assert.equal(words.attrs['data-i18n'], key);
    assert.equal(words.textContent, english);
    assert.ok(shown.back.href.includes(origin.page), shown.back.href);
  }
  // Opened directly: the page's own way back, its own words.
  const direct = page('relations/aa-0.html', null);
  assert.equal(direct.back.words(), 'Back to Insights');
  assert.equal(direct.back.querySelector('[data-back-words]'), null);
  // In Korean, from a kind's list.
  const korean = page('relations/aa-0.html', {...codec.next({...browsing,page:'lists/kind-note-0.html'}, 'relations/aa-0.html'), lang:'ko'});
  assert.equal(korean.back.querySelector('[data-back-words]').textContent, ko['back.list.kind']);
  // Independent review of #97 (I3): the words of a way back switch with the page and never go back
  // to the language the page loaded in. Opened directly: the page's own <k-w>, which the page
  // switches; a later language change leaves it to that switching.
  let chosen = 'en';
  const storage = {getItem(){return chosen;}, setItem(){}};
  const opened = page('relations/aa-0.html', null, {storage});
  const own = opened.back.parts[1];
  own.textContent = ko['back.tab.insights'];  // as the page's switching writes it
  chosen = 'ko';
  opened.listeners.storage.forEach(f => f({key: I18n.KEY}));
  opened.flush();
  assert.equal(opened.back.parts[1], own);
  assert.equal(own.textContent, ko['back.tab.insights']);
  // An origin's words, then Back to an entry without one: the page's own key, in the language now.
  chosen = 'ko';
  const returned = page('relations/aa-0.html', codec.next(browsing, 'relations/aa-0.html', 'record'), {storage});
  assert.equal(returned.back.querySelector('[data-back-words]').textContent, ko['back.tab.insights']);
  returned.listeners.popstate.forEach(f => f({state: {v:1,page:'relations/aa-0.html',anchor:'',tab:'overview',filter:'',
    retired:false,selection:'',source,card:'',offset:0}}));
  returned.flush();
  const home = returned.back.querySelector('[data-back-words]');
  assert.equal(home.attrs['data-i18n'], 'back.tab.insights');
  assert.equal(home.textContent, ko['back.tab.insights']);

  // --- plotly: read when a figure shows ----------------------------------------------------------
  const wide = page('runs/r.html', null, {plotly:true, figures:[1, 2]});
  assert.equal(wide.head.length, 1, 'one script for every figure');
  assert.equal(wide.head[0].src, '../assets/plotly.min.js?v=1');
  const drawn = [];
  wide.window.Plotly = {newPlot(el, data, layout, config){drawn.push(config);}};
  wide.head[0].onload();
  await flush();
  assert.equal(drawn.length, 2);
  assert.deepEqual(drawn.map(c => c.displayModeBar), ['hover', 'hover']);
  const narrow = page('runs/r.html', null, {plotly:true, figures:[1], narrow:true});
  const narrowDrawn = [];
  narrow.window.Plotly = {newPlot(el, data, layout, config){narrowDrawn.push(config);}};
  narrow.head[0].onload();
  await flush();
  assert.equal(narrowDrawn[0].displayModeBar, false, 'no tool bar over a narrow figure');
  const failed = page('runs/r.html', null, {plotly:true, figures:[1]});
  failed.head[0].onerror();
  await flush();
  assert.equal(failed.figures[0].attrs['data-i18n'], 'chart.plotly_missing');
  const none = page('index.html', null, {figures:[]});
  assert.equal(none.head.length, 0, 'a page without a figure reads no plotly');

  // --- the narrow screen's page menu ------------------------------------------------------------
  const menu = page('index.html', null, {menu:true});
  const button = menu.topbar.inserted;
  assert.ok(button && menu.html.classList.contains('js-menu'));
  assert.equal(button.attrs['aria-controls'], 'page-menu');
  assert.equal(button.attrs['aria-expanded'], 'false');
  assert.equal(button.attrs['aria-label'], en['chrome.menu']);
  button.on.click();
  assert.ok(menu.topbar.classList.contains('menu-open'));
  assert.equal(button.attrs['aria-expanded'], 'true');
  // Escape closes the menu and gives its button the focus; it does not leave the page.
  const target = {matches(){return false;}};
  let left = false;
  menu.back.click = () => { left = true; };
  menu.docListeners.keydown.forEach(f => f({key:'Escape', target}));
  assert.ok(!menu.topbar.classList.contains('menu-open') && button.focused && !left);
  menu.docListeners.keydown.forEach(f => f({key:'Escape', target}));
  assert.ok(left, 'with the menu closed, Escape goes back as before');
  console.log('workspace lists: ok');
})().catch(error => { console.error(error); process.exit(1); });
