const LL = JSON.parse(document.body.dataset.ll);
const items=LL.items; const personDirectory=LL.personDirectory; const csrf=LL.csrf; const currentLibrary=LL.currentLibrary; const viewedPersonId=LL.viewedPersonId; let selectedId=LL.selectedId; let currentDetail=null;
const $=id=>document.getElementById(id); const stage=$('stage');
let sidebarKnownPeople=[];let personPicker=null;
async function api(path,payload){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,csrf})});const data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');return data}
function setStatus(text,error=false){$('status').textContent=text;$('status').style.color=error?'#e25c70':'#e2903f'}
function chip(tag,kind){const el=document.createElement('span');el.className='chip '+(kind==='context'?'context':'');el.append(document.createTextNode(tag.name));const b=document.createElement('button');b.textContent='×';b.title='Hide this tag for this photo';b.onclick=()=>removeTag(tag);el.append(b);return el}
function renderSubject(detail){const holder=$('subjectChip');if(!detail.subject){holder.replaceChildren();$('subjectInput').placeholder='Example: Formula 1 race cars';return}const el=document.createElement('span');el.className='chip subject';el.append(document.createTextNode(detail.subject));const b=document.createElement('button');b.textContent='×';b.title='Clear the primary subject';b.onclick=clearSubject;el.append(b);holder.replaceChildren(el);$('subjectInput').placeholder='Replace the current subject'}
function renderChips(detail){renderSubject(detail);$('imageTags').replaceChildren(...detail.image_tags.map(t=>chip(t,'image')));$('contextTags').replaceChildren(...detail.context_tags.map(t=>chip(t,'context')));const hidden=detail.hidden_tags.map(name=>{const e=document.createElement('button');e.className='chip hidden';e.textContent='Restore '+name;e.onclick=()=>restoreTag(name);return e});$('hiddenTags').replaceChildren(...hidden);$('hiddenSection').style.display=hidden.length?'block':'none'}
function renderPeople(detail){const confirmed=detail.confirmed_people.map(person=>{const el=document.createElement('span');el.className='chip person';el.append(document.createTextNode(person.name));const remove=document.createElement('button');remove.textContent='×';remove.title='Remove this person from the photo';remove.onclick=()=>changePerson(person.name,'rejected');el.append(remove);return el});$('confirmedPeople').replaceChildren(...confirmed);const suggested=detail.suggested_people.map(person=>{const el=document.createElement('span');el.className='chip suggestion';el.append(document.createTextNode(person.name+' '+Math.round(person.confidence*100)+'%'));const accept=document.createElement('button');accept.className='accept-person';accept.textContent='✓';accept.title='Confirm this person';accept.onclick=()=>changePerson(person.name,'confirmed');const reject=document.createElement('button');reject.textContent='×';reject.title='Reject this suggestion';reject.onclick=()=>changePerson(person.name,'rejected');el.append(accept,reject);return el});$('suggestedPeople').replaceChildren(...suggested);$('suggestionLabel').style.display=suggested.length?'block':'none';sidebarKnownPeople=detail.people_options}
function renderEmbeddedMetadata(meta){const holder=$('metadataReadout');holder.replaceChildren();let count=0;for(const [key,title] of [['descriptive','Description and ownership'],['capture','Capture details']]){const items=meta?.[key]||[];if(!items.length)continue;count+=items.length;const group=document.createElement('section');group.className='metadata-group';const heading=document.createElement('h3');heading.textContent=title;const list=document.createElement('dl');for(const item of items){const row=document.createElement('div');row.className='metadata-item';const term=document.createElement('dt');term.textContent=item.label;const value=document.createElement('dd');if(item.href){const link=document.createElement('a');link.href=item.href;link.target='_blank';link.rel='noopener';link.title='Open this location on the local photo map';link.textContent=item.value+'  ↗';value.append(link);if(item.osmHref){value.append(' · ');const osmLink=document.createElement('a');osmLink.href=item.osmHref;osmLink.target='_blank';osmLink.rel='noopener';osmLink.title='Open this location on OpenStreetMap';osmLink.textContent='OpenStreetMap ↗';value.append(osmLink)}}else value.textContent=item.value;row.append(term,value);list.append(row)}group.append(heading,list);holder.append(group)}if(!count){const empty=document.createElement('div');empty.className='metadata-empty';empty.textContent='No readable embedded details were found in this file.';holder.append(empty)}}
let zoomScale=1,panX=0,panY=0,zoomMedia=null,zoomResizeObserver=null;
function applyZoomTransform(){
  if(!zoomMedia)return;
  zoomMedia.style.transform=zoomScale===1?'':'scale('+zoomScale+') translate('+panX+'px,'+panY+'px)';
  zoomMedia.classList.toggle('zoomed',zoomScale>1);
  const controls=$('zoomControls');
  controls.classList.toggle('visible',zoomScale>1);
  $('zoomLevel').textContent=Math.round(zoomScale*100)+'%';
}
function clampPan(){
  if(!zoomMedia||zoomScale<=1){panX=0;panY=0;return}
  const stageW=stage.clientWidth,stageH=stage.clientHeight;
  const fitScale=Math.min(zoomMedia.clientWidth/zoomMedia.naturalWidth,zoomMedia.clientHeight/zoomMedia.naturalHeight);
  const imgW=zoomMedia.naturalWidth*fitScale*zoomScale,imgH=zoomMedia.naturalHeight*fitScale*zoomScale;
  const maxPanX=Math.max(0,(imgW-stageW)/(2*zoomScale));
  const maxPanY=Math.max(0,(imgH-stageH)/(2*zoomScale));
  panX=Math.max(-maxPanX,Math.min(maxPanX,panX));
  panY=Math.max(-maxPanY,Math.min(maxPanY,panY));
}
function resetZoom(){zoomScale=1;panX=0;panY=0;applyZoomTransform()}
function initZoom(media){
  zoomMedia=media;zoomScale=1;panX=0;panY=0;
  media.style.transformOrigin='center center';
  applyZoomTransform();
}
stage.addEventListener('wheel',e=>{
  if(!zoomMedia||zoomMedia.tagName==='VIDEO')return;
  e.preventDefault();
  const prev=zoomScale;
  const factor=e.deltaY<0?1.15:1/1.15;
  zoomScale=Math.max(1,Math.min(20,zoomScale*factor));
  if(zoomScale===1){panX=0;panY=0}
  else{
    const rect=zoomMedia.getBoundingClientRect();
    const cx=(e.clientX-rect.left)/rect.width-0.5;
    const cy=(e.clientY-rect.top)/rect.height-0.5;
    panX-=cx*(zoomScale-prev)/zoomScale*zoomMedia.clientWidth/zoomScale;
    panY-=cy*(zoomScale-prev)/zoomScale*zoomMedia.clientHeight/zoomScale;
  }
  clampPan();applyZoomTransform();repositionFaceBox()
},{passive:false});
let panActive=false,panStartX=0,panStartY=0,panStartPanX=0,panStartPanY=0,panPointerId=null;
stage.addEventListener('pointerdown',e=>{
  if(!zoomMedia||zoomScale<=1||e.button!==0||e.target.closest('.stage-nav,.zoom-controls'))return;
  panActive=true;panPointerId=e.pointerId;panStartX=e.clientX;panStartY=e.clientY;panStartPanX=panX;panStartPanY=panY;
  zoomMedia.classList.add('panning');stage.setPointerCapture(e.pointerId);e.preventDefault()
});
stage.addEventListener('pointermove',e=>{
  if(!panActive||e.pointerId!==panPointerId)return;
  panX=panStartPanX+(e.clientX-panStartX)/zoomScale;
  panY=panStartPanY+(e.clientY-panStartY)/zoomScale;
  clampPan();applyZoomTransform();repositionFaceBox()
});
stage.addEventListener('pointerup',e=>{
  if(!panActive||e.pointerId!==panPointerId)return;
  panActive=false;zoomMedia?.classList.remove('panning');
  if(stage.hasPointerCapture(e.pointerId))stage.releasePointerCapture(e.pointerId)
});
stage.addEventListener('pointercancel',()=>{panActive=false;zoomMedia?.classList.remove('panning')});
stage.addEventListener('dblclick',e=>{
  if(!zoomMedia||zoomMedia.tagName==='VIDEO'||e.target.closest('.stage-nav,.zoom-controls'))return;
  if(zoomScale>1){resetZoom();repositionFaceBox();return}
  zoomScale=3;clampPan();applyZoomTransform();repositionFaceBox()
});
$('zoomReset').onclick=()=>{resetZoom();repositionFaceBox()};

