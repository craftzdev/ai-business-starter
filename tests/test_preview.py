"""Standard-library tests; execute the actual inline JavaScript using Node.js.

No npm packages or Python third-party modules are needed. The DOM double verifies
UI wiring, not browser layout; viewport and native focus checks are manual.
"""
import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "preview.html").read_text(encoding="utf-8")


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.scripts = {}
        self.script = None
        self.feed(HTML)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "script":
            self.script = attrs["id"]
            self.scripts[self.script] = ""

    def handle_endtag(self, tag):
        if tag == "script":
            self.script = None

    def handle_data(self, data):
        if self.script:
            self.scripts[self.script] += data


DOC = Document()
HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const nodes = new Map();
let focused = null;
class Element {
  constructor(tag='div') {this.tag=tag;this.children=[];this.attributes={};this.hidden=false;this.value='';this.checked=false;this.disabled=false;this.textContent='';this.listeners={};this.classList={toggle(){}};}
  set id(id){this._id=id;nodes.set(id,this);} get id(){return this._id;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(k,v){this.attributes[k]=v;}
  focus(){focused=this.id;}
  addEventListener(k,fn){this.listeners[k]=fn;}
  showModal(){this.isOpen=true;}
  close(){this.isOpen=false;this.listeners.close?.();}
}
function memory(initial=null){
  let data=initial;return {failRead:false,failWrite:false,failRemove:false,
    getItem(key){assert.equal(key,'ai-business-starter.v1');if(this.failRead)throw Error('blocked');return data;},
    setItem(key,value){assert.equal(key,'ai-business-starter.v1');if(this.failWrite)throw Error('quota');data=value;},
    removeItem(key){assert.equal(key,'ai-business-starter.v1');if(this.failRemove)throw Error('blocked');data=null;},
    get data(){return data;}
  };
}
const disk=memory();
const context=vm.createContext({crypto:webcrypto,Uint8Array,localStorage:disk,document:{
  getElementById(id){if(!nodes.has(id)){const e=new Element();e.id=id;}return nodes.get(id);},
  createElement(tag){return new Element(tag);}
}});
vm.runInContext(CORE,context);
const app=vm.runInContext('App',context);
function fill(s,i){app.open(s,i);for(const k of app.steps[i].keys)app.edit(s,k,'テストの内容');if(i===0){s.checks.fact=true;s.checks.secret=true;}assert.equal(Object.keys(app.complete(s,i)).length,0);}
function full(){const s=app.fresh();for(let i=0;i<4;i++)fill(s,i);return s;}
function ui(){vm.runInContext(UI,context);}
function click(id){const e=nodes.get(id);assert.ok(e,`Missing ${id}`);assert.ok(!e.disabled);e.onclick();}
function input(key,value){const e=nodes.get('input-'+key);e.value=value;e.oninput();}
function state(){return vm.runInContext('store.state',context);}
"""


class PreviewTests(unittest.TestCase):
    def run_js(self, source):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to test the inline JavaScript (no npm packages).")
        program = ("const CORE=" + json.dumps(DOC.scripts["app-core"]) + ";\n"
                   + "const UI=" + json.dumps(DOC.scripts["app-ui"]) + ";\n"
                   + HARNESS + "\n{\n" + source + "\n}")
        result = subprocess.run([node, "-"], input=program, text=True,
                                capture_output=True, cwd=ROOT, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_initial_screen_and_order(self):
        self.run_js("""
ui();assert.equal(nodes.get('progressText').textContent,'0/4');
assert.equal(nodes.get('steps').children.length,4);
assert.match(nodes.get('nextTitle').textContent,/生成AI/);
assert.equal(nodes.get('summaryPanel').hidden,true);
for(let i=1;i<4;i++)assert.equal(nodes.get('steps').children[i].children[0].disabled,true);
assert.equal(app.open(state(),2),false);assert.ok(app.complete(state(),2).order);
assert.equal(state().events.length,0);assert.equal(disk.data,null);
""")

    def test_locked_stage_overviews_keep_work_inaccessible(self):
        self.run_js("""
ui();click('nextButton');
for(let first=0;first<3;first++){
 for(let i=first+1;i<4;i++){
  const row=nodes.get('steps').children[i],button=row.children[0],overview=row.children[1];
  assert.equal(button.disabled,true);assert.equal(overview.tag,'p');
  assert.equal(overview.hidden,false);assert.equal(overview.textContent,app.steps[i].description);
  assert.ok(overview.textContent.length>20);assert.equal(overview.children.length,0);
  const before=JSON.stringify(state()),fields=nodes.get('fields').children;
  button.onclick();assert.equal(JSON.stringify(state()),before);
  assert.equal(nodes.get('fields').children,fields);
  assert.equal(app.open(state(),i),false);assert.ok(app.complete(state(),i).order);
 }
 fill(state(),first);vm.runInContext('renderHome()',context);click('nextButton');
 assert.equal(nodes.get('workTitle').textContent,app.steps[first+1].title);
 assert.equal(nodes.get('steps').children[first+1].children.length,1);
}
""")

    def test_ui_mark_incomplete_preserves_all_inputs_and_persists(self):
        for stage in range(4):
            with self.subTest(stage=stage):
                self.run_js("const stage=" + str(stage) + ";" + """
const initial=full();for(const key of Object.keys(initial.values))initial.values[key]='保持する入力：'+key;
disk.setItem(app.KEY,JSON.stringify(initial));ui();
assert.equal(nodes.get('incompleteButton').hidden,true);
nodes.get('steps').children[stage].children[0].onclick();
assert.equal(nodes.get('incompleteButton').hidden,false);
const values=JSON.stringify(state().values),checks=JSON.stringify(state().checks),events=JSON.stringify(state().events);
click('incompleteButton');assert.equal(nodes.get('progressText').textContent,`${stage}/4`);
assert.equal(nodes.get('summaryPanel').hidden,true);assert.equal(nodes.get('work').hidden,false);
assert.equal(nodes.get('incompleteButton').hidden,true);assert.equal(nodes.get('incompleteButton').disabled,true);
assert.equal(focused,'completeButton');assert.match(nodes.get('workFeedback').textContent,/入力内容.*保持/);
assert.equal(nodes.get('nextDescription').textContent,app.steps[stage].description);
assert.equal(JSON.stringify(state().values),values);assert.equal(JSON.stringify(state().checks),checks);
assert.equal(JSON.stringify(state().events),events);
for(const key of app.steps[stage].keys)assert.equal(nodes.get('input-'+key).value,state().values[key]);
const restored=app.createStore(disk).state;
assert.equal(JSON.stringify(restored.values),values);assert.equal(JSON.stringify(restored.checks),checks);
for(let i=0;i<4;i++)assert.equal(restored.completed[i],i<stage);
for(let i=stage+1;i<4;i++)assert.equal(nodes.get('steps').children[i].children[0].disabled,true);
for(let i=stage;i<4;i++){click('nextButton');click('completeButton');}
assert.equal(nodes.get('progressText').textContent,'4/4');assert.equal(JSON.stringify(state().values),values);
""")

    def test_ui_startup_read_failure_warns_and_allows_edit_and_retry(self):
        self.run_js("""
const original=JSON.stringify(full());disk.setItem(app.KEY,original);disk.failRead=true;ui();
function warning(){
 assert.match(nodes.get('storageStatus').textContent,/読み取れない/);
 assert.match(nodes.get('storageStatus').textContent,/変更を保存できません/);
 assert.match(nodes.get('storageStatus').textContent,/再読み込み.*失われます/);
 assert.equal(nodes.get('retrySave').hidden,false);
}
warning();click('nextButton');input('trial','保存失敗中の入力');warning();
assert.equal(state().values.trial,'保存失敗中の入力');assert.equal(disk.data,original);
for(const id of ['factCheck','secretCheck']){nodes.get(id).checked=true;nodes.get(id).onchange();}
click('completeButton');assert.equal(nodes.get('progressText').textContent,'1/4');warning();
disk.failRead=false;disk.failWrite=true;click('retrySave');assert.match(nodes.get('storageStatus').textContent,/再読み込み.*失われます/);
disk.failWrite=false;click('retrySave');assert.equal(nodes.get('retrySave').hidden,true);
assert.match(nodes.get('storageStatus').textContent,/保存しました/);
const restored=app.createStore(disk).state;
assert.equal(restored.values.trial,'保存失敗中の入力');assert.equal(app.next(restored),1);
""")

    def test_required_length_unicode_and_safety(self):
        self.run_js("""
const s=app.fresh();
for(let i=0;i<4;i++)for(const key of app.steps[i].keys){
  for(const value of ['', '  \\n　', 'あ'.repeat(501)]){s.values[key]=value;assert.ok(app.validate(s,i)[key]);}
  for(const value of ['あ','あ'.repeat(500),'😀'.repeat(500)]){s.values[key]=value;assert.equal(app.validate(s,i)[key],undefined);}
  s.values[key]='😀'.repeat(501);assert.ok(app.validate(s,i)[key]);
}
s.values.trial='試したいこと';assert.ok(app.validate(s,0).checks);
s.checks.fact=true;assert.ok(app.validate(s,0).checks);
s.checks.secret=true;assert.equal(Object.keys(app.validate(s,0)).length,0);
""")

    def test_progress_edit_and_invalidation(self):
        self.run_js("""
const s=app.fresh();for(let i=0;i<4;i++){fill(s,i);assert.equal(app.next(s),i+1);}
assert.equal(s.events.filter(e=>e.name==='step_completed').length,4);
app.edit(s,'customer','変更した顧客');app.complete(s,1);
assert.equal(app.next(s),4);assert.equal(s.events.filter(e=>e.name==='step_completed').length,4);
app.edit(s,'problem',' ');assert.equal(app.next(s),1);assert.equal(s.completed.filter(Boolean).length,1);
assert.equal(app.open(s,3),false);assert.equal(s.values.action,'テストの内容');
app.edit(s,'problem','修正');app.complete(s,1);app.complete(s,2);app.complete(s,3);assert.equal(app.next(s),4);
s.checks.fact=false;app.invalidate(s,0);assert.equal(s.completed.filter(Boolean).length,0);
""")

    def test_storage_roundtrip_and_reload_deduplication(self):
        self.run_js("""
const disk=memory();const st=app.createStore(disk);for(let i=0;i<4;i++)fill(st.state,i);assert.equal(st.save(),true);
const saved=disk.data;const restored=app.createStore(disk);
assert.equal(JSON.stringify(restored.state),saved);assert.equal(app.next(restored.state),4);
assert.equal(disk.data,saved);assert.equal(restored.state.events.length,5);
app.createStore(disk);assert.equal(disk.data,saved);
restored.resume();restored.resume();assert.equal(restored.state.events.filter(e=>e.name==='progress_resumed').length,1);
restored.save();assert.equal(app.createStore(disk).state.events.length,6);
""")

    def test_partial_draft_roundtrip(self):
        self.run_js("""
const disk=memory(),st=app.createStore(disk);app.open(st.state,0);app.edit(st.state,'trial','あ'.repeat(501));st.save();
const restored=app.createStore(disk);assert.equal(restored.blocked,false);assert.equal(restored.state.values.trial.length,501);
assert.equal(app.next(restored.state),0);assert.ok(app.validate(restored.state,0).trial);
""")

    def test_corrupt_storage_requires_explicit_recovery(self):
        self.run_js("""
for(const raw of ['{','null','{}',JSON.stringify({...app.fresh(),version:2})]){
 const disk=memory(raw),st=app.createStore(disk);assert.equal(st.blocked,true);assert.match(st.status,/破損/);
 app.open(st.state,0);app.edit(st.state,'trial','復旧する入力');assert.equal(st.save(),false);assert.equal(disk.data,raw);
 assert.equal(st.retry(),true);assert.equal(app.decode(disk.data).values.trial,'復旧する入力');
}
""")

    def test_rejects_invalid_schema_and_private_event_fields(self):
        self.run_js("""
for(const mutate of [
 s=>s.completed=[false,true,false,false],s=>s.values.customer=null,
 s=>s.checks.fact='true',s=>s.visited=[true],s=>s.id='email@example.com',
 s=>s.events.push({name:'other',id:s.id,at:1}),
 s=>s.events[0].body='private text',s=>s.events[0].step=5,
 s=>s.events[0].at=-1,s=>s.extra='unrecognized',s=>s.events=Array(201).fill(s.events[0]),
 s=>s.values.trial='',s=>s.started=false,s=>s.events=[null]
]){const s=full();mutate(s);assert.throws(()=>app.decode(JSON.stringify(s)));}
""")

    def test_read_write_failure_and_retry(self):
        self.run_js("""
const disk=memory();disk.failRead=true;const st=app.createStore(disk);assert.match(st.status,/読み取れない/);
app.open(st.state,0);app.edit(st.state,'trial','保存できなくても編集');assert.equal(st.save(),false);
disk.failRead=false;disk.failWrite=true;assert.equal(st.retry(),false);assert.match(st.status,/保存できません/);
assert.equal(st.state.values.trial,'保存できなくても編集');disk.failWrite=false;
assert.equal(st.retry(),true);assert.equal(st.blocked,false);assert.match(st.status,/保存しました/);
assert.equal(app.decode(disk.data).values.trial,'保存できなくても編集');
""")

    def test_deletion_failure_and_recovery(self):
        self.run_js("""
const disk=memory(JSON.stringify(full())),st=app.createStore(disk);disk.failRemove=true;
assert.equal(st.remove(),false);assert.equal(st.state.id,null);assert.equal(st.state.events.length,0);
assert.equal(st.pendingDelete,true);assert.match(st.status,/古い内容が戻る/);assert.notEqual(disk.data,null);
assert.equal(st.save(true),false);disk.failRemove=false;assert.equal(st.retry(),true);
assert.equal(disk.data,null);assert.equal(st.blocked,false);assert.equal(st.pendingDelete,false);
""")

    def test_event_allowlist_privacy_and_limit(self):
        self.run_js("""
const s=full();s.values.action='秘密の本文 email@example.com';
for(let i=0;i<220;i++)app.record(s,'next_action_opened',3);
assert.equal(s.events.length,200);assert.equal(new Set(s.events.map(e=>e.id)).size,1);
assert.match(s.id,/^[a-f0-9]{32}$/);assert.notEqual(s.id,full().id);
for(const e of s.events){assert.equal(Object.keys(e).sort().join(','),'at,id,name,step');assert.equal(e.step,4);}
assert.ok(!JSON.stringify(s.events).includes('秘密'));assert.ok(!JSON.stringify(s.events).includes('@'));
assert.throws(()=>app.record(s,'unexpected'));assert.equal(app.decode(JSON.stringify(s)).events.length,200);
""")

    def test_ui_validation_focus_and_completion(self):
        self.run_js("""
ui();click('nextButton');assert.equal(focused,'workTitle');assert.equal(nodes.get('work').hidden,false);
click('completeButton');assert.equal(focused,'input-trial');assert.match(nodes.get('error-trial').textContent,/入力/);
input('trial','やりたいこと');click('completeButton');assert.equal(focused,'factCheck');
for(const id of ['factCheck','secretCheck']){nodes.get(id).checked=true;nodes.get(id).onchange();}
click('completeButton');assert.equal(nodes.get('progressText').textContent,'1/4');
assert.equal(state().events.filter(e=>e.name==='roadmap_started').length,1);
click('completeButton');assert.equal(state().events.filter(e=>e.name==='step_completed').length,1);
input('trial','あ'.repeat(501));assert.equal(nodes.get('progressText').textContent,'0/4');
assert.match(nodes.get('error-trial').textContent,/500文字以内/);
""")

    def test_ui_full_journey_summary_edit_and_next_action(self):
        self.run_js("""
ui();for(let i=0;i<4;i++){
 click('nextButton');for(const key of app.steps[i].keys)input(key,key==='action'?'<img src=x onerror=alert(1)>':'具体的な内容');
 if(i===0)for(const id of ['factCheck','secretCheck']){nodes.get(id).checked=true;nodes.get(id).onchange();}
 click('completeButton');assert.equal(nodes.get('progressText').textContent,`${i+1}/4`);
}
assert.equal(nodes.get('summaryPanel').hidden,false);assert.equal(nodes.get('summaryItems').children.length,7);
assert.equal(nodes.get('nextDescription').textContent,'<img src=x onerror=alert(1)>');
assert.equal(nodes.get('nextDescription').children.length,0);
const first=nodes.get('summaryItems').children[0];first.children[2].onclick();assert.equal(focused,'input-customer');
input('customer','編集した顧客');click('completeButton');assert.equal(nodes.get('summaryItems').children[0].children[1].textContent,'編集した顧客');
click('closeWork');assert.equal(focused,'nextButton');click('nextButton');assert.equal(nodes.get('workTitle').textContent,app.steps[3].title);
assert.equal(app.decode(disk.data).completed.filter(Boolean).length,4);
""")

    def test_ui_delete_cancel_confirm_and_event_reset(self):
        self.run_js("""
ui();click('nextButton');input('trial','保持する内容');const saved=disk.data;
click('deleteButton');assert.equal(nodes.get('deleteDialog').isOpen,true);click('cancelDelete');
assert.equal(disk.data,saved);assert.equal(state().values.trial,'保持する内容');assert.equal(focused,'deleteButton');
click('deleteButton');click('confirmDelete');assert.equal(disk.data,null);assert.equal(state().values.trial,'');
assert.equal(state().events.length,0);assert.equal(state().id,null);assert.equal(nodes.get('progressText').textContent,'0/4');
assert.equal(nodes.get('work').hidden,true);assert.equal(nodes.get('fields').children.length,0);
click('nextButton');assert.equal(state().events.filter(e=>e.name==='roadmap_started').length,1);
""")

    def test_ui_restoration_does_not_emit_until_open(self):
        self.run_js("""
disk.setItem(app.KEY,JSON.stringify(full()));const before=disk.data;ui();
assert.equal(nodes.get('progressText').textContent,'4/4');assert.equal(disk.data,before);
click('nextButton');assert.equal(state().events.filter(e=>e.name==='progress_resumed').length,1);
click('closeWork');click('nextButton');assert.equal(state().events.filter(e=>e.name==='progress_resumed').length,1);
assert.equal(state().events.filter(e=>e.name==='next_action_opened').length,2);
""")

    def test_ui_recovery_and_failed_deletion(self):
        self.run_js("""
disk.setItem(app.KEY,'{broken');ui();assert.equal(nodes.get('retrySave').hidden,false);
click('nextButton');input('trial','新しい下書き');assert.equal(disk.data,'{broken');
click('retrySave');assert.equal(app.decode(disk.data).values.trial,'新しい下書き');
disk.failRemove=true;click('deleteButton');click('confirmDelete');
assert.equal(nodes.get('retrySave').textContent,'削除を再試行');assert.match(nodes.get('storageStatus').textContent,/失敗/);
disk.failRemove=false;click('retrySave');assert.equal(disk.data,null);assert.equal(nodes.get('progressText').textContent,'0/4');
""")

    def test_self_contained_and_accessibility_contract(self):
        ids = [attrs['id'] for _, attrs in DOC.tags if 'id' in attrs]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate IDs")
        self.assertIn(('html', {'lang': 'ja'}), DOC.tags)
        self.assertFalse(any(tag in ('iframe', 'link', 'form') for tag, _ in DOC.tags))
        for tag, attrs in DOC.tags:
            for key in ('src', 'href'):
                if key in attrs:
                    self.assertTrue(attrs[key].startswith(('#', 'data:')), (tag, attrs))
        self.assertIn("connect-src 'none'", HTML)
        self.assertIn(':focus-visible', HTML)
        self.assertIn('@media(max-width:760px)', HTML)
        self.assertIn('minmax(0,1fr)', HTML)
        self.assertIn('overflow-wrap:anywhere', HTML)
        for forbidden in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'innerHTML', 'eval('):
            self.assertNotIn(forbidden, HTML)
        self.assertTrue(any(tag == 'dialog' and 'aria-labelledby' in attrs for tag, attrs in DOC.tags))


if __name__ == '__main__':
    unittest.main()
