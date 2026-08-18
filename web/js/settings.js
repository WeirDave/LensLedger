(function(){
const B=JSON.parse(document.body.dataset.ll);
const csrf=B.csrf;
const post=(url,body)=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf,...body})}).then(r=>r.json());
const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];

let settings=B.settings||{};
let libraries=B.libraries||[];
let currentRoot=B.currentRoot||'';
let models=B.models||[];

function toast(msg,isError){
  const t=$('#toast');t.textContent=msg;
  t.classList.toggle('error',!!isError);
  t.classList.add('show');
  clearTimeout(t._tid);t._tid=setTimeout(()=>t.classList.remove('show'),3500);
}

function renderLibraries(){
  const el=$('#libraryList');if(!el)return;
  el.innerHTML='';
  libraries.forEach(lib=>{
    const item=document.createElement('div');item.className='library-item';
    const isCurrent=lib.path.replace(/\\/g,'/').toLowerCase()===currentRoot.replace(/\\/g,'/').toLowerCase();
    item.innerHTML=`<span class="path">${esc(lib.label||lib.path)}</span>`
      +(isCurrent?'<span class="current-badge">Current</span>':'<button type="button" class="secondary switch-btn">Switch</button>')
      +`<button type="button" class="secondary remove-btn" ${isCurrent?'disabled':''}>Remove</button>`;
    if(!isCurrent){
      item.querySelector('.switch-btn').onclick=()=>switchLibrary(lib.path);
      item.querySelector('.remove-btn').onclick=()=>removeLibrary(lib.path);
    }
    el.appendChild(item);
  });
}

function renderModels(){
  const el=$('#modelList');if(!el)return;
  const current=(settings.scan||{}).semantic_model||'ViT-B-32/openai';
  el.innerHTML='';
  models.forEach(m=>{
    const item=document.createElement('label');
    item.className='model-item'+(m.id===current?' active':'');
    item.innerHTML=`<input type="radio" name="semantic_model" value="${esc(m.id)}" ${m.id===current?'checked':''}>`
      +`<div class="model-info"><div class="model-name">${esc(m.name)}</div><div class="model-desc">${esc(m.description)}</div></div>`
      +`<span class="model-size">${esc(m.size)}</span>`;
    item.querySelector('input').onchange=()=>{
      $$('.model-item').forEach(i=>i.classList.remove('active'));
      item.classList.add('active');
    };
    el.appendChild(item);
  });
}

function renderIngestRules(){
  const el=$('#ruleList');if(!el)return;
  const rules=(settings.ingest||{}).rules||[];
  el.innerHTML='';
  rules.forEach((rule,i)=>{
    const item=document.createElement('div');item.className='rule-item';
    item.innerHTML=`<input type="text" value="${esc(rule.match||'')}" placeholder="Match pattern (e.g. person name)" data-idx="${i}" data-field="match">`
      +`<input type="text" value="${esc(rule.destination||'')}" placeholder="Destination subfolder" data-idx="${i}" data-field="destination">`
      +`<button type="button" class="danger remove-rule" data-idx="${i}">✕</button>`;
    el.appendChild(item);
  });
}

function collectSettings(){
  const s=JSON.parse(JSON.stringify(settings));
  const v=(id)=>{const el=$('#'+id);return el?el.value:undefined};
  const n=(id)=>{const el=$('#'+id);return el?parseInt(el.value,10)||0:0};
  const c=(id)=>{const el=$('#'+id);return el?el.checked:false};
  s.scan=s.scan||{};
  s.scan.ocr_workers=n('ocrWorkers');
  s.scan.ocr_batch_size=n('ocrBatchSize');
  s.scan.semantic_batch_size=n('semanticBatchSize');
  const modelRadio=document.querySelector('input[name="semantic_model"]:checked');
  if(modelRadio)s.scan.semantic_model=modelRadio.value;
  s.display=s.display||{};
  s.display.photos_per_page=n('photosPerPage');
  s.display.default_sort=v('defaultSort');
  s.display.filmstrip_size=v('filmstripSize');
  s.watch=s.watch||{};
  s.watch.enabled=c('watchEnabled');
  s.watch.interval_minutes=n('watchInterval');
  s.ingest=s.ingest||{};
  s.ingest.enabled=c('ingestEnabled');
  s.ingest.source_folder=v('ingestSource')||'';
  s.ingest.destination_folder=v('ingestDest')||'';
  const ruleEls=$$('#ruleList .rule-item');
  s.ingest.rules=ruleEls.map(el=>{
    const match=el.querySelector('[data-field="match"]').value.trim();
    const dest=el.querySelector('[data-field="destination"]').value.trim();
    return {match,destination:dest};
  }).filter(r=>r.match||r.destination);
  return s;
}

