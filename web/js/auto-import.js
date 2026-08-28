(function(){
const B=JSON.parse(document.body.dataset.ll);
const csrf=B.csrf;
const post=(url,body)=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf,...body})}).then(r=>r.json());
const $=id=>document.getElementById(id);

let config=B.ingest||{};
let rules=config.rules||[];
let pollTimer=null;

function toast(msg,isError){
  const t=$('toast');t.textContent=msg;
  t.classList.toggle('error',!!isError);
  t.classList.add('show');
  clearTimeout(t._tid);t._tid=setTimeout(()=>t.classList.remove('show'),3500);
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function updatePreview(){
  const template=$('templateInput').value||'{year}/{year}_{month}_{day}';
  const now=new Date();
  const example=template
    .replace(/\{year\}/g,String(now.getFullYear()))
    .replace(/\{month\}/g,String(now.getMonth()+1).padStart(2,'0'))
    .replace(/\{day\}/g,String(now.getDate()).padStart(2,'0'))
    .replace(/\{hour\}/g,String(now.getHours()).padStart(2,'0'))
    .replace(/\{minute\}/g,String(now.getMinutes()).padStart(2,'0'));
  $('templatePreview').innerHTML='Today\'s photos would go to: <code>'+esc(example)+'</code>';
}

function renderRules(){
  const el=$('ruleList');
  el.innerHTML='';
  if(!rules.length){el.innerHTML='<p class="log-empty">No override rules. All photos use the date template above.</p>';return;}
  rules.forEach((rule,i)=>{
    const item=document.createElement('div');item.className='rule-item';
    item.innerHTML=
      '<input type="text" value="'+esc(rule.match||'')+'" placeholder="Filename contains…" data-idx="'+i+'" data-field="match">'
      +'<input type="text" value="'+esc(rule.destination||'')+'" placeholder="Destination subfolder template" data-idx="'+i+'" data-field="destination">'
      +'<button type="button" class="danger remove-rule" data-idx="'+i+'">&#x2715;</button>';
    el.appendChild(item);
  });
}

function collectConfig(){
  const c={};
  c.enabled=$('ingestEnabled').checked;
  c.source_folder=$('ingestSource').value.trim();
  c.destination_folder=$('ingestDest').value.trim();
  c.default_template=$('templateInput').value.trim()||'{year}/{year}_{month}_{day}';
  c.interval_minutes=Math.max(5,Math.min(1440,parseInt($('ingestInterval').value,10)||10));
  const ruleEls=document.querySelectorAll('#ruleList .rule-item');
  c.rules=Array.from(ruleEls).map(el=>{
    const match=el.querySelector('[data-field="match"]').value.trim();
    const dest=el.querySelector('[data-field="destination"]').value.trim();
    return {match,destination:dest};
  }).filter(r=>r.match||r.destination);
  return c;
}

async function runNow(){
  const btn=$('runNow');
  btn.disabled=true;btn.textContent='Running…';
  try{
    const res=await post('/api/ingest/run',{});
    if(res.error){toast(res.error,true);return;}
    const d=res.delta||{};
    toast('Run complete — '+
      (d.ingested||0)+' ingested, '+
      (d.duplicates||0)+' duplicates, '+
      (d.errors||0)+' errors');
    refreshStatus();loadLog();
  }catch(e){toast('Run failed: '+e.message,true);}
  finally{btn.disabled=false;btn.textContent='Run now';}
}

async function saveConfig(){
  const c=collectConfig();
  const res=await post('/api/ingest/save',{config:c});
  if(res.error){toast(res.error,true);return;}
  config=c;
  rules=c.rules||[];
  toast('Auto-import configuration saved.');
}

async function refreshStatus(){
  try{
    const res=await fetch('/api/ingest/status');
    const data=await res.json();
    const stats=data.stats||{};
    $('statIngested').textContent=String(stats.ingested||0);
    $('statDuplicates').textContent=String(stats.duplicates||0);
    $('statErrors').textContent=String(stats.errors||0);
    $('statusEnabled').textContent=data.enabled?'Running':'Stopped';
  }catch(e){}
}

async function loadLog(){
  try{
    const res=await fetch('/api/ingest/log');
    const data=await res.json();
    const entries=data.entries||[];
    const el=$('logList');
    if(!entries.length){el.innerHTML='<p class="log-empty">No activity yet. The pipeline will log every file it processes here.</p>';return;}
    el.innerHTML='';
    entries.reverse().forEach(entry=>{
      const div=document.createElement('div');div.className='log-entry';
      const action=entry.action||'unknown';
      let path=entry.source||'';
      if(action==='ingested'&&entry.destination)path=entry.source+' → '+entry.destination;
      if(action==='error'&&entry.error)path=entry.source+': '+entry.error;
      const time=entry.timestamp?new Date(entry.timestamp).toLocaleString():'';
      div.innerHTML='<span class="log-action '+esc(action)+'">'+esc(action.replace('_',' '))+'</span>'
        +'<span class="log-path">'+esc(path)+'</span>'
        +'<span class="log-time">'+esc(time)+'</span>';
      el.appendChild(div);
    });
  }catch(e){}
}

async function browseFolder(inputId){
  const res=await post('/api/library/browse',{});
  if(res.error){toast(res.error,true);return;}
  if(res.path){const el=$(inputId);if(el)el.value=res.path;}
}

document.addEventListener('click',e=>{
  if(e.target.id==='saveConfig')saveConfig();
  if(e.target.id==='runNow')runNow();
  if(e.target.id==='addRule'){rules.push({match:'',destination:''});renderRules();}
  if(e.target.id==='browseIngestSource')browseFolder('ingestSource');
  if(e.target.id==='browseIngestDest')browseFolder('ingestDest');
  if(e.target.id==='refreshLog')loadLog();
  if(e.target.classList.contains('remove-rule')){
    const idx=parseInt(e.target.dataset.idx,10);
    rules.splice(idx,1);
    renderRules();
  }
  const toggle=document.getElementById('menuToggle');
  const panel=document.getElementById('menuPanel');
  const backdrop=document.getElementById('menuBackdrop');
  if(e.target===toggle||e.target.closest('#menuToggle')){
    panel.classList.toggle('open');backdrop.classList.toggle('open');
  }
  if(e.target===backdrop||e.target.id==='menuClose'){
    panel.classList.remove('open');backdrop.classList.remove('open');
  }
});
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{document.getElementById('menuPanel').classList.remove('open');document.getElementById('menuBackdrop').classList.remove('open');if(b.dataset.panel==='about'){const o=document.getElementById('aboutOverlay');if(o)o.classList.add('open')}else if(b.dataset.panel==='guide'||b.dataset.panel==='update')window.location='/?panel='+b.dataset.panel});
document.getElementById('aboutClose').onclick=document.getElementById('aboutOverlay').onclick=function(e){if(e.target===this||e.target.id==='aboutClose')document.getElementById('aboutOverlay').classList.remove('open')};

$('templateInput').addEventListener('input',updatePreview);

updatePreview();
renderRules();
refreshStatus();
loadLog();
pollTimer=setInterval(refreshStatus,15000);

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
