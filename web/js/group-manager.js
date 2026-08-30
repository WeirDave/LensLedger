(function(){
var boot=JSON.parse(document.body.dataset.ll);
var csrf=boot.csrf;
var people=[];
var groups=[];
var selectedGroupName=null;
var selectedPeople=new Set();
var searchQuery='';

function $(id){return document.getElementById(id)}
async function api(path,payload){var r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({},payload,{csrf:csrf}))});if(r.status===403){location.reload();return}var data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');return data}

async function loadData(){
  var gr=await fetch('/api/groups').then(function(r){return r.json()});
  groups=gr.groups||[];
  var pr=await fetch('/api/people/all-with-groups').then(function(r){return r.json()});
  people=pr.people||[];
  selectedPeople.clear();
  render();
}

function render(){
  renderGroups();
  renderPeople();
  updateActions();
}

function renderGroups(){
  var list=$('groupList');
  list.innerHTML='';
  groups.forEach(function(g){
    var item=document.createElement('div');
    item.className='gm-group-item'+(selectedGroupName===g.name?' selected':'');
    var nameSpan=document.createElement('span');
    nameSpan.textContent=g.name;
    var countSpan=document.createElement('span');
    countSpan.className='gm-count';
    countSpan.textContent=g.member_count;
    var del=document.createElement('button');
    del.className='gm-delete-group';
    del.textContent='×';
    del.title='Delete group';
    del.onclick=function(e){
      e.stopPropagation();
      if(!confirm('Delete the group "'+g.name+'"? People in this group won’t be deleted, just the group itself.'))return;
      api('/api/group/delete',{group_id:g.id}).then(loadData).catch(function(err){setStatus(err.message,true)});
    };
    item.append(nameSpan,countSpan,del);
    item.onclick=function(){
      selectedGroupName=selectedGroupName===g.name?null:g.name;
      render();
    };
    list.append(item);
  });
}

function getFilteredPeople(){
  var q=searchQuery.toLowerCase();
  return people.filter(function(p){
    if(!q)return true;
    if(p.name.toLowerCase().indexOf(q)!==-1)return true;
    if(p.aliases&&p.aliases.some(function(a){return a.toLowerCase().indexOf(q)!==-1}))return true;
    if(p.groups&&p.groups.some(function(g){return g.toLowerCase().indexOf(q)!==-1}))return true;
    return false;
  });
}

function renderPeople(){
  var list=$('peopleList');
  list.innerHTML='';
  var filtered=getFilteredPeople();
  if(!filtered.length){
    var empty=document.createElement('div');
    empty.className='gm-empty';
    empty.textContent=people.length?'No people match that search.':'No people in the library yet.';
    list.append(empty);
    return;
  }
  filtered.forEach(function(p){
    var row=document.createElement('label');
    row.className='gm-person-row';
    var check=document.createElement('input');
    check.type='checkbox';
    check.checked=selectedPeople.has(p.id);
    check.onchange=function(){
      if(check.checked)selectedPeople.add(p.id);
      else selectedPeople.delete(p.id);
      updateActions();
    };
    var nameDiv=document.createElement('div');
    nameDiv.className='gm-person-name';
    var strong=document.createElement('strong');
    strong.textContent=p.name;
    nameDiv.append(strong);
    if(p.aliases&&p.aliases.length){
      var aliasSpan=document.createElement('small');
      aliasSpan.textContent=' — '+p.aliases.join(', ');
      aliasSpan.className='gm-alias-text';
      nameDiv.append(aliasSpan);
    }
    var badges=document.createElement('div');
    badges.className='gm-person-badges';
    (p.groups||[]).forEach(function(gn){
      var badge=document.createElement('span');
      badge.className='group-badge'+(selectedGroupName&&gn===selectedGroupName?' in-selected':'');
      badge.textContent=gn;
      badges.append(badge);
    });
    var countSpan=document.createElement('span');
    countSpan.className='gm-person-count';
    countSpan.textContent=p.confirmed_count+' photo'+(p.confirmed_count!==1?'s':'');
    row.append(check,nameDiv,badges,countSpan);
    list.append(row);
  });
  updateSelectAll();
}