let faceBoxMarker=null,faceBoxFace=null,faceBoxMedia=null;
function repositionFaceBox(){
  if(!faceBoxMarker||!faceBoxMedia||!faceBoxFace)return;
  const media=faceBoxMedia,face=faceBoxFace,marker=faceBoxMarker;
  if(!media.naturalWidth||!media.naturalHeight)return;
  const fitScale=Math.min(media.clientWidth/media.naturalWidth,media.clientHeight/media.naturalHeight);
  const shownWidth=media.naturalWidth*fitScale,shownHeight=media.naturalHeight*fitScale;
  const mediaRect=media.getBoundingClientRect(),stageRect=stage.getBoundingClientRect();
  const offsetX=mediaRect.left-stageRect.left+(media.clientWidth-shownWidth)/2;
  const offsetY=mediaRect.top-stageRect.top+(media.clientHeight-shownHeight)/2;
  marker.style.left=(offsetX+face.box_left*shownWidth)+'px';
  marker.style.top=(offsetY+face.box_top*shownHeight)+'px';
  marker.style.width=((face.box_right-face.box_left)*shownWidth)+'px';
  marker.style.height=((face.box_bottom-face.box_top)*shownHeight)+'px';
}
function renderFocusedFace(media,face){
  faceBoxMarker=null;faceBoxFace=null;faceBoxMedia=null;
  if(!media||media.tagName!=='IMG'||!face||![face.box_left,face.box_top,face.box_right,face.box_bottom].every(Number.isFinite))return;
  const marker=document.createElement('div');
  marker.className='focused-face-box';
  marker.textContent='Face for '+face.name;
  Object.assign(marker.style,{position:'absolute',zIndex:'1',pointerEvents:'none',border:'3px solid #e2903f',boxShadow:'0 0 0 2px #00131b,0 0 18px #e2903faa',borderRadius:'4px',color:'#fff3e0',fontWeight:'800',fontSize:'12px'});
  faceBoxMarker=marker;faceBoxFace=face;faceBoxMedia=media;
  stage.append(marker);
  stage.querySelectorAll('.stage-nav').forEach(button=>button.style.zIndex='2');
  media.addEventListener('load',repositionFaceBox,{once:true});
  if(media.complete)repositionFaceBox();
  if(zoomResizeObserver)zoomResizeObserver.disconnect();
  zoomResizeObserver=new ResizeObserver(repositionFaceBox);
  zoomResizeObserver.observe(stage);
}
async function selectAsset(id){
  selectedId=Number(id);
  document.querySelectorAll('.thumb').forEach(x=>{const isActive=Number(x.dataset.id)===selectedId;x.classList.toggle('active',isActive);if(isActive)x.setAttribute('aria-current','true');else x.removeAttribute('aria-current')});
  const active=document.querySelector('.thumb.active');active?.scrollIntoView({block:'nearest',inline:'center'});
  try{
    const detailUrl='/api/asset?id='+selectedId+(viewedPersonId?'&person_id='+viewedPersonId:'');
    const r=await fetch(detailUrl);if(!r.ok)throw new Error('Could not load photo');
    const d=await r.json();currentDetail=d;
    stage.querySelectorAll('img,video,.empty,.raw-preview,.focused-face-box').forEach(x=>x.remove());
    resetZoom();zoomMedia=null;
    if(d.media_type==='raw'){
      const raw=document.createElement('div');raw.className='raw-preview';raw.innerHTML='<strong>RAW photo</strong>LensLedger indexed this original file.<br>A browser preview is not available yet.';stage.prepend(raw);
    }else{
      const media=document.createElement(d.media_type==='video'?'video':'img');media.src='/media?id='+d.id;if(d.media_type==='video')media.controls=true;stage.prepend(media);initZoom(media);renderFocusedFace(media,d.focused_person_face);
    }
    $('assetDate').textContent=d.capture_date||'Date unknown';$('assetName').textContent=d.filename;$('assetFolder').textContent=d.folder;$('subjectInput').value='';personPicker?.reset();$('publishDescription').value=d.embedded_metadata?.description||'';$('previewPublish').disabled=!d.publishable;$('restorePublish').disabled=!d.can_restore_publish;$('publishNote').textContent=d.publishable?'Only this selected photo will be changed, and only after you approve the preview.':'Publishing is currently available for JPEG photos.';renderChips(d);renderPeople(d);renderEmbeddedMetadata(d.embedded_metadata);setStatus('');
  }catch(e){setStatus(e.message,true)}
  updateNav();
}
function updateNav(){const i=items.findIndex(x=>Number(x.id)===selectedId);$('previousPhoto').disabled=i<=0;$('nextPhoto').disabled=i<0||i>=items.length-1}
function step(delta){const i=items.findIndex(x=>Number(x.id)===selectedId);const next=items[i+delta];if(next)selectAsset(next.id)}
async function saveSubject(){const value=$('subjectInput').value.trim();if(!value){setStatus('Enter a primary subject first',true);return}try{await api('/api/subject',{id:selectedId,subject:value});await selectAsset(selectedId);setStatus('Primary subject saved')}catch(e){setStatus(e.message,true)}}
async function clearSubject(){try{await api('/api/subject',{id:selectedId,subject:''});await selectAsset(selectedId);setStatus('Primary subject cleared')}catch(e){setStatus(e.message,true)}}
async function addTag(){const value=$('newTag').value.trim();if(!value)return;try{const result=await api('/api/tag/add',{id:selectedId,tag:value});$('newTag').value='';await selectAsset(selectedId);setStatus(result.added===1?'1 photo tag added':result.added+' photo tags added')}catch(e){setStatus(e.message,true)}}
async function addPerson(name){if(!name)return;try{await api('/api/person/add',{id:selectedId,name});await selectAsset(selectedId);setStatus(name+' confirmed in this photo')}catch(e){setStatus(e.message,true)}}
async function changePerson(name,state){try{await api('/api/person/state',{id:selectedId,name,state});await selectAsset(selectedId);setStatus(state==='confirmed'?name+' confirmed':name+' removed from this photo')}catch(e){setStatus(e.message,true)}}
async function addContextTag(){const value=$('newContextTag').value.trim();if(!value)return;try{const result=await api('/api/folder-tag/add',{id:selectedId,tag:value});$('newContextTag').value='';await selectAsset(selectedId);if(result.added===0)setStatus('Those tags are already on this event');else setStatus(result.added===1?'Event tag added to '+result.assets+' photos':result.added+' event tags added to '+result.assets+' photos')}catch(e){setStatus(e.message,true)}}
async function removeTag(tag){try{await api('/api/tag/remove',{id:selectedId,tag:tag.name,source:tag.source});await selectAsset(selectedId);setStatus('Tag hidden for this photo')}catch(e){setStatus(e.message,true)}}
async function restoreTag(name){try{await api('/api/tag/restore',{id:selectedId,tag:name});await selectAsset(selectedId);setStatus('Tag restored')}catch(e){setStatus(e.message,true)}}
async function moveToBin(){if(!currentDetail||!confirm('Move “'+currentDetail.filename+'” to Trash?\n\nIt will leave the photo library and disappear from search. You can undo immediately or restore it later from ☰ → Trash & restore.'))return;try{const oldId=selectedId;const result=await api('/api/review-bin',{id:selectedId});const i=items.findIndex(x=>Number(x.id)===oldId);items.splice(i,1);document.querySelector('.thumb[data-id="'+oldId+'"]').remove();showUndo(result.review_id,currentDetail.filename);if(items.length)selectAsset(items[Math.min(i,items.length-1)].id);else location.reload()}catch(e){setStatus(e.message,true)}}
function showUndo(reviewId,name){const t=$('toast');t.replaceChildren(document.createTextNode('Moved '+name+' to Trash. '));const b=document.createElement('button');b.textContent='Undo';b.onclick=async()=>{try{await api('/api/review-bin/restore',{review_id:reviewId});location.reload()}catch(e){setStatus(e.message,true)}};t.append(b);t.style.display='block';setTimeout(()=>t.style.display='none',12000)}
function closeHelp(){document.querySelectorAll('.help-popover.open').forEach(x=>x.classList.remove('open'))}
function openModal(title,content){document.querySelector('.modal').classList.remove('publish-modal');$('modalTitle').textContent=title;$('modalBody').replaceChildren(content);$('modalBackdrop').classList.add('open')}
function textPanel(paragraphs){const box=document.createElement('div');paragraphs.forEach(text=>{const p=document.createElement('p');p.textContent=text;box.append(p)});return box}
function editAliases(button){const personId=Number(button.dataset.personId);const personName=button.dataset.personName;const aliases=JSON.parse(button.dataset.aliases);const box=document.createElement('div');box.className='alias-editor';const note=document.createElement('p');note.textContent='Change the primary name everywhere in LensLedger. Alternate names are optional and only kept when you enter them below.';const primaryLabel=document.createElement('label');primaryLabel.textContent='Primary name';const primary=document.createElement('input');primary.value=personName;primary.placeholder='Full name';primaryLabel.append(primary);const aliasLabel=document.createElement('label');aliasLabel.textContent='Other names for this same person (optional — separate each name with a comma)';const aliasInput=document.createElement('input');aliasInput.value=aliases.join(', ');aliasInput.placeholder='Example: Dave, David, Me';aliasLabel.append(aliasInput);const actions=document.createElement('div');actions.className='publish-actions';const cancel=document.createElement('button');cancel.className='secondary';cancel.textContent='Cancel';cancel.onclick=()=>$('modalBackdrop').classList.remove('open');const save=document.createElement('button');save.textContent='Save names';save.onclick=async()=>{save.disabled=true;try{await api('/api/person/names',{person_id:personId,name:primary.value,aliases:aliasInput.value.split(',')});location.reload()}catch(e){save.disabled=false;note.textContent=e.message;note.style.color='#e25c70'}};actions.append(cancel,save);box.append(note,primaryLabel,aliasLabel,actions);openModal('Names for '+personName,box);primary.focus();primary.select()}
function openPersonMerge(){if(personDirectory.length<2)return;const box=document.createElement('div');box.className='person-merge';const intro=document.createElement('p');intro.textContent='Choose the one record to keep, then select every duplicate name that belongs to the same person.';const keepLabel=document.createElement('label');keepLabel.textContent='Keep this as the primary name';const primary=document.createElement('select');personDirectory.forEach(person=>{const option=document.createElement('option');option.value=person.id;option.textContent=person.name;primary.append(option)});keepLabel.append(primary);const sourceLabel=document.createElement('label');sourceLabel.textContent='Merge these duplicate names into the primary name';const filter=document.createElement('input');filter.placeholder='Filter names to merge';const choices=document.createElement('div');choices.className='person-merge-options';const note=document.createElement('div');note.className='person-merge-note';note.textContent='The names you merge become alternate searchable names. Photo links are combined, and a confirmed decision wins if the same photo has conflicting decisions. Confirmed JPEG metadata is updated with a safety backup. This cannot be automatically undone.';const status=document.createElement('p');status.className='status';const actions=document.createElement('div');actions.className='publish-actions';const cancel=document.createElement('button');cancel.className='secondary';cancel.textContent='Cancel';cancel.onclick=()=>$('modalBackdrop').classList.remove('open');const merge=document.createElement('button');merge.textContent='Merge selected names';const selectedSourceIds=new Set();function renderChoices(){const primaryId=Number(primary.value);const query=filter.value.trim().toLocaleLowerCase();choices.replaceChildren(...personDirectory.filter(person=>Number(person.id)!==primaryId&&(!query||person.name.toLocaleLowerCase().includes(query))).map(person=>{const label=document.createElement('label');label.className='person-merge-option';const check=document.createElement('input');check.type='checkbox';check.value=person.id;check.checked=selectedSourceIds.has(Number(person.id));check.onchange=()=>{if(check.checked)selectedSourceIds.add(Number(person.id));else selectedSourceIds.delete(Number(person.id))};const details=document.createElement('span');const name=document.createElement('strong');name.textContent=person.name;const count=document.createElement('small');const confirmed=Number(person.confirmed_count)||0;const suggested=Number(person.suggested_count)||0;count.textContent=confirmed+' confirmed · '+suggested+' to review';details.append(name,count);label.append(check,details);return label}));merge.disabled=!choices.querySelector('input')&&!selectedSourceIds.size}primary.onchange=()=>{selectedSourceIds.clear();renderChoices()};filter.oninput=renderChoices;merge.onclick=async()=>{const sourceIds=[...selectedSourceIds];if(!sourceIds.length){status.textContent='Choose at least one duplicate name first.';status.style.color='#e25c70';return}const kept=personDirectory.find(person=>Number(person.id)===Number(primary.value));const merged=personDirectory.filter(person=>sourceIds.includes(Number(person.id))).map(person=>person.name);if(!confirm('Merge '+merged.join(', ')+' into '+kept.name+'?\n\nThose names will become alternate names, their photo links will be combined, and prior review history for the duplicate records will be closed.'))return;merge.disabled=true;cancel.disabled=true;status.style.color='';status.textContent='Merging names and updating confirmed photos…';try{const result=await api('/api/person/merge',{target_person_id:Number(primary.value),source_person_ids:sourceIds});if(result.learning_error){status.textContent='Names merged, but face suggestions need rebuilding: '+result.learning_error;merge.disabled=false;cancel.disabled=false;return}location.href='/?scope=people'}catch(e){status.textContent=e.message;status.style.color='#e25c70';merge.disabled=false;cancel.disabled=false}};actions.append(cancel,merge);box.append(intro,keepLabel,sourceLabel,filter,choices,note,status,actions);renderChoices();openModal('Merge people',box);primary.focus()}
async function openLibraryPanelV2(){
 const body=document.createElement('div');body.className='library-panel';
 const intro=document.createElement('p');intro.textContent='Each folder keeps its own independent index. Choose a previous library, a suggested photo location, or another folder.';
 const choices=document.createElement('div');choices.className='library-choices';
 const row=document.createElement('div');row.className='row';const input=document.createElement('input');input.value=currentLibrary;input.placeholder='Choose a folder';
 const browse=document.createElement('button');browse.className='secondary';browse.textContent='Browse…';row.append(input,browse);
 const status=document.createElement('div');status.className='library-status';const metrics=document.createElement('div');metrics.className='scan-metrics';
 const actions=document.createElement('div');actions.className='publish-actions';const cancel=document.createElement('button');cancel.className='danger';cancel.textContent='Pause scan';cancel.hidden=true;const open=document.createElement('button');open.textContent='Open & index library';actions.append(cancel,open);
 function renderMetrics(job){metrics.replaceChildren(...[['Discovered',job.scanned],['Indexed',job.changed],['Cloud-only',job.placeholders],['Unchanged',job.unchanged],['Removed',job.removed],['Errors',job.errors]].map(([label,value])=>{const box=document.createElement('span');const strong=document.createElement('strong');strong.textContent=Number(value||0).toLocaleString();box.append(strong,document.createTextNode(label));return box}))}
 async function poll(){const response=await fetch('/api/library/status');const job=await response.json();status.textContent=job.message||job.state;renderMetrics(job);if(job.state==='complete'){open.disabled=false;open.textContent='Open library';open.onclick=()=>location.href='/?sort=newest';cancel.hidden=true;return}if(job.state==='cancelled'){open.disabled=false;open.textContent='Resume scan';cancel.hidden=true;return}if(job.state==='error'){open.disabled=false;cancel.hidden=true;return}setTimeout(poll,350)}
 browse.onclick=async()=>{browse.disabled=true;try{const result=await api('/api/library/browse',{});if(result.path)input.value=result.path}catch(e){status.textContent=e.message}finally{browse.disabled=false}};
 open.onclick=async()=>{open.disabled=true;cancel.hidden=false;status.textContent='Starting library index…';try{await api('/api/library/open',{path:input.value});poll()}catch(e){status.textContent=e.message;open.disabled=false;cancel.hidden=true}};
 cancel.onclick=async()=>{cancel.disabled=true;try{await api('/api/library/cancel',{})}catch(e){status.textContent=e.message}};
 body.append(intro,choices,row,actions,status,metrics);openModal('Photo libraries',body);
 try{const response=await fetch('/api/library/options');const data=await response.json();const entries=[...(data.known||[]).map(x=>({...x,group:'Previous'})),...(data.suggestions||[]).map(x=>({...x,group:'Suggested'}))];const seen=new Set();for(const item of entries){if(seen.has(item.path.toLowerCase()))continue;seen.add(item.path.toLowerCase());const button=document.createElement('button');button.className='library-choice';const strong=document.createElement('strong');strong.textContent=item.group+' · '+item.label;const small=document.createElement('small');small.textContent=item.path;button.append(strong,small);button.onclick=()=>input.value=item.path;choices.append(button)}}catch(e){status.textContent=e.message}
}
async function openUpdatePanel(){
 const body=document.createElement('div');body.className='update-panel';const card=document.createElement('section');card.className='update-status-card';const title=document.createElement('strong');title.textContent='Checking for updates…';const message=document.createElement('p');message.textContent='Contacting GitHub for the latest verified release.';card.append(title,message);const location=document.createElement('div');location.className='update-location';const actions=document.createElement('div');actions.className='update-actions';const check=document.createElement('button');check.className='secondary';check.textContent='Check again';const install=document.createElement('button');install.textContent='Download, install & restart';install.hidden=true;const restart=document.createElement('button');restart.textContent='Restart to apply';restart.hidden=true;actions.append(check,restart,install);body.append(card,location,actions);openModal('LensLedger updates',body);
 let restartWatching=false;
 function waitForRestart(){if(restartWatching)return;restartWatching=true;const poll=()=>{fetch('/',{cache:'no-store'}).then(response=>{if(response.ok)window.location.reload();else setTimeout(poll,1500)}).catch(()=>setTimeout(poll,1500))};setTimeout(poll,2000)}
 async function refresh(){try{const status=await fetch('/api/update/status').then(response=>response.json());if(!status.managed_install){const sourceCheckout=status.is_source_checkout;install.hidden=true;if(sourceCheckout&&status.restart_ready){title.textContent='LensLedger '+status.on_disk_version+' is on disk, not running yet';message.textContent='This process is still running '+status.current_version+'. The code in this folder has already moved on since it started — restart to pick it up.';location.textContent='This copy: '+status.current_install_root;restart.hidden=false;restart.disabled=false;return}restart.hidden=true;if(status.state==='checking'){title.textContent='Checking for updates…';message.textContent='Contacting GitHub for the latest verified release.';location.textContent='This copy: '+status.current_install_root;setTimeout(refresh,500);return}title.textContent=status.state==='available'?'LensLedger '+status.release.version+' is available':'Update check';message.textContent=status.state==='available'?(sourceCheckout?"This is a source checkout, so it can't update itself automatically.":"This copy was run directly from an extracted download, so it can't update itself automatically."):(status.message||status.state);location.textContent=sourceCheckout?('This copy: '+status.current_install_root+'. Update it with git pull, or run Install LensLedger.cmd once to create a separate managed copy.'):('This copy: '+status.current_install_root+'. Run Install LensLedger.cmd once from this same folder to move LensLedger into a managed copy that updates itself automatically from then on — your photo library and catalog are unaffected.');return}restart.hidden=true;message.textContent=status.message||status.state;location.textContent='Managed installation: '+status.current_install_root;if(status.state==='checking'){title.textContent='Checking for updates…';install.hidden=true;setTimeout(refresh,500);return}if(status.state==='available'){title.textContent='LensLedger '+status.release.version+' is available';install.hidden=false;install.disabled=false;return}if(status.state==='current'){title.textContent='LensLedger '+status.current_version+' is current';install.hidden=true;return}if(status.state==='restarting'){title.textContent='Installing and restarting…';install.hidden=true;check.disabled=true;waitForRestart();return}title.textContent='Update check needs attention';install.hidden=true}catch(error){title.textContent='Update check needs attention';message.textContent=error.message}}
 check.onclick=async()=>{check.disabled=true;try{await api('/api/update/check',{});title.textContent='Checking for updates…';message.textContent='Contacting GitHub for the latest verified release.';setTimeout(refresh,250)}catch(error){message.textContent=error.message}finally{check.disabled=false}};
 install.onclick=async()=>{if(!confirm('Install the verified update and restart LensLedger now? Your photo library and catalog remain separate and will not be replaced.'))return;install.disabled=true;check.disabled=true;try{const result=await api('/api/update/install',{});title.textContent='Installing and restarting…';message.textContent=result.message;location.textContent='Keep this tab open — reconnecting automatically once the new version is ready…';waitForRestart()}catch(error){message.textContent=error.message;install.disabled=false;check.disabled=false}};restart.onclick=async()=>{restart.disabled=true;check.disabled=true;try{const result=await api('/api/update/restart-source',{});title.textContent='Restarting…';message.textContent=result.message;location.textContent='Keep this tab open — reconnecting automatically once LensLedger is back…';waitForRestart()}catch(error){message.textContent=error.message;restart.disabled=false;check.disabled=false}};refresh()
}
async function watchUpdateBadge(){try{const status=await fetch('/api/update/status').then(response=>response.json());if(status.state==='available'){$('updateMenu').textContent='⬆ Update available · v'+status.release.version;$('updateMenu').style.color='#79dc92';return}if(status.state==='checking')setTimeout(watchUpdateBadge,700)}catch(error){}}setTimeout(watchUpdateBadge,100);
function metadataText(value){if(Array.isArray(value))return value.join(', ');if(value&&typeof value==='object')return JSON.stringify(value);return value==null?'':String(value)}
async function previewPublish(){if(!currentDetail?.publishable)return;try{const preview=await api('/api/publish/preview',{id:selectedId,description:$('publishDescription').value});const box=document.createElement('div');const intro=document.createElement('p');intro.textContent='Review every destination below. Nothing has been written yet.';const table=document.createElement('table');table.className='preview-table';const head=document.createElement('tr');['File field','Before','After'].forEach(label=>{const th=document.createElement('th');th.textContent=label;head.append(th)});table.append(head);const keys=[...new Set([...Object.keys(preview.before),...Object.keys(preview.after)])];keys.forEach(key=>{const row=document.createElement('tr');[key,metadataText(preview.before[key]),metadataText(preview.after[key])].forEach(value=>{const cell=document.createElement('td');cell.textContent=value||'—';row.append(cell)});table.append(row)});const bar=document.createElement('div');bar.className='confirm-bar';const note=document.createElement('small');note.textContent='A full safety copy is created before writing, and decoded picture pixels must match afterward.';const buttons=document.createElement('div');buttons.className='confirm-buttons';const cancelButton=document.createElement('button');cancelButton.className='secondary';cancelButton.textContent='Cancel';cancelButton.onclick=()=>$('modalBackdrop').classList.remove('open');const confirmButton=document.createElement('button');confirmButton.textContent='Publish this photo';confirmButton.onclick=async()=>{confirmButton.disabled=true;cancelButton.disabled=true;try{const result=await api('/api/publish',{id:selectedId,description:$('publishDescription').value,expected_after:preview.after});$('modalBackdrop').classList.remove('open');await selectAsset(selectedId);setStatus(result.message)}catch(e){confirmButton.disabled=false;cancelButton.disabled=false;setStatus(e.message,true)}};buttons.append(cancelButton,confirmButton);bar.append(note,buttons);box.append(intro,table,bar);openModal('Publish metadata to '+currentDetail.filename,box);document.querySelector('.modal').classList.add('publish-modal')}catch(e){setStatus(e.message,true)}}
async function restorePublished(){if(!currentDetail?.can_restore_publish||!confirm('Restore “'+currentDetail.filename+'” from the safety backup made before its last publish?'))return;try{const result=await api('/api/publish/restore',{id:selectedId});await selectAsset(selectedId);setStatus(result.message)}catch(e){setStatus(e.message,true)}}
async function openTrashPanel(){const body=document.createElement('div');body.className='trash-list';openModal('Trash & restore',body);try{const r=await fetch('/api/trash');const data=await r.json();if(!data.items.length){const empty=document.createElement('div');empty.className='trash-empty';empty.textContent='Trash is empty.';body.append(empty);return}const emptyBar=document.createElement('div');emptyBar.style.cssText='display:flex;justify-content:flex-end;margin-bottom:8px';const emptyBtn=document.createElement('button');emptyBtn.className='danger';emptyBtn.textContent='Empty trash';emptyBtn.onclick=async()=>{if(!confirm('Permanently delete all '+data.items.length+' trashed items? This cannot be undone.'))return;emptyBtn.disabled=true;await api('/api/review-bin/empty',{});location.reload()};emptyBar.append(emptyBtn);body.before(emptyBar);data.items.forEach(item=>{const row=document.createElement('div');row.className='trash-item';const meta=document.createElement('div');meta.className='trash-meta';const strong=document.createElement('strong');strong.textContent=item.name;const small=document.createElement('small');small.textContent=item.path;meta.append(strong,small);const restore=document.createElement('button');restore.textContent='Restore';restore.onclick=async()=>{await api('/api/review-bin/restore',{review_id:item.id});location.reload()};const del=document.createElement('button');del.className='danger';del.textContent='Delete';del.onclick=async()=>{if(!confirm('Permanently delete "'+item.name+'"? This cannot be undone.'))return;del.disabled=true;restore.disabled=true;await api('/api/review-bin/delete',{review_id:item.id});row.remove();if(!body.querySelector('.trash-item')){const empty=document.createElement('div');empty.className='trash-empty';empty.textContent='Trash is empty.';body.append(empty)}};row.append(meta,restore,del);body.append(row)})}catch(e){body.textContent=e.message}}
async function openMenuPanel(name){$('menuPanel').classList.remove('open');if(name==='library')return openLibraryPanelV2();if(name==='update')return openUpdatePanel();if(name==='guide')return openModal('Quick guide',textPanel(['Use Primary subject for the main thing in one photo, Photo tags for other visible things, People for confirmed identities, and Event / folder tags for context shared by the whole batch. Search uses Everything by default so all four contribute to normal results.','Face matches are suggestions until you confirm them. Open Information already in this photo to see readable EXIF, IPTC, and XMP details already stored in the file.']));if(name==='about'){const box=document.createElement('div');box.innerHTML='<p><strong>LensLedger v'+LL.appVersion+'</strong> — '+LL.appTagline+'</p><p>Current photo library: <code>'+currentLibrary+'</code></p><p>Local-first photo and video indexing, search, people review, and safe metadata publishing for Windows. Your photos stay on your machine — nothing is uploaded, no cloud required.</p><p>Each library has a separate index. Approved People-review decisions publish immediately; other edits use Preview &amp; Publish. Every write creates a safety backup and verifies the picture pixels afterward.</p><hr style="border:none;border-top:1px solid var(--border);margin:14px 0"><p><strong>Other projects by the developer</strong></p><ul style="padding-left:18px;line-height:1.7"><li><a href="https://github.com/WeirDave/WaxFrame-Professional" target="_blank" rel="noopener" style="color:var(--accent)">WaxFrame Professional</a> — Multi-AI document refinement in your browser. Your hive of AIs reviews and rewrites in parallel; you resolve disagreements your way.</li><li><a href="https://github.com/WeirDave/WaxFrame-Free" target="_blank" rel="noopener" style="color:var(--accent)">WaxFrame Free</a> — Standalone browser-based free version. No install required — runs directly via GitHub Pages.</li><li><a href="https://github.com/WeirDave/Subscription-Wizard" target="_blank" rel="noopener" style="color:var(--accent)">Subscription Wizard</a> — Firefox extension to compare, migrate, and manage Amazon Subscribe &amp; Save subscriptions across accounts.</li><li><a href="https://github.com/WeirDave/WD-Wireless-Tools" target="_blank" rel="noopener" style="color:var(--accent)">WD Wireless Tools</a> — A suite of Ekahau workflow tools for wireless network professionals.</li></ul>';return openModal('About LensLedger',box)};if(name==='trash')return openTrashPanel()}
const batchSelected=new Set();let lastClickedThumbIndex=null;
function updateBatchBar(){
  const count=batchSelected.size;
  $('batchBar').classList.toggle('visible',count>0);
  $('batchCount').textContent=count+' selected';
}
function toggleBatchSelect(id,thumb){
  if(batchSelected.has(id)){batchSelected.delete(id);thumb.classList.remove('selected')}
  else{batchSelected.add(id);thumb.classList.add('selected')}
  updateBatchBar();
}
function clearBatchSelection(){
  batchSelected.clear();
  document.querySelectorAll('.thumb.selected').forEach(t=>t.classList.remove('selected'));
  updateBatchBar();
}
async function batchAddTags(){
  const tag=$('batchTagInput').value.trim();
  if(!tag){setStatus('Enter tags first',true);return}
  if(!batchSelected.size)return;
  $('batchAddTags').disabled=true;
  try{
    const result=await api('/api/tag/add-batch',{ids:[...batchSelected],tag});
    $('batchTagInput').value='';
    setStatus(result.tags_applied+' tag(s) added to '+result.photos_tagged+' photo(s)');
    if(batchSelected.has(selectedId))await selectAsset(selectedId);
  }catch(e){setStatus(e.message,true)}finally{$('batchAddTags').disabled=false}
}
async function batchTrash(){
  if(!batchSelected.size)return;
  if(!confirm('Move '+batchSelected.size+' selected photo(s) to Trash?\n\nThey will leave the photo library and disappear from search. You can restore them from ☰ → Trash & restore.'))return;
  $('batchTrash').disabled=true;
  try{
    const ids=[...batchSelected];
    const result=await api('/api/review-bin/batch',{ids});
    for(const id of ids){
      const idx=items.findIndex(x=>Number(x.id)===Number(id));
      if(idx>=0)items.splice(idx,1);
      document.querySelector('.thumb[data-id="'+id+'"]')?.remove();
    }
    clearBatchSelection();
    setStatus(result.moved+' photo(s) moved to Trash');
    if(ids.includes(selectedId)){
      if(items.length)selectAsset(items[0].id);else location.reload();
    }
  }catch(e){setStatus(e.message,true)}finally{$('batchTrash').disabled=false}
}
function buildThumb(item){const b=document.createElement('button');b.className='thumb';b.dataset.id=item.id;b.title=item.filename;b.onclick=e=>{
  if(e.ctrlKey||e.metaKey){
    e.preventDefault();toggleBatchSelect(Number(item.id),b);lastClickedThumbIndex=items.findIndex(x=>Number(x.id)===Number(item.id));return;
  }
  if(e.shiftKey&&lastClickedThumbIndex!=null){
    e.preventDefault();
    const currentIdx=items.findIndex(x=>Number(x.id)===Number(item.id));
    const from=Math.min(lastClickedThumbIndex,currentIdx),to=Math.max(lastClickedThumbIndex,currentIdx);
    for(let i=from;i<=to;i++){
      const thumbId=Number(items[i].id);
      batchSelected.add(thumbId);
      document.querySelector('.thumb[data-id="'+thumbId+'"]')?.classList.add('selected');
    }
    updateBatchBar();return;
  }
  clearBatchSelection();lastClickedThumbIndex=items.findIndex(x=>Number(x.id)===Number(item.id));selectAsset(item.id);
};if(item.media_type==='image'){const img=document.createElement('img');img.loading='lazy';img.src='/media?id='+item.id;b.append(img)}else{const v=document.createElement('div');v.className='video-label';v.textContent=item.media_type==='raw'?'RAW':'▶ VIDEO';b.append(v)}const s=document.createElement('span');s.textContent=(item.capture_date||'')+'  '+item.filename;b.append(s);return b}
let filmstripPage=LL.page||1;let filmstripHasMore=!!LL.hasMore;let filmstripLoading=false;let filmstripObserved=null;
let filmstripObserver=null;
function pageParams(page){const q=LL.query||{};const params=new URLSearchParams();if(q.q)params.set('q',q.q);if(q.date)params.set('date',q.date);if(q.scope)params.set('scope',q.scope);if(q.sort)params.set('sort',q.sort);if(q.person)params.set('person',q.person);if(q.near)params.set('near',q.near);params.set('page',page);return params}
function observeFilmstripEnd(){const f=$('filmstrip');if(filmstripObserved){filmstripObserver.unobserve(filmstripObserved);filmstripObserved=null}if(!filmstripHasMore)return;const thumbs=f.querySelectorAll('.thumb');const last=thumbs[thumbs.length-1];if(last){filmstripObserved=last;filmstripObserver.observe(last)}}
async function loadMoreFilmstripItems(){if(filmstripLoading||!filmstripHasMore)return;filmstripLoading=true;try{const response=await fetch('/api/library/items?'+pageParams(filmstripPage+1));const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not load more photos');filmstripPage+=1;filmstripHasMore=!!data.has_more;const f=$('filmstrip');data.items.forEach(item=>{items.push(item);f.append(buildThumb(item))});observeFilmstripEnd()}catch(e){setStatus(e.message,true)}finally{filmstripLoading=false}}
function renderFilmstrip(){const f=$('filmstrip');if(!items.length){f.replaceChildren(Object.assign(document.createElement('div'),{className:'filmstrip-empty',textContent:'No photos match this search.'}));return}f.replaceChildren(...items.map(buildThumb));filmstripObserver=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting))loadMoreFilmstripItems()},{root:f,rootMargin:'0px 400px 0px 0px'});observeFilmstripEnd()}
function enableFilmstripDrag(){const f=$('filmstrip');let active=false,startX=0,startScroll=0,moved=false,suppressClick=false,pointerId=null;f.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('a'))return;active=true;moved=false;pointerId=e.pointerId;startX=e.clientX;startScroll=f.scrollLeft});f.addEventListener('pointermove',e=>{if(!active||e.pointerId!==pointerId)return;const delta=e.clientX-startX;if(!moved&&Math.abs(delta)>5){moved=true;f.classList.add('dragging');f.setPointerCapture(pointerId)}if(moved){f.scrollLeft=startScroll-delta;e.preventDefault()}});f.addEventListener('pointerup',e=>{if(!active||e.pointerId!==pointerId)return;suppressClick=moved;active=false;f.classList.remove('dragging');if(f.hasPointerCapture(pointerId))f.releasePointerCapture(pointerId);pointerId=null;if(suppressClick)setTimeout(()=>suppressClick=false,0)});f.addEventListener('pointercancel',()=>{active=false;moved=false;suppressClick=false;f.classList.remove('dragging');pointerId=null});f.addEventListener('click',e=>{if(!suppressClick)return;suppressClick=false;e.preventDefault();e.stopPropagation()},true)}
function changeDay(delta){const input=$('datePicker');let d=input.value?new Date(input.value+'T12:00:00'):new Date();d.setDate(d.getDate()+delta);input.value=d.toISOString().slice(0,10);input.form.submit()}
function submitOnEnter(event,action){if(event.key==='Enter'&&!event.isComposing){event.preventDefault();action()}}

