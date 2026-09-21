'use strict';
const byId = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const categoryNames = {BL_COMPARISON:'Document check',SI_REQUEST:'Shipping instruction',INVOICE_QUERY:'Invoice query',GENERAL:'General',SPAM:'Spam',UNPROCESSED:'Not processed'};
const folderNames = {all:'Inbox',starred:'Starred',review:'Needs review',mismatch:'Discrepancies',verified:'Verified matches',BL_COMPARISON:'Document checks',SI_REQUEST:'Shipping instructions',INVOICE_QUERY:'Invoice queries',GENERAL:'General',SPAM:'Spam'};
let state = null, folder = 'all', actionOnly = false, page = 0, selected = new Set(), current = null, refreshing = false;
const pageSize = 10;
let stars = new Set();
try { stars = new Set(JSON.parse(localStorage.getItem('harborcheck-stars') || '[]')); } catch (_) {}
function notify(text) { byId('toast').textContent = text; }
function statusLabel(email) {
  if(email.error) return 'Processing error';
  return {OK:email.category==='BL_COMPARISON'?'Matched':'Classified',MISMATCH:'Mismatch',NEEDS_REVIEW:'Needs review',PENDING:'Pending'}[email.status] || email.status;
}
function pill(status,label) { return `<span class="status-pill ${escapeHTML(status)}">${status==='OK'?'✓':status==='NEEDS_REVIEW'?'◷':status==='MISMATCH'?'⚑':'·'} ${escapeHTML(label || status)}</span>`; }
async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const response = await fetch(path,options);
  const data = await response.json();
  if(!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function matchesFolder(email) {
  if(folder==='starred') return stars.has(email.email_id);
  if(folder==='review') return email.status==='NEEDS_REVIEW';
  if(folder==='mismatch') return email.status==='MISMATCH';
  if(folder==='verified') return email.status==='OK' && email.category==='BL_COMPARISON';
  return folder==='all' || email.category===folder;
}
function filteredEmails() {
  if(!state) return [];
  const query=byId('search').value.trim().toLowerCase();
  return state.emails.filter(e=>matchesFolder(e) && (!actionOnly||['NEEDS_REVIEW','MISMATCH'].includes(e.status)) &&
    `${e.email_id} ${e.subject} ${e.sender} ${e.preview}`.toLowerCase().includes(query));
}
function renderRows() {
  const filtered=filteredEmails();
  page=Math.min(page,Math.max(0,Math.ceil(filtered.length/pageSize)-1));
  const visible=filtered.slice(page*pageSize,(page+1)*pageSize);
  byId('page-info').textContent=filtered.length?`${page*pageSize+1}–${page*pageSize+visible.length} of ${filtered.length}`:'0 messages';
  byId('prev-page').disabled=page===0;
  byId('next-page').disabled=(page+1)*pageSize>=filtered.length;
  byId('select-all').checked=visible.length>0&&visible.every(e=>selected.has(e.email_id));
  byId('select-all').indeterminate=visible.some(e=>selected.has(e.email_id))&&!byId('select-all').checked;
  byId('process-selected').disabled=selected.size===0||Boolean(state?.job.running)||Boolean(state?.read_only);
  byId('process-selected').textContent=selected.size?`Verify selected (${selected.size})`:'Verify selected';
  byId('mail-list').innerHTML=visible.length?visible.map(e=>{
    const sender=(e.sender||e.email_id).split('@')[0];
    const initials=sender.replace(/[^a-zA-Z]/g,'').slice(0,2).toUpperCase()||'EM';
    return `<div class="mail-row ${selected.has(e.email_id)?'is-selected':''}" data-email="${escapeHTML(e.email_id)}" tabindex="0" role="button" aria-label="Open ${escapeHTML(e.email_id)}: ${escapeHTML(e.subject)}"><input type="checkbox" class="row-check" aria-label="Select ${escapeHTML(e.email_id)}" ${selected.has(e.email_id)?'checked':''}><button class="star-button ${stars.has(e.email_id)?'starred':''}" aria-label="${stars.has(e.email_id)?'Unstar':'Star'} ${escapeHTML(e.email_id)}" aria-pressed="${stars.has(e.email_id)}">${stars.has(e.email_id)?'★':'☆'}</button><div class="sender"><span class="sender-avatar">${escapeHTML(initials)}</span><span class="sender-name" title="${escapeHTML(e.sender)}">${escapeHTML(sender)}</span></div><div class="mail-content"><div class="subject-line"><span class="subject">${escapeHTML(e.subject)}</span><span class="category-tag ${escapeHTML(e.category)}">${escapeHTML(categoryNames[e.category]||e.category)}</span></div><div class="mail-preview">${escapeHTML(e.preview||'Open this email to view the message and its shipping documents.')}</div></div><div class="row-end"><span class="row-id">${escapeHTML(e.email_id.replace('email_','#'))}${e.human_reviewed?' · reviewed':''}</span>${pill(e.status,statusLabel(e))}</div></div>`;
  }).join(''):'<div class="empty-state">No messages here.<br><small>Try another folder or a different search.</small></div>';
  byId('count-starred').textContent=state?state.emails.filter(e=>stars.has(e.email_id)).length:0;
}
function renderSummary() {
  const emails=state.emails, count=fn=>emails.filter(fn).length;
  const verified=count(e=>e.category==='BL_COMPARISON'&&e.status==='OK');
  const mismatch=count(e=>e.status==='MISMATCH'), review=count(e=>e.status==='NEEDS_REVIEW');
  byId('count-all').textContent=emails.length;
  byId('metric-total').textContent=`of ${emails.length} in your inbox`;
  byId('metric-processed').textContent=count(e=>e.status!=='PENDING');
  byId('metric-verified').textContent=byId('count-verified').textContent=verified;
  byId('metric-mismatch').textContent=byId('count-mismatch').textContent=mismatch;
  byId('metric-review').textContent=byId('count-review').textContent=review;
  byId('action-count').textContent=mismatch+review;
  byId('connection').textContent='Workspace connected';
  byId('process-all').disabled=state.job.running||state.read_only;
  byId('job-progress').textContent=state.job.running?`Verifying ${state.job.completed} / ${state.job.total}`:'';
  byId('cloud-status').textContent=state.read_only?'This deployment is read-only — results were pre-computed and verification is disabled here.':state.cloud_configured?'GEMINI_API_KEY and OPENAI_API_KEY are configured. Ready to verify documents.':'Missing GEMINI_API_KEY or OPENAI_API_KEY. Add both to .env, then restart the server.';
  byId('mode-label').textContent=state.read_only?'Read-only demo':state.cloud_configured?'Live pipeline':'Not configured';
  if(state.job.error) notify(state.job.error);
}
async function refresh(force=false) {
  if(refreshing) return;
  refreshing=true;
  try {
    const next=await api('/api/state');
    const finished=state?.job.running&&!next.job.running;
    const changed=!state||JSON.stringify(next)!==JSON.stringify(state);
    state=next;
    if(changed||force){renderSummary();renderRows();}
    if(finished&&current&&byId('reader').open) await openEmail(current.email.email_id);
  } catch(error){notify(error.message);byId('connection').textContent='Connection unavailable';}
  finally{refreshing=false;}
}
function setFolder(value) {
  folder=value;page=0;selected.clear();
  if(window.matchMedia('(max-width:850px)').matches) document.body.classList.remove('sidebar-hidden');
  document.querySelectorAll('[data-folder]').forEach(el=>el.classList.toggle('active',el.dataset.folder===value));
  byId('folder-title').textContent=folderNames[value];
  byId('folder-description').textContent=value==='review'?'The cases that need your expertise.':value==='mismatch'?'See exactly where shipment details differ.':value==='starred'?'Important messages, kept close.':'A clearer view of every shipment.';
  renderRows();
}
function setTab(action) {
  actionOnly=action;page=0;selected.clear();
  for(const [id,active] of [['tab-all',!action],['tab-action',action]]){
    byId(id).classList.toggle('active',active);byId(id).setAttribute('aria-selected',String(active));
  }
  renderRows();
}
async function openEmail(id) {
  try {
    const data=await api('/api/email/'+encodeURIComponent(id));current=data;
    const e=data.email,r=data.report;
    byId('reader-id').textContent=id;
    byId('retry').disabled=Boolean(state?.job.running)||Boolean(state?.read_only);
    let html=`<h2>${escapeHTML(e.subject)}</h2><div class="reader-meta"><span>From ${escapeHTML(e.from)}</span>${pill(r?.status||'PENDING',r?statusLabel(r):'Pending')}<span>${escapeHTML(r?.mode||'Not processed')}${r?.human_reviewed?' · Human reviewed':''}</span></div><details><summary>Read message</summary><pre>${escapeHTML(e.body)}</pre></details><div>${e.attachments.map((path,index)=>`<a class="attachment-link" href="/api/attachment?email_id=${encodeURIComponent(id)}&index=${index}">▤ ${escapeHTML(path.split('/').pop())}</a>`).join('')||'<p class="report-notice">No attachments were supplied with this email.</p>'}</div>`;
    if(r){
      html+=`<p class="report-notice">${escapeHTML(categoryNames[r.category]||r.category)} · ${escapeHTML(r.classification_reason||'')}</p>`;
      if(r.error)html+=`<p class="report-notice error">Processing failed: ${escapeHTML(r.error)}</p>`;
      if(r.review_detail)html+=`<p class="report-notice">${escapeHTML(r.review_detail)}</p>`;
      if(r.category==='BL_COMPARISON'&&r.status==='OK')html+='<p class="report-notice">✓ No mismatch detected. All seven fields agree.</p>';
      if(r.rows?.length)html+=`<h3>Shipment comparison</h3><div class="table-wrap"><table><thead><tr><th>Field</th><th>SI · Reference</th><th>BL · Draft</th><th>Result</th></tr></thead><tbody>${r.rows.map(row=>`<tr><td>${escapeHTML(row.field.replaceAll('_',' '))}</td><td>${escapeHTML(row.si??'Missing')}</td><td>${escapeHTML(row.bl??'Missing')}</td><td>${pill(row.result==='MATCH'?'OK':row.result,row.result==='MATCH'?'Match':row.result==='MISMATCH'?'Mismatch':'Review')}<details><summary>Evidence</summary><b>SI</b><pre>${escapeHTML(row.si_evidence)}</pre><b>BL</b><pre>${escapeHTML(row.bl_evidence)}</pre></details></td></tr>`).join('')}</tbody></table></div>`;
    } else html+='<p class="report-notice">This message has not been processed. Choose Reprocess to classify it and check any shipping documents.</p>';
    byId('reader-content').innerHTML=html;
    if(!byId('reader').open)byId('reader').showModal();
  }catch(error){notify(error.message);}
}
function settings(){byId('settings').showModal();}
async function run(ids) {
  if(state.read_only){notify('This deployment is read-only — verification is disabled here.');return;}
  if(!state.cloud_configured){settings();notify('Add GEMINI_API_KEY and OPENAI_API_KEY to .env, then restart the server.');return;}
  const count=ids?ids.length:state.emails.length;
  if(!confirm(`Verify ${count} email${count===1?'':'s'}? Existing reports for these emails will be replaced. This calls Gemini and OpenAI and uses API credits.`))return;
  try{await api('/api/run',ids?{email_ids:ids}:{});notify('Verification started. Progress appears above the inbox.');await refresh(true);if(byId('reader').open)byId('retry').disabled=true;}
  catch(error){notify(error.message);}
}
byId('mail-list').onclick=event=>{
  const row=event.target.closest('[data-email]');if(!row)return;const id=row.dataset.email;
  if(event.target.closest('.star-button')){
    stars.has(id)?stars.delete(id):stars.add(id);try{localStorage.setItem('harborcheck-stars',JSON.stringify([...stars]));}catch(_){}
    renderRows();return;
  }
  if(event.target.matches('.row-check')){event.target.checked?selected.add(id):selected.delete(id);renderRows();return;}
  openEmail(id);
};
byId('mail-list').onkeydown=event=>{if(event.target.matches('.mail-row')&&(event.key==='Enter'||event.key===' ')){event.preventDefault();openEmail(event.target.dataset.email);}};
byId('select-all').onchange=event=>{filteredEmails().slice(page*pageSize,(page+1)*pageSize).forEach(e=>event.target.checked?selected.add(e.email_id):selected.delete(e.email_id));renderRows();};
document.querySelectorAll('[data-folder]').forEach(el=>el.onclick=()=>setFolder(folder===el.dataset.folder?'all':el.dataset.folder));
byId('search').oninput=()=>{page=0;renderRows();};
byId('tab-all').onclick=()=>setTab(false);byId('tab-action').onclick=()=>setTab(true);
byId('prev-page').onclick=()=>{page--;renderRows();};byId('next-page').onclick=()=>{page++;renderRows();};
byId('refresh').onclick=()=>refresh(true);
byId('settings-open').onclick=settings;
byId('menu-toggle').onclick=()=>document.body.classList.toggle('sidebar-hidden');
byId('process-all').onclick=()=>state&&run();byId('process-selected').onclick=()=>run([...selected]);
byId('retry').onclick=()=>current&&run([current.email.email_id]);byId('reader-close').onclick=()=>byId('reader').close();
byId('export').onclick=async()=>{try{const data=await api('/api/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='submission.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Exported submission.json.');}catch(error){notify(error.message);}};
refresh();setInterval(()=>refresh(),3000);
