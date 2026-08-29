(function(){
const B=JSON.parse(document.body.dataset.ll);
const csrf=B.csrf;
const post=(url,body)=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf,...body})}).then(r=>r.json());
const $=s=>document.querySelector(s);
const DEV_PW_HASH='c930f4bedafc8f8dc0fc0b00f85851668dd60cc56c39ae8e1b09f5b2ea1e1902';

function toast(msg,isError){
  const t=$('#toast');t.textContent=msg;
  t.classList.toggle('error',!!isError);
  t.classList.add('show');
  clearTimeout(t._tid);t._tid=setTimeout(()=>t.classList.remove('show'),3500);
}

async function hashString(str){
  const buf=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(str));
  return Array.from(new Uint8Array(buf)).map(b=>b.toString(16).padStart(2,'0')).join('');
}

function showLocked(){
  $('#devLocked').hidden=false;
  $('#devUnlocked').hidden=true;
}

function showUnlocked(){
  $('#devLocked').hidden=true;
  $('#devUnlocked').hidden=false;
  fetch('/api/dev/status').then(r=>r.json()).then(r=>render(r.override||''));
}

async function submitPassword(){
  const input=$('#devPwInput');
  const val=input?.value||'';
  const hash=await hashString(val);
  if(hash===DEV_PW_HASH){
    localStorage.setItem('lensledger_dev','1');
    showUnlocked();
    toast('Dev tools unlocked');
  }else{
    input.value='';
    toast('Wrong password',true);
  }
}

function lockDevTools(){
  localStorage.removeItem('lensledger_dev');
  post('/api/dev/set',{mode:''});
  showLocked();
  toast('Dev tools locked');
}

$('#devPwSubmit').onclick=submitPassword;
$('#devPwInput').addEventListener('keydown',e=>{if(e.key==='Enter')submitPassword();});
$('#lockDevTools').onclick=lockDevTools;

if(localStorage.getItem('lensledger_dev')==='1'){
  showUnlocked();
}else{
  showLocked();
}

const LABELS={onboarding:'First-run setup',reconnection:'Reconnection',picker:'Library picker'};

function render(mode){
  document.querySelectorAll('.override-btn').forEach(btn=>{
    btn.classList.toggle('active',btn.dataset.mode===mode);
  });
  const status=$('#overrideStatus');
  if(mode){
    status.className='override-status active';
    status.textContent='Override active: home page will show the '+LABELS[mode]+' screen.';
  }else{
    status.className='override-status inactive';
    status.textContent='No override — home page shows its normal view.';
  }
}

async function setMode(mode){
  const r=await post('/api/dev/set',{mode});
  if(r.error){toast(r.error,true);return;}
  render(r.override);
  toast(mode?'Override set to '+LABELS[mode]:'Override cleared');
}

document.querySelectorAll('.override-btn').forEach(btn=>{
  btn.onclick=()=>setMode(btn.dataset.mode);
});
$('#resetOverride').onclick=()=>setMode('');

const menuPanel=document.getElementById('menuPanel');
const menuBackdrop=document.getElementById('menuBackdrop');
function openMenu(){menuPanel.classList.add('open');menuBackdrop.classList.add('open')}
function closeMenu(){menuPanel.classList.remove('open');menuBackdrop.classList.remove('open')}
document.getElementById('menuToggle').onclick=e=>{e.stopPropagation();menuPanel.classList.contains('open')?closeMenu():openMenu()};
document.getElementById('menuClose').onclick=closeMenu;
menuBackdrop.onclick=closeMenu;
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{closeMenu();if(b.dataset.panel==='about'){const o=document.getElementById('aboutOverlay');if(o)o.classList.add('open')}else if(b.dataset.panel==='guide'||b.dataset.panel==='update')window.location='/?panel='+b.dataset.panel});
document.getElementById('aboutClose').onclick=document.getElementById('aboutOverlay').onclick=function(e){if(e.target===this||e.target.id==='aboutClose')document.getElementById('aboutOverlay').classList.remove('open')};
window.copyDiagnostics=function(){fetch('/api/diagnostics').then(r=>r.json()).then(d=>{navigator.clipboard.writeText(JSON.stringify(d,null,2));toast('Diagnostics copied')}).catch(()=>toast('Could not copy',true))};
})();
