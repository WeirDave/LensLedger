function initLightboxZoom(boxId) {
  const box = document.getElementById(boxId);
  if (!box) return null;
  let scale = 1, panX = 0, panY = 0, img = null;

  function apply() {
    if (!img) return;
    img.style.transform = scale === 1 ? '' : 'scale(' + scale + ') translate(' + panX + 'px,' + panY + 'px)';
    img.classList.toggle('lb-zoomed', scale > 1);
    const controls = box.querySelector('.lb-zoom-controls');
    if (controls) {
      controls.classList.toggle('visible', scale > 1);
      const level = controls.querySelector('.lb-zoom-level');
      if (level) level.textContent = Math.round(scale * 100) + '%';
    }
    repositionFaceBoxes();
  }

  function clamp() {
    if (!img || scale <= 1) { panX = 0; panY = 0; return; }
    const fitScale = Math.min(img.clientWidth / img.naturalWidth, img.clientHeight / img.naturalHeight);
    const imgW = img.naturalWidth * fitScale * scale, imgH = img.naturalHeight * fitScale * scale;
    const boxW = box.clientWidth, boxH = box.clientHeight;
    const maxPanX = Math.max(0, (imgW - boxW) / (2 * scale));
    const maxPanY = Math.max(0, (imgH - boxH) / (2 * scale));
    panX = Math.max(-maxPanX, Math.min(maxPanX, panX));
    panY = Math.max(-maxPanY, Math.min(maxPanY, panY));
  }

  function repositionFaceBoxes() {
    box.querySelectorAll('.face-box').forEach(function(marker) {
      if (!img || !img.naturalWidth || !img.naturalHeight) return;
      const face = marker._faceData;
      if (!face) return;
      const fitScale = Math.min(img.clientWidth / img.naturalWidth, img.clientHeight / img.naturalHeight);
      const shownWidth = img.naturalWidth * fitScale;
      const shownHeight = img.naturalHeight * fitScale;
      const offsetX = (img.clientWidth - shownWidth) / 2;
      const offsetY = (img.clientHeight - shownHeight) / 2;
      const imgRect = img.getBoundingClientRect();
      const boxRect = box.getBoundingClientRect();
      const imgOffsetX = imgRect.left - boxRect.left;
      const imgOffsetY = imgRect.top - boxRect.top;
      marker.style.left = (imgOffsetX + offsetX + face.box_left * shownWidth) + 'px';
      marker.style.top = (imgOffsetY + offsetY + face.box_top * shownHeight) + 'px';
      marker.style.width = ((face.box_right - face.box_left) * shownWidth) + 'px';
      marker.style.height = ((face.box_bottom - face.box_top) * shownHeight) + 'px';
    });
  }

  function reset() {
    scale = 1; panX = 0; panY = 0;
    if (img) { img.style.transform = ''; img.classList.remove('lb-zoomed'); }
    const controls = box.querySelector('.lb-zoom-controls');
    if (controls) controls.classList.remove('visible');
  }

  function attach(imgEl) {
    img = imgEl;
    reset();
    img.style.transformOrigin = 'center center';
  }

  box.addEventListener('wheel', function(e) {
    if (!img) return;
    e.preventDefault();
    var prev = scale;
    var factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    scale = Math.max(1, Math.min(20, scale * factor));
    if (scale === 1) { panX = 0; panY = 0; }
    else {
      var rect = img.getBoundingClientRect();
      var cx = (e.clientX - rect.left) / rect.width - 0.5;
      var cy = (e.clientY - rect.top) / rect.height - 0.5;
      panX -= cx * (scale - prev) / scale * img.clientWidth / scale;
      panY -= cy * (scale - prev) / scale * img.clientHeight / scale;
    }
    clamp(); apply();
  }, { passive: false });

  var panActive = false, panStartX = 0, panStartY = 0, panStartPanX = 0, panStartPanY = 0, panPointerId = null;
  box.addEventListener('pointerdown', function(e) {
    if (!img || scale <= 1 || e.button !== 0 || e.target.closest('.lb-zoom-controls,button')) return;
    panActive = true; panPointerId = e.pointerId;
    panStartX = e.clientX; panStartY = e.clientY;
    panStartPanX = panX; panStartPanY = panY;
    img.classList.add('lb-panning');
    box.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  box.addEventListener('pointermove', function(e) {
    if (!panActive || e.pointerId !== panPointerId) return;
    panX = panStartPanX + (e.clientX - panStartX) / scale;
    panY = panStartPanY + (e.clientY - panStartY) / scale;
    clamp(); apply();
  });
  box.addEventListener('pointerup', function(e) {
    if (!panActive || e.pointerId !== panPointerId) return;
    panActive = false;
    if (img) img.classList.remove('lb-panning');
    if (box.hasPointerCapture(e.pointerId)) box.releasePointerCapture(e.pointerId);
  });
  box.addEventListener('pointercancel', function() {
    panActive = false;
    if (img) img.classList.remove('lb-panning');
  });

  box.addEventListener('click', function(e) {
    if (e.detail >= 3 && img && !e.target.closest('.lb-zoom-controls,button')) {
      if (scale > 1) { reset(); apply(); }
      else { scale = 3; clamp(); apply(); }
    }
  });

  return { reset: reset, attach: attach, repositionFaceBoxes: repositionFaceBoxes };
}
