/* Globe map — Three.js sphere with SVG texture and HTML marker overlays */
var scene, camera, renderer, globe, atmosphere, animId;
var rotX = 0.52, rotY = 0.7, targetRotX = 0.52, targetRotY = 0.7;
var camDist = 3.2, targetDist = 3.2;
var MIN_DIST = 1.6, MAX_DIST = 6;
var drag = null, rawPoints = [], selectedCluster = null, selectedMarker = null;
var viewport = document.getElementById('viewport');
var markerLayer = document.getElementById('markerLayer');
var details = document.getElementById('details');
var GLOBE_RADIUS = 1;

function initScene() {
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(45, viewport.clientWidth / viewport.clientHeight, 0.1, 100);
  camera.position.set(0, 0, camDist);

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(viewport.clientWidth, viewport.clientHeight);
  renderer.setClearColor(0x000000, 0);
  viewport.insertBefore(renderer.domElement, viewport.firstChild);
  renderer.domElement.style.position = 'absolute';
  renderer.domElement.style.inset = '0';

  var geo = new THREE.SphereGeometry(GLOBE_RADIUS, 96, 64);
  var mat = new THREE.MeshBasicMaterial({ color: 0x111111 });
  globe = new THREE.Mesh(geo, mat);
  scene.add(globe);

  var atmosGeo = new THREE.SphereGeometry(GLOBE_RADIUS * 1.015, 64, 48);
  var atmosMat = new THREE.ShaderMaterial({
    vertexShader: [
      'varying vec3 vNormal;',
      'void main(){',
      '  vNormal = normalize(normalMatrix * normal);',
      '  gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0);',
      '}'
    ].join('\n'),
    fragmentShader: [
      'varying vec3 vNormal;',
      'void main(){',
      '  float intensity = pow(0.65 - dot(vNormal, vec3(0,0,1.0)), 2.0);',
      '  gl_FragColor = vec4(0.3, 0.5, 0.8, intensity * 0.4);',
      '}'
    ].join('\n'),
    side: THREE.BackSide,
    transparent: true,
    depthWrite: false,
  });
  atmosphere = new THREE.Mesh(atmosGeo, atmosMat);
  scene.add(atmosphere);

  loadTexture();
}

function loadTexture() {
  var img = new Image();
  img.onload = function () {
    var can = document.createElement('canvas');
    can.width = 2048;
    can.height = 1024;
    var ctx = can.getContext('2d');
    ctx.drawImage(img, 0, 0, can.width, can.height);
    var tex = new THREE.CanvasTexture(can);
    tex.minFilter = THREE.LinearFilter;
    tex.magFilter = THREE.LinearFilter;
    globe.material = new THREE.MeshBasicMaterial({ map: tex });
  };
  img.src = '/world-map.svg';
}

function latLonToVec3(lat, lon) {
  var phi = (90 - lat) * Math.PI / 180;
  var theta = (lon + 180) * Math.PI / 180;
  return new THREE.Vector3(
    -GLOBE_RADIUS * Math.sin(phi) * Math.cos(theta),
    GLOBE_RADIUS * Math.cos(phi),
    GLOBE_RADIUS * Math.sin(phi) * Math.sin(theta)
  );
}

function projectToScreen(vec3) {
  var v = vec3.clone();
  v.applyMatrix4(globe.matrixWorld);
  v.project(camera);
  return {
    x: (v.x * 0.5 + 0.5) * viewport.clientWidth,
    y: (-v.y * 0.5 + 0.5) * viewport.clientHeight,
    z: v.z,
  };
}

function isOnFrontSide(worldPos) {
  var camPos = camera.position.clone();
  var globeCenter = new THREE.Vector3(0, 0, 0);
  var toPoint = worldPos.clone().sub(globeCenter).normalize();
  var toCamera = camPos.clone().sub(globeCenter).normalize();
  return toPoint.dot(toCamera) > 0.05;
}

var wasAnimating = false, needsRecluster = false;

function cameraIsSettled() {
  return Math.abs(targetRotX - rotX) < 0.002 &&
    Math.abs(targetRotY - rotY) < 0.002 &&
    Math.abs(targetDist - camDist) < 0.01;
}

function updateCamera() {
  rotX += (targetRotX - rotX) * 0.15;
  rotY += (targetRotY - rotY) * 0.15;
  camDist += (targetDist - camDist) * 0.15;
  camera.position.set(
    camDist * Math.sin(rotY) * Math.cos(rotX),
    camDist * Math.sin(rotX),
    camDist * Math.cos(rotY) * Math.cos(rotX)
  );
  camera.lookAt(0, 0, 0);
}

