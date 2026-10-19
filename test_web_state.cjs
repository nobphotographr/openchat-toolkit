// Regression checks for the actual frontend's editor/navigation transitions.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

async function app({failSchedule=false}={}) {
  const nodes=new Map(), calls=[];
  const node=selector=>{
    if(!nodes.has(selector)) nodes.set(selector,{
      value:'',textContent:'',innerHTML:'',disabled:false,isConnected:true,
      classList:{toggle(){}},showModal(){this.open=true;},close(){this.open=false;}
    });
    return nodes.get(selector);
  };
  const context=vm.createContext({
    document:{querySelector:node,querySelectorAll:()=>[]},
    window:{addEventListener(){}},location:{hash:'',pathname:'/'},history:{replaceState(){}},
    setTimeout:()=>0,clearTimeout(){},setInterval(){},console,
    confirm:()=>true,
    fetch:async (url,options)=>{
      calls.push({url,data:options&&JSON.parse(options.body)});
      const bad=failSchedule&&url==='/api/schedule';
      return {ok:!bad,json:async()=>bad?{error:'confirmation_expired'}:
        url==='/api/overview'?{rooms:['テスト'],posts:[],templates:[],labels:{},paused:false}:{} };
    }
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'web/app.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  // Rendering is independent of this state regression; keep the old compose DOM
  // present until navigate executes, as in the real confirmation callback.
  vm.runInContext("render=async()=>{};page='compose';editor={id:'draft-1',version:2,room:'テスト',body:'確定対象'};dirty=false;overview={rooms:['テスト']};",context);
  node('#body').value='確定対象';node('#room').value='テスト';node('#when').value='2026-10-15T22:30';
  return {context,node,calls,evaluate:source=>vm.runInContext(source,context)};
}

test('history and inbox preserve separate search and pagination state',async()=>{
 const a=await app();
 a.evaluate("page='history';searchValues={query:'企画',room:'履歴の部屋'};offset=100;");
 await a.evaluate("navigate('unread')");
 assert.equal(a.evaluate("Object.keys(searchValues).length"),0);
 assert.equal(a.evaluate('offset'),0);
 a.evaluate("searchValues={query:'募集'};offset=0;");
 await a.evaluate("navigate('history')");
 assert.equal(a.evaluate('searchValues.query'),'企画');
 assert.equal(a.evaluate('searchValues.room'),'履歴の部屋');
 assert.equal(a.evaluate('offset'),100);
 await a.evaluate("navigate('unread')");
 assert.equal(a.evaluate('searchValues.query'),'募集');
});

test('bookmark control renders saved state rather than an ambiguous toggle',async()=>{
 const a=await app();
 a.evaluate("page='history';archiveRows=[{room:'部屋',timestamp:'2026-10-15T10:00:00+09:00',text:'本文',links:[],saved:true}];recordDetail(0);");
 assert.match(a.node('#archive-detail').innerHTML,/保存済み（解除）/);
 assert.match(a.node('#archive-detail').innerHTML,/aria-pressed="true"/);
 assert.doesNotMatch(a.node('#archive-detail').innerHTML,/T10:00:00/);
 a.evaluate('archiveRows[0].saved=false;recordDetail(0);');
 assert.match(a.node('#archive-detail').innerHTML,/☆ 保存する/);
});

test('confirmed post is not recaptured as a new draft during navigation',async()=>{
  const a=await app();
  a.evaluate("showConfirmation({row:{room:'テスト',body:'確定対象',due_at:'2026-10-16T08:00:00+09:00'},confirmation:'once'});");
  await a.node('#approve').onclick();
  assert.equal(a.evaluate('page'),'schedule');
  assert.equal(a.evaluate('editor.body'),'');
  assert.equal(a.evaluate('editor.id'),null);
  assert.equal(a.calls.filter(c=>c.url==='/api/schedule').length,1);
});

test('failed confirmation preserves the editable saved draft',async()=>{
  const a=await app({failSchedule:true});
  a.evaluate("showConfirmation({row:{room:'テスト',body:'確定対象',due_at:'2026-10-16T08:00:00+09:00'},confirmation:'once'});");
  await a.node('#approve').onclick();
  assert.equal(a.evaluate('page'),'compose');
  assert.equal(a.evaluate('editor.id'),'draft-1');
  assert.equal(a.evaluate('editor.body'),'確定対象');
  assert.equal(a.node('#dialog').open,true);
});

test('normal page navigation retains unsaved body, destination and time',async()=>{
  const a=await app();
  a.node('#body').value='編集中\n二行目';a.node('#room').value='変更した部屋';
  a.evaluate('dirty=true');
  await a.evaluate("navigate('history')");
  assert.equal(a.evaluate('editor.body'),'編集中\n二行目');
  assert.equal(a.evaluate('editor.room'),'変更した部屋');
  assert.equal(a.evaluate('editor.when'),'2026-10-15T22:30');
  assert.equal(a.evaluate('dirty'),true);
});

test('editing a returned draft restores its prior time in JST',async()=>{
  const a=await app();
  a.evaluate("page='drafts';overview={posts:[{id:'returned',status:'draft',body:'変更する本文',room:'テスト',version:3,due_at:'2026-10-15T23:00:00+00:00'}],labels:{draft:'下書き'}};");
  await a.evaluate("postDetail('returned')");
  await a.node('#edit-post').onclick();
  assert.equal(a.evaluate('editor.when'),'2026-10-16T08:00');
  assert.equal(a.evaluate('editor.body'),'変更する本文');
  assert.equal(a.evaluate('page'),'compose');
});
