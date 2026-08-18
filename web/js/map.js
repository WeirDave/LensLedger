const viewport = document.getElementById('viewport'), world = document.getElementById('world'), details = document.getElementById('details');
let zoom = 1, panX = 0, panY = 0, drag = null, rawPoints = [], selectedCluster = null, selectedMarker = null;
const MAP_W = 1440, MAP_H = 720, MAX_ZOOM = 32;

function baseScale() {
  return Math.min(viewport.clientWidth / MAP_W, viewport.clientHeight / MAP_H);
}

function transform() {
  const scale = baseScale() * zoom;
  const x = (viewport.clientWidth - MAP_W * scale) / 2 + panX;
  const y = (viewport.clientHeight - MAP_H * scale) / 2 + panY;
  world.style.transform = `translate(${x}px,${y}px) scale(${scale})`;
}

function toMapXY(lat, lon) {
  return { x: (lon + 180) / 360 * MAP_W, y: (90 - lat) / 180 * MAP_H };
}

function clusterPoints(points, zoomLevel) {
  const cellSize = Math.max(30, 60 / zoomLevel);
  const scale = baseScale() * zoomLevel;
  const cells = new Map();
  for (const p of points) {
    const mx = p._mx * scale;
    const my = p._my * scale;
    const key = Math.floor(mx / cellSize) + ',' + Math.floor(my / cellSize);
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push(p);
  }
  const result = [];
  for (const group of cells.values()) {
    let totalLat = 0, totalLon = 0, totalCount = 0, firstDate = null, lastDate = null, assetId = null, filename = null;
    for (const p of group) {
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
    });
  }
  return result;
}

function visibleBounds() {
  const scale = baseScale() * zoom;
  const ox = (viewport.clientWidth - MAP_W * scale) / 2 + panX;
  const oy = (viewport.clientHeight - MAP_H * scale) / 2 + panY;
  const pad = 40;
  return {
    left: (-ox - pad) / scale,
    right: (-ox + viewport.clientWidth + pad) / scale,
    top: (-oy - pad) / scale,
    bottom: (-oy + viewport.clientHeight + pad) / scale,
  };
}

function renderMarkers() {
  const bounds = visibleBounds();
  const visible = rawPoints.filter(p => p._mx >= bounds.left && p._mx <= bounds.right && p._my >= bounds.top && p._my <= bounds.bottom);
  const clustered = clusterPoints(visible, zoom);

  world.querySelectorAll('.marker').forEach(el => el.remove());
  selectedMarker = null;

  for (const c of clustered) {
    const pos = toMapXY(c.latitude, c.longitude);
    const button = document.createElement('button');
    button.type = 'button';
    const isMulti = c.photo_count > 1;
    button.className = 'marker' + (isMulti ? ' multi' : '');
    button.style.left = pos.x + 'px';
    button.style.top = pos.y + 'px';
    if (isMulti) {
      const count = c.photo_count;
      button.textContent = count >= 1000 ? Math.round(count / 1000) + 'k' : count;
    }
    button.title = c.photo_count.toLocaleString() + ' photo' + (c.photo_count === 1 ? '' : 's');
    button.setAttribute('aria-label', button.title + ' at ' + c.latitude.toFixed(3) + ', ' + c.longitude.toFixed(3));
    button.onclick = event => {
      event.stopPropagation();
      if (c._sources.length > 1 && zoom < MAX_ZOOM) {
        centerOn(c.latitude, c.longitude, zoom * 2.5);
        scheduleRecluster();
      } else {
        selectMarker(button, c);
        show(c);
      }
    };
    if (selectedCluster && selectedCluster.asset_id === c.asset_id &&
        Math.abs(selectedCluster.latitude - c.latitude) < 0.001) {
      button.classList.add('selected');
      selectedMarker = button;
    }
    world.append(button);
  }
}