function animate() {
  animId = requestAnimationFrame(animate);
  var settled = cameraIsSettled();
  updateCamera();
  renderer.render(scene, camera);
  if (!settled) {
    wasAnimating = true;
    updateMarkerPositions();
  } else if (wasAnimating || needsRecluster) {
    wasAnimating = false;
    needsRecluster = false;
    renderMarkers();
  }
}

var activeMarkers = [];
function updateMarkerPositions() {
  for (var i = 0; i < activeMarkers.length; i++) {
    var m = activeMarkers[i];
    var sp = projectToScreen(m.cluster._pos);
    var visible = sp.z <= 1 && isOnFrontSide(m.cluster._pos);
    if (visible) {
      m.el.style.left = sp.x + 'px';
      m.el.style.top = sp.y + 'px';
      m.el.hidden = false;
    } else {
      m.el.hidden = true;
    }
  }
}

function clusterPoints(points) {
  var cellSize = Math.max(28, 56 / (MAX_DIST / camDist));
  var cells = new Map();
  for (var i = 0; i < points.length; i++) {
    var p = points[i];
    var sp = projectToScreen(p._pos);
    if (sp.z > 1 || sp.x < -50 || sp.x > viewport.clientWidth + 50 ||
        sp.y < -50 || sp.y > viewport.clientHeight + 50) continue;
    if (!isOnFrontSide(p._pos)) continue;
    var key = Math.floor(sp.x / cellSize) + ',' + Math.floor(sp.y / cellSize);
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push(p);
  }
  var result = [];
  cells.forEach(function (group) {
    var totalLat = 0, totalLon = 0, totalCount = 0;
    var firstDate = null, lastDate = null, assetId = null, filename = null;
    for (var i = 0; i < group.length; i++) {
      var p = group[i];
      totalLat += p.latitude * p.photo_count;
      totalLon += p.longitude * p.photo_count;
      totalCount += p.photo_count;
      if (!firstDate || (p.first_date && p.first_date < firstDate)) firstDate = p.first_date;
      if (!lastDate || (p.last_date && p.last_date > lastDate)) lastDate = p.last_date;
      if (!assetId) { assetId = p.asset_id; filename = p.filename; }
    }
    result.push({
      latitude: totalLat / totalCount,
      longitude: totalLon / totalCount,
      photo_count: totalCount,
      first_date: firstDate,
      last_date: lastDate,
      asset_id: assetId,
      filename: filename,
      _sources: group,
      _pos: latLonToVec3(totalLat / totalCount, totalLon / totalCount),
    });
  });
  return result;
}

function scheduleRecluster() {
  needsRecluster = true;
  if (!reclusterTimer) reclusterTimer = setTimeout(function () {
    reclusterTimer = null;
    if (!needsRecluster || !viewport.clientHeight) return;
    updateCamera();
    renderer.render(scene, camera);
    needsRecluster = false;
    renderMarkers();
  }, 80);
}
var reclusterTimer = null;

function renderMarkers() {
  var clustered = clusterPoints(rawPoints);
  markerLayer.querySelectorAll('.marker').forEach(function (el) { el.remove(); });
  selectedMarker = null;
  activeMarkers = [];

  for (var i = 0; i < clustered.length; i++) {
    var c = clustered[i];
    var sp = projectToScreen(c._pos);

    var button = document.createElement('button');
    button.type = 'button';
    var isMulti = c.photo_count > 1;
    button.className = 'marker' + (isMulti ? ' multi' : '');
    button.style.left = sp.x + 'px';
    button.style.top = sp.y + 'px';
    if (isMulti) {
      var count = c.photo_count;
      button.textContent = count >= 1000 ? Math.round(count / 1000) + 'k' : count;
    }
    button.title = c.photo_count.toLocaleString() + ' photo' + (c.photo_count === 1 ? '' : 's');
    button.setAttribute('aria-label', button.title + ' at ' + c.latitude.toFixed(3) + ', ' + c.longitude.toFixed(3));
    (function (cluster, btn) {
      btn.onclick = function (event) {
        event.stopPropagation();
        if (cluster._sources.length > 1 && camDist > MIN_DIST + 0.3) {
          centerOn(cluster.latitude, cluster.longitude, camDist * 0.4);
          scheduleRecluster();
        } else {
          selectMarker(btn, cluster);
          show(cluster);
        }
      };
    })(c, button);
    if (selectedCluster && selectedCluster.asset_id === c.asset_id &&
        Math.abs(selectedCluster.latitude - c.latitude) < 0.001) {
      button.classList.add('selected');
      selectedMarker = button;
    }
    markerLayer.appendChild(button);
    activeMarkers.push({ el: button, cluster: c });
  }
}