function updateSelectAll(){
  var filtered=getFilteredPeople();
  var all=filtered.length>0&&filtered.every(function(p){return selectedPeople.has(p.id)});
  $('selectAll').checked=all;
  $('selectAll').indeterminate=!all&&filtered.some(function(p){return selectedPeople.has(p.id)});
}

function updateActions(){
  var count=selectedPeople.size;
  $('selectionCount').textContent=count?count+' selected':'';
  $('addToGroup').disabled=!count||!selectedGroupName;
  $('removeFromGroup').disabled=!count||!selectedGroupName;
  $('addToGroup').textContent=selectedGroupName?'Add to '+selectedGroupName:'Add to group';
  $('removeFromGroup').textContent=selectedGroupName?'Remove from '+selectedGroupName:'Remove from group';
  updateSelectAll();
}

function setStatus(msg,isError){
  var el=$('statusMsg');
  el.textContent=msg;
  el.className='gm-status'+(isError?' error':'');
  if(msg&&!isError)setTimeout(function(){if(el.textContent===msg)el.textContent=''},3000);
}

$('newGroupBtn').onclick=function(){
  var input=$('newGroupInput');
  var name=input.value.trim();
  if(!name)return;
  api('/api/groups/bulk-assign',{group_name:name,person_ids:[]}).then(function(){
    input.value='';
    return loadData();
  }).then(function(){
    selectedGroupName=name;
    render();
    setStatus('Group "'+name+'" created.');
  }).catch(function(err){setStatus(err.message,true)});
};
$('newGroupInput').onkeydown=function(e){if(e.key==='Enter')$('newGroupBtn').click()};

$('searchInput').oninput=function(){
  searchQuery=this.value.trim();
  renderPeople();
};

$('selectAll').onchange=function(){
  var filtered=getFilteredPeople();
  if(this.checked)filtered.forEach(function(p){selectedPeople.add(p.id)});
  else filtered.forEach(function(p){selectedPeople.delete(p.id)});
  renderPeople();
  updateActions();
};

$('addToGroup').onclick=function(){
  if(!selectedGroupName||!selectedPeople.size)return;
  var ids=Array.from(selectedPeople);
  var btn=this;
  btn.disabled=true;
  api('/api/groups/bulk-assign',{group_name:selectedGroupName,person_ids:ids}).then(function(r){
    setStatus('Added '+r.added+' of '+ids.length+' people to '+selectedGroupName+'.');
    return loadData();
  }).catch(function(err){setStatus(err.message,true)}).finally(function(){btn.disabled=false});
};

$('removeFromGroup').onclick=function(){
  if(!selectedGroupName||!selectedPeople.size)return;
  var ids=Array.from(selectedPeople);
  var btn=this;
  btn.disabled=true;
  api('/api/groups/bulk-remove',{group_name:selectedGroupName,person_ids:ids}).then(function(r){
    setStatus('Removed '+r.removed+' of '+ids.length+' people from '+selectedGroupName+'.');
    return loadData();
  }).catch(function(err){setStatus(err.message,true)}).finally(function(){btn.disabled=false});
};

function openMenu(){$('menuPanel').classList.add('open');$('menuBackdrop').classList.add('open')}
function closeMenu(){$('menuPanel').classList.remove('open');$('menuBackdrop').classList.remove('open')}
$('menuToggle').onclick=function(e){e.stopPropagation();if($('menuPanel').classList.contains('open'))closeMenu();else openMenu()};
$('menuClose').onclick=closeMenu;
$('menuBackdrop').onclick=closeMenu;
document.querySelectorAll('[data-panel]').forEach(function(b){b.onclick=function(){
  var panel=b.dataset.panel;
  if(panel==='about'){$('aboutOverlay').classList.add('open');$('aboutClose').onclick=function(){$('aboutOverlay').classList.remove('open')}}
}});
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeMenu()});

loadData();
})();
