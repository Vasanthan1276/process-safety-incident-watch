// Browser-independent regression harness: executes the production dashboard script.
// This checks application behavior and escaping, not browser layout or accessibility.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const app=fs.readFileSync('public/app.js','utf8');
const fixedNow=Date.parse('2026-11-13T08:00:00Z');
const fixture={incident_id:'PSI-TEST-1',title:'Intel plant gas leak',stream:'Semiconductor',review_status:'Needs review',status:'New',reported_at:'2026-11-12',incident_date:null,first_detected:'2026-11-12',screening:{decision:'Retain',reason:'Review required'},sources:[{title:'Intel plant gas leak',url:'https://example.org/report',source_name:'Test source'}],confirmed_facts:[],possible_duplicates:[]};
let checks=0;
function ok(value,msg){assert.ok(value,msg);checks++;}
async function harness(options={}){
 const ids={},listeners={},notices=[];
 const element=id=>ids[id] ||= {value:({stream:'all',scope:'candidates',window:'current',reviewStatus:'all',sort:'recent'}[id]||''),innerHTML:'',textContent:'',hidden:false,disabled:false,classList:{toggle(){}},addEventListener(type,fn){listeners[id+':'+type]=fn;},append(el){notices.push(el.textContent);},click(){}};
 const storage=new Map();
 const raw=options.raw||{last_run:'2026-11-12',incidents:[fixture]};
 const projection=options.projection||{data_as_of:'2026-11-12',generated_at:'2026-11-13',incidents:[fixture]};
 const diag={generated_at:'2026-11-12',providers:{GDELT:{queries_succeeded:0,queries_failed:1,failures:['HTTP 429']},'Google News RSS':{queries_succeeded:1,queries_failed:0,articles_returned:3}}};
 const files={'./data/review_index.json':projection,'./data/incidents.json':raw,'./data/run_diagnostics.json':diag,'./data/latest_report.json':{}};
 class FixedDate extends Date{constructor(...args){super(...(args.length?args:[fixedNow]));}static now(){return fixedNow;}}
 const context=vm.createContext({Date:FixedDate,URL,Blob,setTimeout,Intl,console,
 document:{getElementById:element,querySelector:()=>element('nav'),querySelectorAll:()=>[],createElement:()=>({textContent:'',click(){}})},
 localStorage:{getItem:key=>storage.get(key),setItem:(key,v)=>{if(options.storageBlocked)throw Error('blocked');storage.set(key,v);}},
 FormData:class{constructor(form){this.form=form;}get(k){return this.form.fields[k];}},
 location:{hash:''},fetch:async path=>({ok:!options.fail?.includes(path),status:503,json:async()=>structuredClone(files[path])})});
 vm.runInContext(app,context);await new Promise(resolve=>setImmediate(resolve));
 return {context,ids,listeners,notices,storage,run:code=>vm.runInContext(code,context)};
}
(async()=>{
 const h=await harness();
 ok(h.ids.queue.innerHTML.includes('Intel plant gas leak'),'Normal load shows stored report');
 ok(h.ids.queue.innerHTML.includes('No independently verified facts recorded'),'No auto-confirmation');
 ok(h.ids.queue.innerHTML.includes('Publication date is not the incident date'),'Separate event date');
 ok(h.notices.some(x=>x.includes('incomplete')),'Partial feed failure is visible');
 ok(h.ids.healthContent.innerHTML.includes('Google News RSS')&&h.ids.healthContent.innerHTML.includes('HTTP 429'),'Provider failures displayed separately');
 ok(h.run("safeUrl('javascript:alert(1)')")==='','Unsafe URL blocked');
 ok(h.run("safeUrl('https://user:pass@example.org')")==='','Credential URL blocked');
 const malicious={...fixture,title:'<img src=x onerror=alert(1)>',sources:[{url:'javascript:alert(1)',source_name:'<script>bad</script>'}]};
 h.context.bad=malicious;const html=h.run('card(bad)');
 ok(!html.includes('<img')&&!html.includes('<script>')&&!html.includes('href="javascript:'),'Source and title HTML escaped');
 h.ids.search.value='absent';h.listeners['search:input']();ok(h.ids.queue.innerHTML.includes('No matching reports'),'Search empty state');
 h.ids.search.value='PSI-TEST-1';h.listeners['search:input']();ok(h.ids.queue.innerHTML.includes('Intel plant gas leak'),'Search by record ID');
 const optional=await harness({fail:['./data/latest_report.json','./data/run_diagnostics.json']});
 ok(optional.ids.queue.innerHTML.includes('Intel plant gas leak'),'Optional summary and diagnostics failures preserve records');
 ok(optional.notices.some(x=>x.includes('diagnostics')),'Missing diagnostics explicitly unknown');
 const fallback=await harness({fail:['./data/review_index.json']});
 ok(fallback.ids.queue.innerHTML.includes('Intel plant gas leak'),'Raw-data fallback works');
 ok(fallback.notices.some(x=>x.includes('raw records')),'Fallback is labelled');
 const failed=await harness({fail:['./data/review_index.json','./data/incidents.json']});
 ok(failed.ids.queue.innerHTML.includes('No records have been removed'),'Dual dataset failure is explicit');
 const stale=await harness({projection:{data_as_of:'2026-09-01',incidents:[fixture]}});
 ok(stale.notices.some(x=>x.includes('stale')),'Staleness uses data collection time');
 const many=Array.from({length:27},(_,i)=>({...fixture,incident_id:'PSI-'+i}));
 const paged=await harness({projection:{data_as_of:'2026-11-12',incidents:many}});
 ok((paged.ids.queue.innerHTML.match(/<article class="incident"/g)||[]).length===12,'Queue paginated at 12');
 paged.listeners['next:click']();ok(paged.ids.pageInfo.textContent==='Page 2 of 3','Pagination advances');
 const scope=await harness({projection:{data_as_of:'2026-11-12',incidents:[fixture,{...fixture,incident_id:'OLD',title:'ETF market news',screening:{decision:'Out of scope'}}]}});
 ok(!scope.ids.queue.innerHTML.includes('ETF market news'),'Out-of-scope excluded by default');
 scope.ids.scope.value='all';scope.listeners['scope:change']();ok(scope.ids.queue.innerHTML.includes('ETF market news'),'Historical screened-out record remains accessible');
 const old=await harness({projection:{data_as_of:'2026-11-12',incidents:[{...fixture,incident_date:'2020-01-01'}]}});
 ok(old.ids.queue.innerHTML.includes('No matching reports'),'Incident date drives window when known');
 old.ids.window.value='all';old.listeners['window:change']();ok(old.ids.queue.innerHTML.includes('Intel plant gas leak'),'Archive toggle retains older event');
 function formFor(target){const message={textContent:''},badge={textContent:''},badges={querySelector:s=>s==='.warn'?badge:null,insertAdjacentHTML(){}};const form={dataset:{id:'PSI-TEST-1'},fields:{status:'In review',notes:'Synthetic regression test; no real incident decision.',reviewer:'Test reviewer'},querySelector:()=>message,closest:s=>s==='article'?{querySelector:()=>badges}:form};return {form,message,badge};}
 const draft=formFor(h);h.listeners['queue:submit']({target:draft.form,preventDefault(){}});
 ok(draft.message.textContent.includes('Local draft saved'),'Draft saved locally');
 ok(JSON.parse(h.storage.get('psiw-review-drafts-v1'))['PSI-TEST-1'].status==='In review','Draft status persists');
 ok(h.run('records[0].review_status')==='Needs review','Draft does not mutate published record');
 const blocked=await harness({storageBlocked:true});const blockedForm=formFor(blocked);blocked.listeners['queue:submit']({target:blockedForm.form,preventDefault(){}});
 ok(blockedForm.message.textContent.includes('could not be saved'),'Storage-denied path is explicit');
 console.log(`PASS: ${checks} dashboard behavior assertions (no browser rendering claimed).`);
})().catch(e=>{console.error(e);process.exitCode=1;});
