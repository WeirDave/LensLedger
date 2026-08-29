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
    item.innerHTML=`<span class="path">${esc(lib.path)}</span>`
      +(isCurrent?'<span class="current-badge">Current</span>':'<button type="button" class="secondary switch-btn">Switch</button>')
      +`<button type="button" class="secondary relocate-btn">Relocate…</button>`
      +`<button type="button" class="secondary remove-btn" ${isCurrent?'disabled':''}>Remove</button>`;
    if(!isCurrent){
      item.querySelector('.switch-btn').onclick=()=>switchLibrary(lib.path);
      item.querySelector('.remove-btn').onclick=()=>removeLibrary(lib.path);
    }
    item.querySelector('.relocate-btn').onclick=()=>relocateLibrary(lib.path);
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
  s.ingest.interval_minutes=Math.max(5,Math.min(1440,n('ingestInterval')||10));
  s.startup=s.startup||{};
  s.startup.show_library_picker=c('showLibraryPicker');
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
  if(!confirm('Switch to this library? Your current library will remain in the list.'))return;
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
  const dbChoice=prompt(
    'Where should the database be stored?\n\n'
    +'1) Inside the photo library folder (default — index travels with your photos)\n'
    +'2) Application data folder (keeps photos folder clean)\n\n'
    +'Enter 1 or 2:','1');
  if(!dbChoice)return;
  const dbLocation=dbChoice.trim()==='2'?'appdata':'library';
  const addRes=await post('/api/library/add',{path:res.path,db_location:dbLocation});
  if(addRes.error){toast(addRes.error,true);return}
  const exists=libraries.some(l=>l.path.replace(/\\/g,'/').toLowerCase()===res.path.replace(/\\/g,'/').toLowerCase());
  if(!exists)libraries.unshift({label:res.path.split(/[\\/]/).pop(),path:res.path});
  renderLibraries();
  toast('Library added: '+res.path+'. Switch to it and scan when ready.');
}

async function relocateLibrary(oldPath){
  const res=await post('/api/library/browse',{});
  if(res.error){toast(res.error,true);return}
  if(!res.path)return;
  if(!confirm('Move library from:\n'+oldPath+'\n\nTo:\n'+res.path+'\n\nThis updates the database to point at the new location. Your photos must already be at the new path.'))return;
  const result=await post('/api/library/relocate',{old_path:oldPath,new_path:res.path});
  if(result.error){toast(result.error,true);return}
  const oldNorm=oldPath.replace(/\\/g,'/').toLowerCase();
  libraries=libraries.map(l=>l.path.replace(/\\/g,'/').toLowerCase()===oldNorm?{label:res.path.split(/[\\/]/).pop(),path:res.path}:l);
  if(currentRoot.replace(/\\/g,'/').toLowerCase()===oldNorm)currentRoot=res.path;
  renderLibraries();
  toast('Library relocated to '+res.path);
}

async function removeLibrary(path){
  if(!confirm('Remove this library from the list? Your photos and index data are not deleted.'))return;
  const res=await post('/api/settings/remove-library',{path});
  if(res.error){toast(res.error,true);return}
  libraries=libraries.filter(l=>l.path.replace(/\\/g,'/').toLowerCase()!==path.replace(/\\/g,'/').toLowerCase());
  renderLibraries();
  toast('Removed from list.');
}

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

