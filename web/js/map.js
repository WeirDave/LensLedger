const viewport = document.getElementById('viewport'), world = document.getElementById('world'), details = document.getElementById('details');
let zoom = 1, panX = 0, panY = 0, drag = null, clusters = [], selectedMarker = null;

function baseScale() {
  return Math.min(viewport.clientWidth / 1440, viewport.clientHeight / 720);
}

function transform() {
  const scale = baseScale() * zoom;
  const x = (viewport.clientWidth - 1440 * scale) / 2 + panX;
  const y = (viewport.clientHeight - 720 * scale) / 2 + panY;
  world.style.transform = `translate(${x}px,${y}px) scale(${scale})`;
}

function marker(point) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'marker' + (point.photo_count > 1 ? ' multi' : '');
  button.style.left = ((point.longitude + 180) / 360 * 1440) + 'px';
  button.style.top = ((90 - point.latitude) / 180 * 720) + 'px';
  button.textContent = point.photo_count > 1 ? point.photo_count : '';
  button.title = point.photo_count.toLocaleString() + ' photo' + (point.photo_count === 1 ? '' : 's');
  button.setAttribute('aria-label', button.title + ' at ' + point.latitude.toFixed(3) + ', ' + point.longitude.toFixed(3));
  button.onclick = event => { event.stopPropagation(); select(button); show(point); };
  return button;
}

function select(button) {
  if (selectedMarker) selectedMarker.classList.remove('selected');
  selectedMarker = button;
  selectedMarker.classList.add('selected');
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
  details.classList.add('open');
}

function centerOn(latitude, longitude, targetZoom) {
  zoom = Math.max(1, Math.min(8, targetZoom));
  const scale = baseScale() * zoom;
  const markerLeft = (longitude + 180) / 360 * 1440;
  const markerTop = (90 - latitude) / 180 * 720;
  panX = scale * (720 - markerLeft);
  panY = scale * (360 - markerTop);
  transform();
}

function setZoom(next, cx = viewport.clientWidth / 2, cy = viewport.clientHeight / 2) {
  const prior = zoom;
  zoom = Math.max(1, Math.min(8, next));
  if (zoom === prior) return;
  panX = (panX - cx) * (zoom / prior) + cx;
  panY = (panY - cy) * (zoom / prior) + cy;
  transform();
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
};
viewport.onpointerup = () => { drag = null; viewport.classList.remove('dragging'); };
document.getElementById('zoomIn').onclick = () => setZoom(zoom * 1.4);
document.getElementById('zoomOut').onclick = () => setZoom(zoom / 1.4);
document.getElementById('reset').onclick = () => { zoom = 1; panX = panY = 0; transform(); };
document.getElementById('closeDetails').onclick = () => {
  details.classList.remove('open');
  if (selectedMarker) { selectedMarker.classList.remove('selected'); selectedMarker = null; }
};
window.onresize = transform;
fetch('/api/map/points').then(response => response.json()).then(data => {
  clusters = data.clusters || [];
  document.getElementById('count').textContent = Number(data.located || 0).toLocaleString() + ' located photos · ' + clusters.length.toLocaleString() + ' places';
  const markerButtons = clusters.map(marker);
  world.append(...markerButtons);
  if (!clusters.length) {
    document.getElementById('empty').classList.add('open');
    if (data.pending) document.getElementById('emptyText').textContent = Number(data.pending).toLocaleString() + ' cataloged files still need a location scan. Rescan this library from the library menu, then return here.';
  }
  transform();
  const deepLat = parseFloat(new URLSearchParams(location.search).get('lat'));
  const deepLon = parseFloat(new URLSearchParams(location.search).get('lon'));
  if (!isNaN(deepLat) && !isNaN(deepLon) && clusters.length) {
    let nearestIndex = 0, bestDistance = Infinity;
    clusters.forEach((candidate, index) => {
      const distance = Math.hypot(candidate.latitude - deepLat, candidate.longitude - deepLon);
      if (distance < bestDistance) { bestDistance = distance; nearestIndex = index; }
    });
    const nearest = clusters[nearestIndex];
    centerOn(nearest.latitude, nearest.longitude, 4);
    select(markerButtons[nearestIndex]);
    show(nearest);
  }
}).catch(error => {
  document.getElementById('empty').classList.add('open');
  document.getElementById('emptyText').textContent = error.message;
});
transform();