// Hand-rolled calendar dropdown instead of the native <input type=date> picker.
// Every browser (and OS locale) renders that control's popup differently --
// this one is plain DOM/CSS we fully control, so it looks and behaves
// identically everywhere. #datePicker stays a real hidden form field so
// changeDay() and the scope-change date-clear above keep working untouched.
const MONTH_NAMES=['January','February','March','April','May','June','July','August','September','October','November','December'];
const MONTH_ABBR=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
let calViewYear=null,calViewMonth=null;
function pad2(n){return String(n).padStart(2,'0')}
function parseDateValue(value){if(!value)return null;const[y,m,d]=value.split('-').map(Number);return{y,m,d}}
function formatTriggerLabel(value){const parsed=parseDateValue(value);return parsed?MONTH_ABBR[parsed.m-1]+' '+parsed.d+', '+parsed.y:'Any date'}
function syncDateTrigger(){$('dateTrigger').textContent=formatTriggerLabel($('datePicker').value)}
function populateCalendarSelects(){const monthSelect=$('calMonth');if(!monthSelect.options.length)monthSelect.replaceChildren(...MONTH_NAMES.map((name,i)=>Object.assign(document.createElement('option'),{value:i,textContent:name})));const yearSelect=$('calYear');const thisYear=new Date().getFullYear();const years=[];for(let y=thisYear+1;y>=1900;y--)years.push(y);yearSelect.replaceChildren(...years.map(y=>Object.assign(document.createElement('option'),{value:y,textContent:y})))}
function renderCalendarDays(){const grid=$('calDays');grid.replaceChildren();const selected=parseDateValue($('datePicker').value);const firstWeekday=new Date(calViewYear,calViewMonth,1).getDay();const daysInMonth=new Date(calViewYear,calViewMonth+1,0).getDate();const today=new Date();const cells=[];for(let i=0;i<firstWeekday;i++)cells.push(document.createElement('span'));for(let day=1;day<=daysInMonth;day++){const cell=document.createElement('button');cell.type='button';cell.className='cal-day';cell.textContent=day;if(selected&&selected.y===calViewYear&&selected.m===calViewMonth+1&&selected.d===day)cell.classList.add('selected');if(today.getFullYear()===calViewYear&&today.getMonth()===calViewMonth&&today.getDate()===day)cell.classList.add('today');cell.onclick=()=>chooseDate(calViewYear,calViewMonth+1,day);cells.push(cell)}grid.append(...cells)}
function renderCalendar(){$('calMonth').value=calViewMonth;$('calYear').value=calViewYear;renderCalendarDays()}
function openCalendar(){const now=new Date();const current=parseDateValue($('datePicker').value)||{y:now.getFullYear(),m:now.getMonth()+1};calViewYear=current.y;calViewMonth=current.m-1;populateCalendarSelects();renderCalendar();$('datePopover').classList.add('open')}
function chooseDate(y,m,d){$('datePicker').value=y+'-'+pad2(m)+'-'+pad2(d);syncDateTrigger();$('datePopover').classList.remove('open');$('datePicker').form.submit()}
function shiftCalendarMonth(delta){calViewMonth+=delta;if(calViewMonth<0){calViewMonth=11;calViewYear-=1}else if(calViewMonth>11){calViewMonth=0;calViewYear+=1}renderCalendar()}
$('reviewPeopleGallery')?.addEventListener('click',()=>location.href='/people-review');
$('mergePeopleGallery')?.addEventListener('click',openPersonMerge);$('previousPhoto').onclick=()=>step(-1);$('nextPhoto').onclick=()=>step(1);$('previousDay').onclick=()=>changeDay(-1);$('nextDay').onclick=()=>changeDay(1);$('dateTrigger').onclick=e=>{e.stopPropagation();if($('datePopover').classList.contains('open')){$('datePopover').classList.remove('open')}else{openCalendar()}};$('calPrevMonth').onclick=()=>shiftCalendarMonth(-1);$('calNextMonth').onclick=()=>shiftCalendarMonth(1);$('calMonth').onchange=e=>{calViewMonth=Number(e.target.value);renderCalendarDays()};$('calYear').onchange=e=>{calViewYear=Number(e.target.value);renderCalendarDays()};$('calToday').onclick=()=>{const t=new Date();chooseDate(t.getFullYear(),t.getMonth()+1,t.getDate())};$('calClear').onclick=()=>{$('datePicker').value='';syncDateTrigger();$('datePopover').classList.remove('open');$('datePicker').form.submit()};syncDateTrigger();$('saveSubject').onclick=saveSubject;$('subjectInput').onkeydown=e=>submitOnEnter(e,saveSubject);$('addTag').onclick=addTag;$('newTag').onkeydown=e=>submitOnEnter(e,addTag);personPicker=createPersonPicker({container:$('personPickerContainer'),getNames:()=>sidebarKnownPeople,placeholder:'Person’s name',onChoose:addPerson});$('addContextTag').onclick=addContextTag;$('newContextTag').onkeydown=e=>submitOnEnter(e,addContextTag);$('previewPublish').onclick=previewPublish;$('restorePublish').onclick=restorePublished;$('moveToTrash').onclick=moveToBin;$('sidebarToggle').onclick=()=>{$('sidebar').classList.toggle('open');$('sidebarBackdrop').classList.toggle('open')};$('sidebarBackdrop').onclick=()=>{$('sidebar').classList.remove('open');$('sidebarBackdrop').classList.remove('open')};$('batchAddTags').onclick=batchAddTags;$('batchTagInput').onkeydown=e=>submitOnEnter(e,batchAddTags);$('batchTrash').onclick=batchTrash;$('batchClear').onclick=clearBatchSelection;$('scopePicker').onchange=e=>{if(e.target.value==='people')e.target.form.querySelector('[name=q]').value='';if(['people','semantic'].includes(e.target.value)){e.target.form.querySelector('[name=date]').value='';syncDateTrigger()}e.target.form.submit()};document.querySelectorAll('.edit-aliases').forEach(button=>button.onclick=()=>editAliases(button));$('menuToggle').onclick=e=>{e.stopPropagation();closeHelp();$('menuPanel').classList.toggle('open')};document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>openMenuPanel(b.dataset.panel));document.querySelectorAll('[data-help]').forEach(b=>b.onclick=e=>{e.stopPropagation();const target=$(b.dataset.help);const opening=!target.classList.contains('open');closeHelp();if(opening)target.classList.add('open')});$('modalClose').onclick=()=>$('modalBackdrop').classList.remove('open');$('modalBackdrop').onclick=e=>{if(e.target===$('modalBackdrop'))$('modalBackdrop').classList.remove('open')};document.addEventListener('click',e=>{if(!e.target.closest('.menu-panel')&&!e.target.closest('.menu-toggle'))$('menuPanel').classList.remove('open');if(!e.target.closest('.help-popover')&&!e.target.closest('.info-button'))closeHelp();if(!e.target.closest('.date-field'))$('datePopover').classList.remove('open')});document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('menuPanel').classList.remove('open');$('modalBackdrop').classList.remove('open');$('datePopover').classList.remove('open');closeHelp();if(batchSelected.size)clearBatchSelection();$('sidebar').classList.remove('open');$('sidebarBackdrop').classList.remove('open')}if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='ArrowLeft')step(-1);if(e.key==='ArrowRight')step(1)});renderFilmstrip();enableFilmstripDrag();if(selectedId)selectAsset(selectedId);else{document.querySelectorAll('.sidebar input,.sidebar textarea,.sidebar button').forEach(control=>control.disabled=true);$('moveToTrash').disabled=true;updateNav()}