function selectMarker(button, cluster) {
  if (selectedMarker) selectedMarker.classList.remove('selected');
  selectedMarker = button;
  selectedMarker.classList.add('selected');
  selectedCluster = cluster;
}

function show(point) {
  document.getElementById('preview').src = '/media?id=' + point.asset_id;
  document.getElementById('placeTitle').textContent = point.photo_count.toLocaleString() + ' photo' + (point.photo_count === 1 ? '' : 's') + ' near this location';
  document.getElementById('placeDates').textContent = point.first_date === point.last_date
    ? (point.first_date || 'Date unknown')
    : (point.first_date || 'Unknown') + ' – ' + (point.last_date || 'Unknown');
  document.getElementById('placeCoords').textContent = point.latitude.toFixed(5) + ', ' + point.longitude.toFixed(5);
  document.getElementById('openPhoto').href = '/?date=' + (point.first_date || '') + '&selected=' + point.asset_id;
  document.getElementById('viewAllHere').href = '/?near=' + point.latitude.toFixed(1) + ',' + point.longitude.toFixed(1) + '&scope=all&sort=newest';
  document.getElementById('openStreetMap').href = 'https://www.openstreetmap.org/?mlat=' + point.latitude.toFixed(6) + '&mlon=' + point.longitude.toFixed(6) + '#map=16/' + point.latitude.toFixed(6) + '/' + point.longitude.toFixed(6);
  details.classList.add('open');
}

function centerOn(lat, lon, dist) {
  targetRotX = lat * Math.PI / 180;
  targetRotY = (lon + 90) * Math.PI / 180;
  targetDist = Math.max(MIN_DIST, Math.min(MAX_DIST, dist));
}

/* --- Input handling --- */
viewport.onwheel = function (event) {
  event.preventDefault();
  targetDist *= event.deltaY > 0 ? 1.1 : 0.9;
  targetDist = Math.max(MIN_DIST, Math.min(MAX_DIST, targetDist));
  scheduleRecluster();
};

viewport.onpointerdown = function (event) {
  if (event.target.closest('button,a,.details')) return;
  drag = { x: event.clientX, y: event.clientY, rx: targetRotX, ry: targetRotY };
  viewport.setPointerCapture(event.pointerId);
  viewport.classList.add('dragging');
};
viewport.onpointermove = function (event) {
  if (!drag) return;
  var dx = event.clientX - drag.x;
  var dy = event.clientY - drag.y;
  var sensitivity = 0.005;
  targetRotY = drag.ry - dx * sensitivity;
  targetRotX = Math.max(-Math.PI / 2 + 0.05, Math.min(Math.PI / 2 - 0.05,
    drag.rx - dy * sensitivity));
};
viewport.onpointerup = function () {
  drag = null;
  viewport.classList.remove('dragging');
};

document.getElementById('zoomIn').onclick = function () {
  targetDist = Math.max(MIN_DIST, targetDist * 0.7);
  scheduleRecluster();
};
document.getElementById('zoomOut').onclick = function () {
  targetDist = Math.min(MAX_DIST, targetDist * 1.4);
  scheduleRecluster();
};
document.getElementById('reset').onclick = function () {
  targetRotX = 0.52; targetRotY = 0.7;
  targetDist = 3.2;
};
document.getElementById('closeDetails').onclick = function () {
  details.classList.remove('open');
  selectedCluster = null;
  if (selectedMarker) { selectedMarker.classList.remove('selected'); selectedMarker = null; }
};
window.onresize = function () {
  if (!viewport.clientHeight) return;
  camera.aspect = viewport.clientWidth / viewport.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(viewport.clientWidth, viewport.clientHeight);
  if (rawPoints.length) scheduleRecluster();
};

/* --- Init --- */
initScene();
animate();