document.addEventListener('click',e=>{
  if(e.target.id==='saveSettings')saveSettings();
  if(e.target.id==='addLibrary')addLibrary();
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
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{$('#menuPanel').classList.remove('open');$('#menuBackdrop').classList.remove('open');if(b.dataset.panel==='about'){const o=document.getElementById('aboutOverlay');if(o)o.classList.add('open')}else if(b.dataset.panel==='guide'||b.dataset.panel==='update')window.location='/?panel='+b.dataset.panel});
document.getElementById('aboutClose').onclick=document.getElementById('aboutOverlay').onclick=function(e){if(e.target===this||e.target.id==='aboutClose')document.getElementById('aboutOverlay').classList.remove('open')};

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

let semanticInstalled=false;
let semanticPolling=false;

async function checkSemanticStatus(){
  try{
    const res=await fetch('/api/semantic/status');
    const data=await res.json();
    const setupArea=$('#semanticSetupArea');
    const intro=$('#modelIntro');
    const modelList=$('#modelList');
    const msg=$('#semanticSetupMsg');
    const barWrap=$('#semanticInstallBarWrap');
    const installBtn=$('#installSemantic');
    const install=data.install||{};
    if(data.installed){
      semanticInstalled=true;
      setupArea.hidden=true;
      intro.hidden=false;
      modelList.hidden=false;
      semanticPolling=false;
    }else{
      intro.hidden=true;
      modelList.hidden=true;
      setupArea.hidden=false;
      if(install.state==='installing'){
        msg.textContent=install.message||'Installing meaning search software…';
        barWrap.hidden=false;
        barWrap.classList.add('indeterminate');
        installBtn.hidden=true;
        if(!semanticPolling){semanticPolling=true;setTimeout(checkSemanticStatus,2000);}
        else setTimeout(checkSemanticStatus,2000);
        return;
      }else if(install.state==='complete'){
        semanticInstalled=true;
        setupArea.hidden=true;
        intro.hidden=false;
        modelList.hidden=false;
        semanticPolling=false;
        toast('Meaning search installed successfully.');
        return;
      }else if(install.state==='error'){
        msg.textContent=install.message||'Install failed.';
        barWrap.hidden=true;
        installBtn.hidden=false;
        installBtn.disabled=false;
        semanticPolling=false;
      }else{
        msg.textContent='Meaning search is not installed. Click the button below to download and install the model software (roughly 1–2 GB).';
        barWrap.hidden=true;
        installBtn.hidden=false;
        installBtn.disabled=false;
        semanticPolling=false;
      }
    }
  }catch(e){
    const msg=$('#semanticSetupMsg');
    if(msg)msg.textContent='Could not check meaning search status.';
    semanticPolling=false;
  }
}

$('#installSemantic').onclick=async(e)=>{
  e.preventDefault();
  if(!confirm('This downloads and installs the local meaning-search model software (roughly 1–2 GB) and may take several minutes. It runs entirely on this computer and nothing is uploaded. Continue?'))return;
  const btn=$('#installSemantic');
  btn.disabled=true;
  try{
    await post('/api/semantic/install',{});
    semanticPolling=true;
    checkSemanticStatus();
  }catch(err){
    toast(err.message||'Install failed',true);
    btn.disabled=false;
  }
};

if(location.hash==='#meaning-search'){
  const el=$('#meaning-search');
  if(el)el.scrollIntoView({behavior:'smooth',block:'start'});
}

renderLibraries();
renderModels();
loadExportStatus();
checkSemanticStatus();

const tocLinks=$$('.settings-toc a');
if(tocLinks.length){
  const sections=tocLinks.map(a=>document.querySelector(a.getAttribute('href'))).filter(Boolean);
  function updateToc(){
    let active=sections[0];
    for(const s of sections){if(s.getBoundingClientRect().top<=100)active=s}
    tocLinks.forEach(a=>{a.classList.toggle('active',a.getAttribute('href')==='#'+active.id)});
  }
  window.addEventListener('scroll',updateToc,{passive:true});
  updateToc();
  tocLinks.forEach(a=>a.addEventListener('click',e=>{
    e.preventDefault();const t=document.querySelector(a.getAttribute('href'));
    if(t)t.scrollIntoView({behavior:'smooth',block:'start'});
  }));
}

function checkServerVersion(){
  fetch('/api/version',{cache:'no-store'}).then(r=>r.json()).then(info=>{
    const existing=document.getElementById('staleBanner');
    if(!info.restartReady){if(existing)existing.remove();return}
    if(existing)return;
    const b=document.createElement('div');b.id='staleBanner';b.className='stale-banner';
    b.innerHTML='<span class="stale-icon">⚠</span>'
      +'<span class="stale-msg"><b>Server is running v'+info.version+'</b> but v'+info.onDiskVersion+' is on disk. Click <b>Restart</b> to load it.</span>'
      +'<button type="button" class="stale-restart">Restart now</button>'
      +'<button type="button" class="stale-close" title="Dismiss">×</button>';
    b.querySelector('.stale-close').onclick=()=>b.remove();
    b.querySelector('.stale-restart').onclick=()=>{
      const btn=b.querySelector('.stale-restart');const msg=b.querySelector('.stale-msg');
      btn.disabled=true;btn.textContent='Restarting…';
      msg.innerHTML='<b>Restarting server…</b> Page will reload when the new version is ready.';
      const oldStarted=info.startedAt;
      post('/api/update/restart-source',{}).catch(()=>{});
      const deadline=Date.now()+20000;
      (function poll(){
        if(Date.now()>deadline){msg.innerHTML='<b>Server did not restart.</b> Close and reopen LensLedger manually.';return}
        fetch('/api/version',{cache:'no-store'}).then(r=>r.json()).then(j=>{
          if(j&&j.startedAt&&j.startedAt!==oldStarted)setTimeout(()=>location.reload(),200);
          else setTimeout(poll,500);
        }).catch(()=>setTimeout(poll,700));
      })();
    };
    document.body.prepend(b);
  }).catch(()=>{});
}
checkServerVersion();setInterval(checkServerVersion,30000);
})();
