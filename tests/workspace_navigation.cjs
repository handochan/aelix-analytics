// Nonbrowser controller test: mocked DOM/history, never a rendering or file-URL browser check.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const codec = require(process.argv[2]);
// The kit's words as a page has them: the runtime and both catalogs beside workspace.js (#96).
const assets = require('node:path').dirname(process.argv[3]);
const I18n = require(require('node:path').join(assets, 'workspace-i18n.js'));
I18n.register({en: JSON.parse(require('node:fs').readFileSync(require('node:path').join(assets, 'i18n', 'en.json'), 'utf8')),
  ko: JSON.parse(require('node:fs').readFileSync(require('node:path').join(assets, 'i18n', 'ko.json'), 'utf8'))});
const source = fs.readFileSync(process.argv[3], 'utf8');
const origin = {v:1,page:'index.html',anchor:'insight-e2',tab:'insights',filter:'finding',retired:true,
  selection:'old/図',source:'aabbccddeeff0011',card:'insight-e2',offset:115};
let url = new URL('file:///synthetic/workspace/runs/old.html' + codec.search({...origin,page:'runs/old.html',card:'chart-trend',anchor:'chart-trend',offset:120,origin}) + '#chart-trend');
let focused = null;
const events = {}, documentEvents = {}, frames = [];
const history = {state:null,replaceState(value, _, path) {this.state=value;url=new URL(path,url);},pushState(value, _, path) {this.replaceState(value,_,path);}};
const location = {get href(){return url.href;},get pathname(){return url.pathname;},get protocol(){return url.protocol;},get host(){return url.host;},get search(){return url.search;},get hash(){return url.hash;},reload(){}};
const card = {id:'chart-trend',dataset:{selection:'old%2F%E5%9B%B3'},offsetParent:{},attrs:{},
  closest(){return null;},getBoundingClientRect(){return {top:1500-window.scrollY};},hasAttribute(k){return k in this.attrs;},setAttribute(k,v){this.attrs[k]=v;},focus(){focused=this.id;}};
const back = {href:'../index.html',clicked:false,click(){this.clicked=true;}};
const notice = {hidden:true,textContent:''};
const document = {documentElement:{dataset:{root:'../',page:'runs/old.html',source:'aabbccddeeff0011',selection:'run%3Aold'},classList:{add(){},toggle(){}}},
  hidden:false,head:{appendChild(){}},getElementById(id){return id==='chart-trend'?card:null;},
  querySelector(selector){return selector==='.state-notice'?notice:selector==='a[data-return]'?back:null;},
  querySelectorAll(selector){return selector==='a[data-return]'?[back]:[];},
  addEventListener(k,f){(documentEvents[k] ||= []).push(f);},createElement(){return {remove(){}};}};
const window = {AnalyticsWorkspaceState:codec,AnalyticsI18n:I18n,scrollY:1300,scrollTo(_,y){this.scrollY=y;},
  addEventListener(k,f){(events[k] ||= []).push(f);}};
vm.runInNewContext(source,{window,document,location,history,URL,URLSearchParams,Set,
  requestAnimationFrame:f=>frames.push(f),setInterval(){},setTimeout(){return 1;},clearTimeout(){}});
function flush(){while(frames.length) frames.shift()();}
flush();
assert.equal(window.scrollY,1380); // restores the offset against real card geometry
assert.equal(focused,'chart-trend');
const returned = new URL(back.href,url);
assert.equal(returned.pathname,'/synthetic/workspace/index.html');
assert.deepEqual(codec.decode(returned.search).state,origin);
const link={href:'file:///synthetic/workspace/runs/new.html#chart-trend',hasAttribute(){return false;},
 closest(selector){return selector==='a[href]'?this:card;}};
documentEvents.click.forEach(f=>f({target:link}));
assert.deepEqual(codec.decode(new URL(link.href).search).state.origin,origin);
assert.equal(codec.decode(new URL(link.href).search).state.page,'runs/new.html');
documentEvents.keydown.forEach(f=>f({key:'Escape',target:{matches(){return false;}}}));
assert.equal(back.clicked,true);
// Back to an old unencoded entry resets saved descendant state instead of carrying its origin.
url=new URL('file:///synthetic/workspace/runs/old.html');window.scrollY=0;
events.popstate.forEach(f=>f({state:null}));flush();
assert.equal(history.state.origin,undefined);
assert.equal(codec.decode(url.search).state.origin,undefined);