fetch('/api/map/points').then(function (r) { return r.json(); }).then(function (data) {
  rawPoints = (data.clusters || []).map(function (p) {
    return Object.assign({}, p, { _pos: latLonToVec3(p.latitude, p.longitude) });
  });
  document.getElementById('count').textContent =
    Number(data.located || 0).toLocaleString() + ' located photos \xb7 ' +
    rawPoints.length.toLocaleString() + ' places';
  if (!rawPoints.length) {
    document.getElementById('empty').classList.add('open');
    if (data.pending) document.getElementById('emptyText').innerHTML =
      Number(data.pending).toLocaleString() + ' cataloged files still need a location scan. Run <a href="/scan-photos">Scan your photos</a> → "Scan for photo locations", then return here.';
  }
  scheduleRecluster();
  var deepLat = parseFloat(new URLSearchParams(location.search).get('lat'));
  var deepLon = parseFloat(new URLSearchParams(location.search).get('lon'));
  if (!isNaN(deepLat) && !isNaN(deepLon) && rawPoints.length) {
    var nearest = rawPoints[0], bestDistance = Infinity;
    for (var i = 0; i < rawPoints.length; i++) {
      var p = rawPoints[i];
      var d = Math.hypot(p.latitude - deepLat, p.longitude - deepLon);
      if (d < bestDistance) { bestDistance = d; nearest = p; }
    }
    centerOn(nearest.latitude, nearest.longitude, 2);
    show(nearest);
  }
}).catch(function (error) {
  document.getElementById('empty').classList.add('open');
  document.getElementById('emptyText').textContent = error.message;
});

/* --- Menu / about / stale-banner (unchanged) --- */
var $ = function (id) { return document.getElementById(id); };
function openMenu(){$('menuPanel').classList.add('open');$('menuBackdrop').classList.add('open')}function closeMenu(){$('menuPanel').classList.remove('open');$('menuBackdrop').classList.remove('open')}
document.querySelectorAll('[data-panel]').forEach(function(b){b.onclick=function(){closeMenu();if(b.dataset.panel==='about'){var o=document.getElementById('aboutOverlay');if(o)o.classList.add('open')}else if(b.dataset.panel==='guide'||b.dataset.panel==='update')window.location='/?panel='+b.dataset.panel}});
document.getElementById('aboutClose').onclick=document.getElementById('aboutOverlay').onclick=function(e){if(e.target===this||e.target.id==='aboutClose')document.getElementById('aboutOverlay').classList.remove('open')};
$('menuToggle').onclick = function(e) { e.stopPropagation(); if($('menuPanel').classList.contains('open'))closeMenu();else openMenu(); };
$('menuClose').onclick = closeMenu; $('menuBackdrop').onclick = closeMenu;
document.addEventListener('click', function(e) { if (!e.target.closest('.menu-panel') && !e.target.closest('.menu-toggle')) closeMenu(); });
document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeMenu(); });

(function(){
var mapCsrf=JSON.parse(document.body.dataset.ll).csrf;
function checkServerVersion(){
  fetch('/api/version',{cache:'no-store'}).then(function(r){return r.json()}).then(function(info){
    var existing=document.getElementById('staleBanner');
    if(!info.restartReady){if(existing)existing.remove();return}
    if(existing)return;
    var b=document.createElement('div');b.id='staleBanner';b.className='stale-banner';
    b.innerHTML='<span class="stale-icon">⚠</span>'
      +'<span class="stale-msg"><b>Server is running v'+info.version+'</b> but v'+info.onDiskVersion+' is on disk. Click <b>Restart</b> to load it.</span>'
      +'<button type="button" class="stale-restart">Restart now</button>'
      +'<button type="button" class="stale-close" title="Dismiss">\xd7</button>';
    b.querySelector('.stale-close').onclick=function(){b.remove()};
    b.querySelector('.stale-restart').onclick=function(){
      var btn=b.querySelector('.stale-restart');var msg=b.querySelector('.stale-msg');
      btn.disabled=true;btn.textContent='Restarting…';
      msg.innerHTML='<b>Restarting server…</b> Page will reload when the new version is ready.';
      var oldStarted=info.startedAt;
      fetch('/api/update/restart-source',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf:mapCsrf})}).catch(function(){});
      var deadline=Date.now()+20000;
      (function poll(){
        if(Date.now()>deadline){msg.innerHTML='<b>Server did not restart.</b> Close and reopen LensLedger manually.';return}
        fetch('/api/version',{cache:'no-store'}).then(function(r){return r.json()}).then(function(j){
          if(j&&j.startedAt&&j.startedAt!==oldStarted)setTimeout(function(){location.reload()},200);
          else setTimeout(poll,500);
        }).catch(function(){setTimeout(poll,700)});
      })();
    };
    document.body.prepend(b);
  }).catch(function(){});
}
checkServerVersion();setInterval(checkServerVersion,30000);
})();