let reclusterTimer = null;
function scheduleRecluster() {
  if (reclusterTimer) clearTimeout(reclusterTimer);
  reclusterTimer = setTimeout(renderMarkers, 80);
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

function centerOn(latitude, longitude, targetZoom) {
  zoom = Math.max(1, Math.min(MAX_ZOOM, targetZoom));
  const scale = baseScale() * zoom;
  const markerLeft = (longitude + 180) / 360 * MAP_W;
  const markerTop = (90 - latitude) / 180 * MAP_H;
  panX = scale * (MAP_W / 2 - markerLeft);
  panY = scale * (MAP_H / 2 - markerTop);
  transform();
}

function setZoom(next, cx = viewport.clientWidth / 2, cy = viewport.clientHeight / 2) {
  const prior = zoom;
  zoom = Math.max(1, Math.min(MAX_ZOOM, next));
  if (zoom === prior) return;
  panX = (panX - cx) * (zoom / prior) + cx;
  panY = (panY - cy) * (zoom / prior) + cy;
  transform();
  scheduleRecluster();
}

viewport.onwheel = event => {
  event.preventDefault();
  const box = viewport.getBoundingClientRect();
  setZoom(zoom * (event.deltaY < 0 ? 1.25 : .8), event.clientX - box.left, event.clientY - box.top);
};
viewport.onpointerdown = event => {
  if (event.target.closest('button,a,.details')) return;
  drag = { x: event.clientX, y: event.clientY, px: panX, py: panY };
  viewport.setPointerCapture(event.pointerId);
  viewport.classList.add('dragging');
};
viewport.onpointermove = event => {
  if (!drag) return;
  panX = drag.px + event.clientX - drag.x;
  panY = drag.py + event.clientY - drag.y;
  transform();
  scheduleRecluster();
};
viewport.onpointerup = () => { drag = null; viewport.classList.remove('dragging'); };
document.getElementById('zoomIn').onclick = () => setZoom(zoom * 1.4);
document.getElementById('zoomOut').onclick = () => setZoom(zoom / 1.4);
document.getElementById('reset').onclick = () => { zoom = 1; panX = panY = 0; transform(); renderMarkers(); };
document.getElementById('closeDetails').onclick = () => {
  details.classList.remove('open');
  selectedCluster = null;
  if (selectedMarker) { selectedMarker.classList.remove('selected'); selectedMarker = null; }
};
window.onresize = () => { transform(); scheduleRecluster(); };
fetch('/api/map/points').then(response => response.json()).then(data => {
  rawPoints = (data.clusters || []).map(p => {
    const pos = toMapXY(p.latitude, p.longitude);
    return { ...p, _mx: pos.x, _my: pos.y };
  });
  document.getElementById('count').textContent = Number(data.located || 0).toLocaleString() + ' located photos · ' + rawPoints.length.toLocaleString() + ' places';
  if (!rawPoints.length) {
    document.getElementById('empty').classList.add('open');
    if (data.pending) document.getElementById('emptyText').innerHTML = Number(data.pending).toLocaleString() + ' cataloged files still need a location scan. Run <a href="/scan-photos">Scan your photos</a> → "Scan for photo locations", then return here.';
  }
  transform();
  renderMarkers();
  const deepLat = parseFloat(new URLSearchParams(location.search).get('lat'));
  const deepLon = parseFloat(new URLSearchParams(location.search).get('lon'));
  if (!isNaN(deepLat) && !isNaN(deepLon) && rawPoints.length) {
    let nearest = rawPoints[0], bestDistance = Infinity;
    for (const p of rawPoints) {
      const d = Math.hypot(p.latitude - deepLat, p.longitude - deepLon);
      if (d < bestDistance) { bestDistance = d; nearest = p; }
    }
    centerOn(nearest.latitude, nearest.longitude, 4);
    renderMarkers();
    show(nearest);
  }
}).catch(error => {
  document.getElementById('empty').classList.add('open');
  document.getElementById('emptyText').textContent = error.message;
});
transform();

const $ = id => document.getElementById(id);
function openMenu(){$('menuPanel').classList.add('open');$('menuBackdrop').classList.add('open')}function closeMenu(){$('menuPanel').classList.remove('open');$('menuBackdrop').classList.remove('open')}
document.querySelectorAll('[data-panel]').forEach(b=>b.onclick=()=>{closeMenu();if(b.dataset.panel==='about'){const o=document.getElementById('aboutOverlay');if(o)o.classList.add('open')}else if(b.dataset.panel==='guide'||b.dataset.panel==='update')window.location='/?panel='+b.dataset.panel});
document.getElementById('aboutClose').onclick=document.getElementById('aboutOverlay').onclick=function(e){if(e.target===this||e.target.id==='aboutClose')document.getElementById('aboutOverlay').classList.remove('open')};
$('menuToggle').onclick = e => { e.stopPropagation(); if($('menuPanel').classList.contains('open'))closeMenu();else openMenu(); };
$('menuClose').onclick = closeMenu; $('menuBackdrop').onclick = closeMenu;
document.addEventListener('click', e => { if (!e.target.closest('.menu-panel') && !e.target.closest('.menu-toggle')) closeMenu(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