async function saveSettings(){
  const s=collectSettings();
  const res=await post('/api/settings/save',{settings:s});
  if(res.error){toast(res.error,true);return}
  settings=s;
  toast('Settings saved.');
}

async function switchLibrary(path){
  const res=await post('/api/library/open',{path});
  if(res.error){toast(res.error,true);return}
  currentRoot=path;
  renderLibraries();
  toast('Switched to '+path);
}

async function addLibrary(){
  const res=await post('/api/library/browse',{});
  if(res.error){toast(res.error,true);return}
  if(!res.path)return;
  const openRes=await post('/api/library/open',{path:res.path});
  if(openRes.error){toast(openRes.error,true);return}
  currentRoot=res.path;
  const exists=libraries.some(l=>l.path.replace(/\\/g,'/').toLowerCase()===res.path.replace(/\\/g,'/').toLowerCase());
  if(!exists)libraries.unshift({label:res.path.split(/[\\/]/).pop(),path:res.path});
  renderLibraries();
  toast('Added and switched to '+res.path);
}

async function removeLibrary(path){
  const res=await post('/api/settings/remove-library',{path});
  if(res.error){toast(res.error,true);return}
  libraries=libraries.filter(l=>l.path.replace(/\\/g,'/').toLowerCase()!==path.replace(/\\/g,'/').toLowerCase());
  renderLibraries();
  toast('Removed from list.');
}

async function browseFolder(inputId){
  const res=await post('/api/library/browse',{});
  if(res.error){toast(res.error,true);return}
  if(res.path){const el=$('#'+inputId);if(el)el.value=res.path;}
}

function addRule(){
  const s=settings.ingest=settings.ingest||{};
  s.rules=s.rules||[];
  s.rules.push({match:'',destination:''});
  renderIngestRules();
}

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

document.addEventListener('click',e=>{
  if(e.target.id==='saveSettings')saveSettings();
  if(e.target.id==='addLibrary')addLibrary();
  if(e.target.id==='addRule')addRule();
  if(e.target.id==='browseIngestSource')browseFolder('ingestSource');
  if(e.target.id==='browseIngestDest')browseFolder('ingestDest');
  if(e.target.classList.contains('remove-rule')){
    const idx=parseInt(e.target.dataset.idx,10);
    const rules=(settings.ingest||{}).rules||[];
    rules.splice(idx,1);
    renderIngestRules();
  }
  const menuToggle=$('#menuToggle');
  const menuPanel=$('#menuPanel');
  const menuBackdrop=$('#menuBackdrop');
  if(e.target===menuToggle||e.target.closest('#menuToggle')){
    menuPanel.classList.toggle('open');menuBackdrop.classList.toggle('open');
  }
  if(e.target===menuBackdrop||e.target.id==='menuClose'){
    menuPanel.classList.remove('open');menuBackdrop.classList.remove('open');
  }
});

async function loadExportStatus(){
  try{
    const res=await fetch('/api/settings/export-status');
    const data=await res.json();
    const el=$('#exportStatus');
    if(el&&data.last_backup)el.textContent='Last backup: '+data.last_backup;
  }catch(e){}
}

const exportBtn=$('#exportDatabase');
if(exportBtn)exportBtn.onclick=async()=>{
  exportBtn.disabled=true;exportBtn.textContent='Exporting…';
  const res=await post('/api/settings/export',{});
  exportBtn.disabled=false;exportBtn.textContent='Export database';
  if(res.error){toast(res.error,true);return}
  toast('Database exported to '+res.path);
  const el=$('#exportStatus');if(el)el.textContent='Last export: just now';
};

const importBtn=$('#importDatabase');
if(importBtn)importBtn.onclick=async()=>{
  importBtn.disabled=true;importBtn.textContent='Importing…';
  const res=await post('/api/settings/import',{});
  importBtn.disabled=false;importBtn.textContent='Import database';
  if(res.error){toast(res.error,true);return}
  toast('Database imported: '+res.message);
};

renderLibraries();
renderModels();
renderIngestRules();
loadExportStatus();
})();
