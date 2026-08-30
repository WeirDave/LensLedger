(function(){
var boot=JSON.parse(document.body.dataset.ll);
var csrf=boot.csrf;
var people=[];
var groups=[];
var selectedGroupName=null;
var selectedPeople=new Set();
var searchQuery='';
var toastTimer=null;

function $(id){return document.getElementById(id)}
async function api(path,payload){var r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({},payload,{csrf:csrf}))});if(r.status===403){location.reload();return}var data=await r.json();if(!r.ok)throw new Error(data.error||'Request failed');return data}

function toast(msg){
  var el=$('toast');
  el.textContent=msg;
  el.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer=setTimeout(function(){el.classList.remove('visible')},2500);
}

async function loadData(){
  var gr=await fetch('/api/groups').then(function(r){return r.json()});
  groups=gr.groups||[];
  var pr=await fetch('/api/people/all-with-groups').then(function(r){return r.json()});
  people=pr.people||[];
  people.sort(function(a,b){return a.name.localeCompare(b.name)});
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
    var icon=document.createElement('div');
    icon.className='gm-group-icon';
    icon.textContent='📢';
    var nameSpan=document.createElement('span');
    nameSpan.className='gm-group-name';
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
      if(!confirm('Delete the group "'+g.name+'"? People in this group won\'t be deleted, just the group itself.'))return;
      api('/api/group/delete',{group_id:g.id}).then(function(){
        if(selectedGroupName===g.name)selectedGroupName=null;
        return loadData();
      }).then(function(){toast('Deleted "'+g.name+'"')}).catch(function(err){setStatus(err.message,true)});
    };
    item.append(icon,nameSpan,countSpan,del);
    item.onclick=function(){
      selectedGroupName=selectedGroupName===g.name?null:g.name;
      render();
    };
    list.append(item);
  });
  if(!groups.length){
    var empty=document.createElement('div');
    empty.className='gm-empty';
    empty.innerHTML='<div class="gm-empty-icon">📢</div><div class="gm-empty-text">No groups yet.<br>Create one above to get started.</div>';
    list.append(empty);
  }
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

function renderAlphaBar(filtered){
  var bar=$('alphaBar');
  if(!bar)return;
  bar.innerHTML='';
  var present=new Set();
  filtered.forEach(function(p){
    var ch=(p.name[0]||'?').toUpperCase();
    if(ch>='A'&&ch<='Z')present.add(ch);else present.add('#');
  });
  'ABCDEFGHIJKLMNOPQRSTUVWXYZ#'.split('').forEach(function(ch){
    var a=document.createElement('a');
    a.className='gm-alpha-link'+(present.has(ch)?'':' disabled');
    a.textContent=ch;
    a.href='#';
    a.onclick=function(e){
      e.preventDefault();
      if(!present.has(ch))return;
      var target=document.getElementById('letter-'+ch);
      if(target)target.scrollIntoView({behavior:'smooth',block:'start'});
    };
    bar.append(a);
  });
}

function renderPeople(){
  var grid=$('peopleGrid');
  grid.innerHTML='';
  var filtered=getFilteredPeople();
  renderAlphaBar(filtered);
  if(!filtered.length){
    var empty=document.createElement('div');
    empty.className='gm-empty';
    empty.innerHTML='<div class="gm-empty-icon">🔍</div><div class="gm-empty-text">'+(people.length?'No people match your search.':'No people in the library yet.')+'</div>';
    grid.append(empty);
    return;
  }
  var lastLetter='';
  filtered.forEach(function(p){
    var ch=(p.name[0]||'?').toUpperCase();
    if(ch<'A'||ch>'Z')ch='#';
    if(ch!==lastLetter){
      lastLetter=ch;
      var header=document.createElement('div');
      header.className='gm-letter-header';
      header.id='letter-'+ch;
      var charSpan=document.createElement('span');
      charSpan.className='gm-letter-char';
      charSpan.textContent=ch;
      var strip=document.createElement('img');
      strip.className='gm-film-strip';
      strip.src='/web/img/filmstrip.png?v='+boot.appVersion;
      strip.alt='';
      strip.setAttribute('aria-hidden','true');
      header.append(charSpan,strip);
      grid.append(header);
    }
    var card=document.createElement('label');
    card.className='gm-person-card'+(selectedPeople.has(p.id)?' checked':'');
    var check=document.createElement('input');
    check.type='checkbox';
    check.checked=selectedPeople.has(p.id);
    check.onchange=function(){
      if(check.checked){selectedPeople.add(p.id);card.classList.add('checked')}
      else{selectedPeople.delete(p.id);card.classList.remove('checked')}
      updateActions();
    };
    var avatar=document.createElement('div');
    avatar.className='gm-person-avatar';
    if(p.representative_id){
      var img=document.createElement('img');
      img.src='/media?id='+p.representative_id;
      img.alt=p.name;
      img.loading='lazy';
      avatar.append(img);
    }else{
      avatar.textContent='👤';
    }
    var info=document.createElement('div');
    info.className='gm-person-info';
    var nameEl=document.createElement('div');
    nameEl.className='gm-person-name';
    nameEl.textContent=p.name;
    info.append(nameEl);
    var meta=document.createElement('div');
    meta.className='gm-person-meta';
    meta.textContent=p.confirmed_count+' photo'+(p.confirmed_count!==1?'s':'');
    if(p.aliases&&p.aliases.length){
      meta.textContent+=' · aka '+p.aliases.join(', ');
    }
    info.append(meta);
    if(p.groups&&p.groups.length){
      var badges=document.createElement('div');
      badges.className='gm-person-badges';
      p.groups.forEach(function(gn){
        var badge=document.createElement('span');
        badge.className='group-badge'+(selectedGroupName&&gn===selectedGroupName?' in-selected':'');
        badge.textContent=gn;
        badges.append(badge);
      });
      info.append(badges);
    }
    var indicator=document.createElement('div');
    indicator.className='gm-check-indicator';
    indicator.textContent='✓';
    card.append(check,avatar,info,indicator);
    grid.append(card);
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
    toast('Group "'+name+'" created');
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
    toast('Added '+r.added+' of '+ids.length+' to '+selectedGroupName);
    return loadData();
  }).catch(function(err){setStatus(err.message,true)}).finally(function(){btn.disabled=false});
};

$('removeFromGroup').onclick=function(){
  if(!selectedGroupName||!selectedPeople.size)return;
  var ids=Array.from(selectedPeople);
  var btn=this;
  btn.disabled=true;
  api('/api/groups/bulk-remove',{group_name:selectedGroupName,person_ids:ids}).then(function(r){
    toast('Removed '+r.removed+' of '+ids.length+' from '+selectedGroupName);
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
