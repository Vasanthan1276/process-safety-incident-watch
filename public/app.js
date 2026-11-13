/* Public evidence is rendered as text; review drafts never leave this browser unless exported. */
'use strict';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
const safeUrl = value => { try { const u = new URL(value); return ['https:','http:'].includes(u.protocol) && !u.username && !u.password ? u.href : ''; } catch { return ''; } };
const link = (url, label) => safeUrl(url) ? `<a href="${escapeHtml(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} ↗</a>` : `<span>${escapeHtml(label)} — URL unavailable</span>`;
function date(value, time=false) { if(!value) return 'Unknown'; const d = new Date(value); return Number.isNaN(d.getTime()) ? 'Unknown / invalid date' : new Intl.DateTimeFormat('en-SG',{day:'numeric',month:'short',year:'numeric',...(time?{hour:'2-digit',minute:'2-digit',timeZoneName:'short'}:{}),timeZone:'Asia/Singapore'}).format(d); }
let data={}, records=[], diagnostics=null, report=null, page=1;
const PAGE_SIZE=12, DRAFT_KEY='psiw-review-drafts-v1';
let drafts={};
try { drafts=JSON.parse(localStorage.getItem(DRAFT_KEY)||'{}'); if(!drafts || Array.isArray(drafts) || typeof drafts !== 'object') drafts={}; } catch { drafts={}; }
const stateOf = r => drafts[r.incident_id]?.status || r.review_status || 'Needs review';
const isCurrent = r => { const d=new Date(r.incident_date || r.reported_at || r.first_detected); return Number.isNaN(d.getTime()) || d.getTime() >= Date.now()-90*86400000; };
function addNotice(text, error=false) { const el=document.createElement('div'); el.className='notice'+(error?' error':''); el.textContent=text; $('notice').append(el); }
function view(name) { ['review','health','guide'].forEach(v=>{$(v+'View').hidden=v!==name; document.querySelector(`[data-view="${v}"]`).classList.toggle('active',v===name);}); }
function stats() {
 const current=records.filter(isCurrent), retained=current.filter(r=>r.screening?.decision!=='Out of scope');
 const countDraft=Object.keys(drafts).filter(id=>records.some(r=>r.incident_id===id)).length;
 $('stats').innerHTML=[
  [retained.length,'Current review candidates','Reports; not verified event totals'],
  [retained.filter(r=>stateOf(r)!=='Reviewed'&&stateOf(r)!=='Excluded').length,'Awaiting review',countDraft+' browser draft decisions'],
  [retained.filter(r=>r.possible_duplicates?.length).length,'Records with possible matches','Compare before combining events'],
  [current.length-retained.length,'Screened out · preserved','Available through the Scope filter']
 ].map(x=>`<div class="stat"><div class="stat-value">${x[0]}</div><div class="stat-label">${x[1]}</div><div class="stat-note">${x[2]}</div></div>`).join('');
 $('historySummary').textContent=`All ${records.length} historical records remain available. Original data last collected ${date(data.data_as_of,true)}. Review projection generated ${date(data.generated_at,true)}; rebuilding it does not refresh discovery.`;
}
function claims(items, empty) { return items?.length ? items.map(c=>`<div class="claim">${escapeHtml(c.text)}${link(c.source_url,'Evidence source')}</div>`).join('') : `<div class="empty-evidence">${escapeHtml(empty)}</div>`; }
function names(r,field) { return r[field] || (r[field+'_mentions']||[]).map(x=>x.value).join(' · ') || 'Unknown — review source'; }
function sourceRow(s) { return `<div class="source-row">${link(s.original_url||s.url,s.source_name||s.domain||'Source')}<span class="muted"> · Publisher rating ${escapeHtml(s.reliability??'—')}/5 (heuristic)</span><div class="source-title">${escapeHtml(s.title)}</div><div>${escapeHtml(s.link_type || 'Source link')} · ${escapeHtml(s.retrieval_status || 'Retrieval unknown')}</div><div class="muted">Published: ${date(s.published_at)} · Source updated: ${date(s.updated_at)} · Retrieved: ${date(s.retrieved_at)}</div><div class="muted">${escapeHtml(s.date_basis||'Date basis not recorded')}${s.discovered_at?' · Discovered '+date(s.discovered_at):''}</div></div>`; }
function card(r) {
 const id=r.incident_id, draft=drafts[id], status=stateOf(r), first=r.sources?.[0], score=r.scores?.watch_score;
 return `<article class="incident" id="${escapeHtml(id)}">
 <div class="card-head"><div><div class="badges"><span class="badge ${r.stream==='Semiconductor'?'semi':''}">${escapeHtml(r.stream)}</span><span class="badge warn">${escapeHtml(status)}</span>${draft?'<span class="badge draft">Local draft</span>':''}${r.possible_duplicates?.length?`<span class="badge warn">${r.possible_duplicates.length} possible match${r.possible_duplicates.length===1?'':'es'}</span>`:''}${r.screening?.decision!=='Retain'?`<span class="badge ${r.screening?.decision==='Out of scope'?'danger':'warn'}">${escapeHtml(r.screening?.decision)}</span>`:''}</div><h3 class="card-title">${escapeHtml(r.title)}</h3><div class="record-id">${escapeHtml(id)} · ${r.sources?.length||0} source report${r.sources?.length===1?'':'s'} · First report / discovery ${date(r.reported_at)}</div></div></div>
 <div class="card-meta"><div><strong>Company</strong>${escapeHtml(names(r,'company'))}${r.company?'':'<div class="unverified">Title mentions only; role unverified</div>'}</div><div><strong>Location</strong>${escapeHtml(names(r,'location'))}${r.location?'':'<div class="unverified">Title mentions only; location unverified</div>'}</div><div><strong>Incident date</strong>${date(r.incident_date)}${r.incident_date?'':'<div class="unverified">Publication date is not the incident date</div>'}</div></div>
 <div class="card-actions"><div class="primary-source">${first?link(first.original_url||first.url,first.source_name||first.domain||'Open source'):'No source link recorded'}</div><span class="muted">${r.confirmed_facts?.length?r.confirmed_facts.length+' reviewer-confirmed fact(s)':'No independently verified facts recorded'}</span></div>
 <details class="detail-block"><summary>Review evidence, dates & potential lessons</summary>
 ${r.legacy_status_note?`<div class="notice">${escapeHtml(r.legacy_status_note)}.</div>`:''}
 <div class="small muted">Screening: ${escapeHtml(r.screening?.reason)}. ${escapeHtml(r.window_basis)}.</div>
 ${r.possible_duplicates?.length?`<div class="duplicate-list"><strong>Possible duplicates — not merged</strong><p>Check event dates, site and whether articles are independent. ${r.possible_duplicates.map(d=>`<button class="text-button jump" data-id="${escapeHtml(d.incident_id)}">${escapeHtml(d.incident_id)}</button>`).join(' · ')}</p></div>`:''}
 <div class="detail-grid"><div><h4>Confirmed facts · human review</h4>${claims(r.confirmed_facts,'None recorded. Publisher reputation does not confirm the event.')}<h4>Source-reported information</h4>${claims(r.reported_claims,'No usable article content available. Open the original source.')}</div><div><h4>Preliminary or disputed information</h4>${claims(r.preliminary_or_disputed,'No uncertainty statement extracted. This does not establish certainty.')}<h4>Reported consequences · unverified</h4>${claims(r.reported_consequences,'Consequences not established in the available evidence.')}</div></div>
 <div class="panel"><h4>Potential lessons for semiconductor fabs</h4><p class="small muted">Assessment prompts inferred from incident terms. These are not established causes or failed safeguards.</p><ul>${(r.potential_lessons||[]).map(s=>`<li class="small">${escapeHtml(s)}</li>`).join('')||'<li class="small">Establish relevance before drawing lessons.</li>'}</ul><p class="small"><strong>Tier:</strong> ${escapeHtml(r.tier)}</p></div>
 <div class="source-section"><h4>All source reports</h4>${(r.sources||[]).map(sourceRow).join('')||'<p>No source recorded.</p>'}</div>
 <div class="small muted">First detected: ${date(r.first_detected,true)} · Record updated: ${date(r.last_updated,true)} · Legacy watch ranking: ${Number.isFinite(Number(score))?Number(score).toFixed(1):'—'}/5 (heuristic; not severity classification or verification)</div>
 <details><summary class="small">Record change history</summary>${(r.change_history||[]).map(c=>`<p class="small">${date(c.timestamp,true)} — ${escapeHtml((c.changes||[]).join('; '))}</p>`).join('')}</details>
 <form class="draft-form" data-id="${escapeHtml(id)}"><h4>Draft a review decision</h4><p class="small muted">Saved on this browser only. Export for repository review. Do not include confidential information; this is a public-source project. A draft does not verify claims.</p>
 <div class="draft-fields"><label>Review status<select name="status">${['Needs review','In review','Reviewed','Excluded'].map(s=>`<option ${s===status?'selected':''}>${s}</option>`).join('')}</select></label><label>Reviewer<input name="reviewer" value="${escapeHtml(draft?.reviewed_by||r.manual_review?.reviewed_by||'')}" required maxlength="120" placeholder="Name or reviewer ID"></label></div>
 <label>Evidence checked, missing details or exclusion reason<textarea name="notes" required maxlength="5000">${escapeHtml(draft?.notes||r.manual_review?.notes||'')}</textarea></label><div class="draft-buttons"><button type="submit">Save local draft</button><button type="button" class="clear-draft" data-id="${escapeHtml(id)}">Clear this draft</button></div><div class="draft-message" aria-live="polite">${draft?'Local draft saved '+date(draft.reviewed_at,true):''}</div></form>
 </details></article>`;
}
function filtered() {
 const q=$('search').value.trim().toLowerCase();
 return records.filter(r=>{
  if(q && !JSON.stringify(r).toLowerCase().includes(q))return false;
  if($('stream').value!=='all'&&r.stream!==$('stream').value)return false;
  if($('reviewStatus').value!=='all'&&stateOf(r)!==$('reviewStatus').value)return false;
  const scope=$('scope').value, out=r.screening?.decision==='Out of scope';
  if(scope==='candidates'&&out||scope==='out'&&!out||scope==='duplicates'&&!r.possible_duplicates?.length)return false;
  const w=$('window').value;
  if(w==='current'&&!isCurrent(r)||w==='new'&&r.status!=='New'||w==='updated'&&r.status!=='Updated')return false;
  return true;
 }).sort((a,b)=>{const sort=$('sort').value, time=r=>new Date(r.reported_at||r.first_detected||0).getTime()||0; return (sort==='priority'?(b.stream==='Semiconductor')-(a.stream==='Semiconductor'):0)||(sort==='oldest'?time(a)-time(b):time(b)-time(a))||a.incident_id.localeCompare(b.incident_id);});
}
function render() {
 stats();const items=filtered(), pages=Math.max(1,Math.ceil(items.length/PAGE_SIZE));page=Math.min(page,pages);
 $('resultCount').textContent=`${items.length} matching reports · ${records.length} preserved records total`;
 $('queue').innerHTML=items.slice((page-1)*PAGE_SIZE,page*PAGE_SIZE).map(card).join('')||'<div class="empty">No matching reports. Try All dates / history or All preserved records.</div>';
 $('pageInfo').textContent=`Page ${page} of ${pages}`;$('previous').disabled=page===1;$('next').disabled=page===pages;
}
function renderHealth() {
 if(!diagnostics){$('healthContent').innerHTML='<div class="notice error">Diagnostics unavailable. Incident records remain usable; source health cannot be established.</div>';return;}
 const providers=Object.entries(diagnostics.providers||{});
 $('healthContent').innerHTML=`<p class="small muted">Last discovery attempt: ${date(diagnostics.generated_at,true)}. Counts below describe that run, not current live source availability.</p><div class="health-grid">${providers.map(([name,p])=>{const state=p.status||(p.queries_failed?(p.queries_succeeded?'degraded':'unavailable'):(p.queries_succeeded?'healthy':'not attempted'));return `<article class="panel"><div class="badges"><h3>${escapeHtml(name)}</h3><span class="badge ${state==='healthy'?'semi':'warn'}">${escapeHtml(state)}</span></div><div class="health-count">${p.articles_returned||0} <span class="small muted">articles returned</span></div><p>${p.queries_succeeded||0} queries succeeded · ${p.queries_failed||0} failed</p><p class="small muted">Last successful query: ${date(p.last_success_at||(p.queries_succeeded?diagnostics.generated_at:null),true)}</p>${(p.failures||[]).map(x=>`<div class="notice">${escapeHtml(x)}</div>`).join('')}</article>`;}).join('')}</div><div class="panel"><h3>Article access & screening limits</h3><p>${diagnostics.selected_for_enrichment??'Unknown'} article fetches selected; ${diagnostics.article_pages_enriched??'Unknown'} recorded as enriched. Legacy enrichment counts can include aggregator boilerplate.</p><p class="small">${Object.entries(diagnostics.article_retrieval||{}).map(([k,v])=>escapeHtml(k)+': '+v).join(' · ')||'Detailed article retrieval states were not recorded by the legacy collector.'}</p><details class="health-details"><summary>Query coverage and fetch limits</summary>${(diagnostics.query_details||[]).map(q=>`<p><strong>${escapeHtml(q.name)}</strong>: ${q.discovered} results · ${q.selected_for_enrichment} selected · ${q.deferred_by_limit??'unknown'} deferred by limit</p>`).join('')}</details></div><div class="panel"><h3>Screened-out discoveries</h3><p class="small">Rejected new discoveries are logged for audit; historical records are never deleted.</p><details class="health-details"><summary>${diagnostics.screened_out?.length??0} items logged by the new screening rules</summary>${(diagnostics.screened_out||[]).map(x=>`<p>${link(x.url,x.title)}<br>${escapeHtml(x.reason)}</p>`).join('')}</details></div>`;
}
async function loadJson(path) { const r=await fetch(path,{cache:'no-store'});if(!r.ok)throw Error(path+' HTTP '+r.status);return r.json(); }
async function load() {
 const names=['./data/review_index.json','./data/run_diagnostics.json','./data/latest_report.json','./data/incidents.json'];
 const results=await Promise.allSettled(names.map(loadJson));
 diagnostics=results[1].status==='fulfilled'?results[1].value:null; report=results[2].status==='fulfilled'?results[2].value:null;
 if(results[0].status==='fulfilled'&&Array.isArray(results[0].value.incidents))data=results[0].value;
 else if(results[3].status==='fulfilled'&&Array.isArray(results[3].value.incidents)) {
  const raw=results[3].value; data={data_as_of:raw.last_run,incidents:raw.incidents.map(r=>({...r,review_status:'Needs review',legacy_status_note:r.review_status==='Confirmed'?'Legacy Confirmed was a publisher-score threshold, not independent verification':null,screening:{decision:'Needs context',reason:'Review projection unavailable; raw record fallback'},reported_claims:(r.sources||[]).map(s=>({text:s.title||r.title,source_url:s.url})),confirmed_facts:[],tier:'Not assigned — methodology and evidence required'}))};
  addNotice('Review projection unavailable. Showing preserved raw records with verification and screening marked unknown.',true);
 } else {throw Error('Both the review projection and incident database are unavailable.');}
 records=data.incidents;
 const age=Date.now()-new Date(data.data_as_of).getTime(), stale=!Number.isFinite(age)||age < -86400000||age>(data.stale_after_days||8)*86400000;
 $('freshness').innerHTML=`<strong>${stale?'● Data stale / freshness unknown':'● Stored data within update interval'}</strong>Last collected ${date(data.data_as_of,true)}<br>Scheduled weekly · stale after ${data.stale_after_days||8} days`;
 if(stale)addNotice('Stored discovery data is stale or its age is unknown. Reports remain available; absence of new records does not mean no incidents occurred.');
 if(!diagnostics)addNotice('Source diagnostics could not be loaded. Reports are still available; source health is unknown.');
 else if(Object.values(diagnostics.providers||{}).some(p=>p.queries_failed))addNotice('Source coverage was incomplete at the last attempt. Available reports are retained. See Source health for the failed queries.');
 if(!report)addNotice('The run summary is unavailable. Counts are calculated from the available records.');
 render();renderHealth();
 let target='';try{target=decodeURIComponent(location.hash.slice(1));}catch{}if(target&&records.some(r=>r.incident_id===target))jump(target);
}
function jump(id) { view('review');$('search').value=id;$('scope').value='all';$('window').value='all';$('stream').value='all';$('reviewStatus').value='all';page=1;render();location.hash=id; }
function reset() {$('search').value='';$('stream').value='all';$('scope').value='candidates';$('window').value='current';$('reviewStatus').value='all';$('sort').value='priority';page=1;render();}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>view(b.dataset.view)));
['search','stream','scope','window','reviewStatus','sort'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',()=>{page=1;render();}));
$('reset').addEventListener('click',reset);$('previous').addEventListener('click',()=>{page--;render();});$('next').addEventListener('click',()=>{page++;render();});
$('queue').addEventListener('click',e=>{const jumpEl=e.target.closest('.jump');if(jumpEl)jump(jumpEl.dataset.id);const clear=e.target.closest('.clear-draft');if(clear){const form=clear.closest('form');try{const next={...drafts};delete next[clear.dataset.id];localStorage.setItem(DRAFT_KEY,JSON.stringify(next));drafts=next;render();addNotice('Local draft cleared. The published record is unchanged.');}catch{form.querySelector('.draft-message').textContent='Browser storage is unavailable; draft was not cleared.';}}});
$('queue').addEventListener('submit',e=>{const form=e.target.closest('.draft-form');if(!form)return;e.preventDefault();const fields=new FormData(form), notes=String(fields.get('notes')||'').trim(), reviewer=String(fields.get('reviewer')||'').trim();if(!notes||!reviewer){form.querySelector('.draft-message').textContent='Reviewer and evidence notes are required.';return;}const entry={status:fields.get('status'),reviewed_by:reviewer,reviewed_at:new Date().toISOString(),notes};try{const next={...drafts,[form.dataset.id]:entry};localStorage.setItem(DRAFT_KEY,JSON.stringify(next));drafts=next;form.querySelector('.draft-message').textContent='Local draft saved. Export it for repository review; nothing has been published.';const article=form.closest('article'), badges=article.querySelector('.badges');badges.querySelector('.warn').textContent=entry.status;if(!badges.querySelector('.draft'))badges.insertAdjacentHTML('beforeend','<span class="badge draft">Local draft</span>');stats();}catch{form.querySelector('.draft-message').textContent='Browser storage is unavailable; draft could not be saved. Copy your notes before leaving.';}});
$('exportDrafts').addEventListener('click',()=>{const reviews={};records.forEach(r=>{if(drafts[r.incident_id])reviews[r.incident_id]=drafts[r.incident_id];});const blob=new Blob([JSON.stringify({schema_version:1,reviews},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='incident-review-drafts.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
load().catch(error=>{$('freshness').textContent='Data unavailable';addNotice(error.message+' Open Source health or retry after restoring the data files.',true);$('queue').innerHTML='<div class="empty">Incident records could not be loaded. No records have been removed.</div>';renderHealth();});
