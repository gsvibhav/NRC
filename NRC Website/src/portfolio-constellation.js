/**
 * NRC Selected Work — constellation module (Phase 4 production port of the
 * approved prototype; Phase 5 replaces freeform drift with grouped orbital
 * motion: prototypes/portfolio-constellation-prototype.html).
 *
 * Client content is authored semantically in index.html (.work-data-list);
 * this module reads it from the DOM — it never owns the copy. Layout is a
 * deterministic tiered-radial placement with a measured collision solver.
 * Motion is a single requestAnimationFrame loop advancing one angle per
 * tier group (each node keeps its solver-assigned angular offset fixed
 * and rotates in lock-step with its group); it writes only two transform-
 * driving CSS custom properties per node per frame and is gated by class
 * state for hover/focus/panel-hover/selection/offscreen/hidden-tab/
 * reduced-motion/tablet-mobile.
 */

(function () {
  'use strict';

  var root = document.getElementById('work-constellation');
  if (!root) return;

  var dataList   = root.querySelector('.work-data-list');
  var app        = root.querySelector('.work-app');
  var field      = document.getElementById('work-field');
  var panelLabel = document.getElementById('work-panel-label');
  var panelName  = document.getElementById('work-panel-name');
  var panelCats  = document.getElementById('work-panel-cats');
  var panelDesc  = document.getElementById('work-panel-desc');
  var panelServ  = document.getElementById('work-panel-services');
  var panelLink  = document.getElementById('work-panel-link');
  var moreBlock  = document.getElementById('work-more');
  var moreList   = document.getElementById('work-more-list');
  var mobileGrid = document.getElementById('work-mobile-grid');
  var announcer  = document.getElementById('work-announcer');
  if (!dataList || !app || !field) return;

  /* ── DATA — parsed from the semantic list ─────────────────────────── */

  function parseClients() {
    var items = dataList.querySelectorAll(':scope > li');
    var clients = [];
    for (var i = 0; i < items.length; i++) {
      var li = items[i];
      var nameEl = li.querySelector('h3');
      var catEl = li.querySelector('.work-data-category');
      var descEl = li.querySelector('.work-data-description');
      if (!nameEl) continue;
      var services = (li.getAttribute('data-services') || '')
        .split('|').map(function (s) { return s.trim(); }).filter(Boolean);
      clients.push({
        id: li.getAttribute('data-client-id') || 'client-' + i,
        name: nameEl.textContent.trim(),
        category: catEl ? catEl.textContent.split('·').map(function (c) {
          return c.trim();
        }).filter(Boolean) : [],
        description: descEl ? descEl.textContent.trim() : '',
        services: services,
        caseStudyUrl: li.getAttribute('data-case-study-url') || '',
        tier: li.getAttribute('data-tier') || 'standard',
        order: parseInt(li.getAttribute('data-order') || String((i + 1) * 10), 10)
      });
    }
    return clients;
  }

  var clients = parseClients();
  if (!clients.length) return;

  var MAX_CONSTELLATION = 20;
  // Radii trimmed ~8-10% from the Phase 2-4 static values (0.24/0.20,
  // 0.38/0.33, 0.46/0.43) to guarantee no clipping through a full 360°
  // orbital revolution -- the static jitter (±8-14° around each tier's
  // start angle) never visited the true 0/90/180/270 extremes, but
  // continuous rotation does. Ring guides, static rest position, and
  // orbit motion all read from the same TIERS values, so nothing here
  // drifts out of sync with what's drawn.
  var TIERS = {
    featured: { rx: 0.17, ry: 0.145, start: -96, jitter: 8 },
    standard: { rx: 0.35, ry: 0.305, start: -58, jitter: 12 },
    compact:  { rx: 0.44, ry: 0.40,  start: -24, jitter: 14 }
  };

  // Grouped orbital motion: one angle per tier, advanced by elapsed real
  // time. Every node in a tier keeps its solver-assigned angular offset
  // fixed and rotates in lock-step with its group -- spacing calculated
  // by the collision solver can never be disturbed by the animation.
  //
  // Direction: both rings turn the SAME way (clockwise). Two concentric
  // rings rotating at different net rates will always re-align
  // periodically -- that is unavoidable geometry, identical to real
  // planetary conjunctions -- so what actually matters is how *often*
  // that happens. Opposite directions sum the two rates (here ~19°/s
  // relative), producing a realignment every ~19s -- frequent enough
  // that text-on-text overlap showed up in ~13% of sampled frames during
  // testing. Same direction only differentiates the rates (~3.4°/s
  // relative here), stretching the cycle to ~105s, so a typical visitor
  // scrolling past the section will rarely see one at all. Tested and
  // chosen over opposite directions for exactly this reason -- it read
  // calmer and produced far fewer overlaps, not just an aesthetic
  // preference. The featured (inner) radius was also trimmed to widen
  // the gap to the standard ring, shrinking the residual danger arc.
  var ORBIT_CFG = {
    featured: { duration: 32, dir: 1 },  // inner, clockwise
    standard: { duration: 46, dir: 1 },  // outer, clockwise (same direction)
    compact:  { duration: 58, dir: 1 }   // future-proofing; dormant today
  };

  var state = {
    committedId: null,
    previewId: null,
    layout: null,       // desktop | tablet | mobile
    settled: false,
    onscreen: false,
    pointerInside: false,
    focusInside: false,
    panelPointerInside: false
  };

  var orbit = {
    active: false,
    rafId: null,
    lastT: null,
    angles: { featured: 0, standard: 0, compact: 0 },
    nodesByTier: { featured: [], standard: [], compact: [] },
    fieldW: 0,
    fieldH: 0
  };

  var reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  function motionReduced() { return reduceMQ.matches; }

  function byId(id) {
    for (var i = 0; i < clients.length; i++)
      if (clients[i].id === id) return clients[i];
    return null;
  }

  /* ── DETERMINISTIC HASH (djb2 → 0..1) ─────────────────────────────── */

  function hash01(str, salt) {
    var h = 5381, s = str + '|' + salt;
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    return (h % 10000) / 10000;
  }

  /* ── LAYOUT SOLVER (optimized: cached rects, pass ceiling 30,
        graduated residual sweep — deterministic) ─────────────────────── */

  var rectReads = 0;
  function readRect(node) {
    rectReads++;
    return node.el.getBoundingClientRect();
  }

  function splitClients() {
    var sorted = clients.slice().sort(function (a, b) { return a.order - b.order; });
    return {
      constellation: sorted.slice(0, MAX_CONSTELLATION),
      overflow: sorted.slice(MAX_CONSTELLATION)
    };
  }

  function applyPosition(node) {
    var cfg = node.tierCfg;
    var rad = node.angle * Math.PI / 180;
    var x = 50 + cfg.rx * node.rMod * Math.cos(rad) * 100;
    var y = 50 + cfg.ry * node.rMod * Math.sin(rad) * 100;
    node.el.style.left = x + '%';
    node.el.style.top = y + '%';
  }

  function clampIntoField(node) {
    var f = field.getBoundingClientRect();
    if (!f.width) return;
    var r = readRect(node);
    var leftPct = parseFloat(node.el.style.left);
    var topPct = parseFloat(node.el.style.top);
    var EDGE = 4;
    if (r.left < f.left + EDGE)     leftPct += ((f.left + EDGE) - r.left) / f.width * 100;
    if (r.right > f.right - EDGE)   leftPct -= (r.right - (f.right - EDGE)) / f.width * 100;
    if (r.top < f.top + EDGE)       topPct += ((f.top + EDGE) - r.top) / f.height * 100;
    if (r.bottom > f.bottom - EDGE) topPct -= (r.bottom - (f.bottom - EDGE)) / f.height * 100;
    node.el.style.left = leftPct + '%';
    node.el.style.top = topPct + '%';
  }

  function collidesCached(rect, rects, selfIdx, pad) {
    for (var k = 0; k < rects.length; k++) {
      if (k === selfIdx) continue;
      var b = rects[k];
      if (rect.left - pad < b.right && rect.right + pad > b.left &&
          rect.top - pad < b.bottom && rect.bottom + pad > b.top) return true;
    }
    return false;
  }

  function resolveResidual(nodes, pad) {
    var rects = nodes.map(readRect);
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (!collidesCached(rects[i], rects, i, pad)) continue;
      var tiers = [node.tierCfg, TIERS.compact];
      var placed = false;
      for (var t = 0; t < tiers.length && !placed; t++) {
        for (var rStep = 0; rStep < 3 && !placed; rStep++) {
          for (var slot = 0; slot < 24 && !placed; slot++) {
            node.tierCfg = tiers[t];
            node.rMod = 1 + rStep * 0.09;
            node.angle = slot * 15 + hash01(node.client.id, 'slot') * 7;
            applyPosition(node);
            clampIntoField(node);
            rects[i] = readRect(node);
            if (!collidesCached(rects[i], rects, i, pad)) placed = true;
          }
        }
      }
    }
  }

  function placeNodes(nodes) {
    var groups = { featured: [], standard: [], compact: [] };
    nodes.forEach(function (n) {
      (groups[n.client.tier] || groups.standard).push(n);
    });

    Object.keys(groups).forEach(function (tier) {
      var g = groups[tier], cfg = TIERS[tier];
      var n = g.length;
      if (!n) return;
      g.forEach(function (node, i) {
        var maxJ = Math.min(cfg.jitter, (360 / n) * 0.22);
        var jitterA = (hash01(node.client.id, 'a') - 0.5) * 2 * maxJ;
        node.angle = cfg.start + (i * 360 / Math.max(n, 1)) + jitterA;
        node.rMod = 1 + (hash01(node.client.id, 'r') - 0.5) * 0.08;
        node.tierCfg = cfg;
      });
    });

    // Measure against base geometry only: .measuring neutralizes the
    // settle offset, transitions and drift animation during layout.
    field.classList.add('measuring');
    var t0 = performance.now();
    rectReads = 0;

    nodes.forEach(applyPosition);
    nodes.forEach(clampIntoField);

    var rects = nodes.map(readRect);
    var PAD = 14, MAX_PASSES = 30;
    for (var pass = 0; pass < MAX_PASSES; pass++) {
      var moved = false;
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var a = rects[i], b = rects[j];
          if (a.left - PAD < b.right && a.right + PAD > b.left &&
              a.top - PAD < b.bottom && a.bottom + PAD > b.top) {
            nodes[j].nudges = (nodes[j].nudges || 0) + 1;
            nodes[j].angle += 9;
            if (nodes[j].nudges % 3 === 0) {
              nodes[j].rMod = Math.min(nodes[j].rMod * 1.06, 1.35);
            }
            applyPosition(nodes[j]);
            clampIntoField(nodes[j]);
            rects[j] = readRect(nodes[j]);
            moved = true;
          }
        }
      }
      if (!moved) break;
    }

    resolveResidual(nodes, 14);
    resolveResidual(nodes, 10);
    resolveResidual(nodes, 6);

    field.classList.remove('measuring');

    // Lightweight telemetry for validation tooling; inert for visitors.
    window.__nrcConstellationPerf = {
      layoutMs: Math.round((performance.now() - t0) * 10) / 10,
      rectReads: rectReads,
      nodes: nodes.length
    };
  }

  /* ── GROUPED ORBITAL MOTION ────────────────────────────────────────
     One requestAnimationFrame loop, at most. It advances a single angle
     per tier group (time-based, not frame-count-based) and writes only
     two CSS custom properties per node -- --orbit-dx/--orbit-dy, in px,
     consumed by the .orbiting transform rule in index.html. No layout
     reads happen inside the loop: field dimensions are cached once per
     render (buildOrbitNodes), never re-measured mid-animation. Stopping
     the loop freezes the transform at its exact last value -- there is
     nothing to "snap back" because no other rule overrides it while
     .orbiting is present. */

  function tierKeyOf(cfg) {
    for (var k in TIERS) if (TIERS[k] === cfg) return k;
    return 'standard';
  }

  function buildOrbitNodes(nodes) {
    orbit.nodesByTier = { featured: [], standard: [], compact: [] };
    nodes.forEach(function (n) {
      var key = tierKeyOf(n.tierCfg);
      orbit.nodesByTier[key].push({ el: n.el, baseAngle: n.angle, rMod: n.rMod });
    });
    var f = field.getBoundingClientRect(); // one read, cached for the loop's lifetime
    orbit.fieldW = f.width;
    orbit.fieldH = f.height;
  }

  function stepOrbit(ts) {
    if (!orbit.active) return;
    if (orbit.lastT == null) orbit.lastT = ts;
    var dt = (ts - orbit.lastT) / 1000;
    orbit.lastT = ts;

    var tiers = ['featured', 'standard', 'compact'];
    for (var t = 0; t < tiers.length; t++) {
      var tier = tiers[t];
      var list = orbit.nodesByTier[tier];
      if (!list.length) continue;
      var ocfg = ORBIT_CFG[tier];
      var degPerSec = 360 / ocfg.duration;
      var groupAngle = (orbit.angles[tier] + ocfg.dir * degPerSec * dt + 360) % 360;
      orbit.angles[tier] = groupAngle;

      var cfg = TIERS[tier];
      for (var i = 0; i < list.length; i++) {
        var node = list[i];
        var restRad = node.baseAngle * Math.PI / 180;
        var curRad = (node.baseAngle + groupAngle) * Math.PI / 180;
        // Delta from the static rest position (not the raw ellipse
        // point) so the settle-in / static layout stays the anchor and
        // the orbit is purely an additive transform offset.
        var dxPct = cfg.rx * node.rMod * 100 * (Math.cos(curRad) - Math.cos(restRad));
        var dyPct = cfg.ry * node.rMod * 100 * (Math.sin(curRad) - Math.sin(restRad));
        node.el.style.setProperty('--orbit-dx', (dxPct / 100 * orbit.fieldW).toFixed(2) + 'px');
        node.el.style.setProperty('--orbit-dy', (dyPct / 100 * orbit.fieldH).toFixed(2) + 'px');
      }
    }

    orbit.rafId = requestAnimationFrame(stepOrbit);
  }

  function startOrbit() {
    if (orbit.active) return;
    orbit.active = true;
    orbit.lastT = null;
    orbit.rafId = requestAnimationFrame(stepOrbit);
  }

  function stopOrbit() {
    if (!orbit.active) return;
    orbit.active = false;
    if (orbit.rafId) cancelAnimationFrame(orbit.rafId);
    orbit.rafId = null;
    orbit.lastT = null;
    // orbit.angles retained on purpose: resuming continues from the
    // same group angle, so there is no jump when the loop restarts.
  }

  /* ── RENDER ───────────────────────────────────────────────────────── */

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function renderField() {
    field.innerHTML = '';
    field.classList.remove('settled', 'settle-done', 'orbiting', 'has-active');
    stopOrbit();
    var split = splitClients();

    var tiersInUse = {};
    split.constellation.forEach(function (c) { tiersInUse[c.tier] = true; });
    Object.keys(tiersInUse).forEach(function (tier) {
      var cfg = TIERS[tier];
      if (!cfg) return;
      var o = document.createElement('div');
      o.className = 'work-orbit';
      o.style.width = (cfg.rx * 2 * 100) + '%';
      o.style.height = (cfg.ry * 2 * 100) + '%';
      field.appendChild(o);
    });

    var nodes = [];
    split.constellation.forEach(function (client, i) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'work-node tier-' + client.tier;
      b.setAttribute('data-id', client.id);
      b.setAttribute('aria-pressed', 'false');
      var nm = document.createElement('span');
      nm.className = 'nm';
      nm.textContent = client.name;
      b.appendChild(nm);

      var inx = (hash01(client.id, 'ix') - 0.5) * 40;
      var iny = (hash01(client.id, 'iy') - 0.5) * 40;
      b.style.setProperty('--inx', inx.toFixed(1) + 'px');
      b.style.setProperty('--iny', iny.toFixed(1) + 'px');
      b.style.setProperty('--settle-delay', Math.round(i * 45) + 'ms');

      field.appendChild(b);
      nodes.push({ client: client, el: b });
    });

    placeNodes(nodes);
    buildOrbitNodes(nodes);

    moreList.innerHTML = '';
    if (split.overflow.length) {
      moreBlock.hidden = false;
      split.overflow.forEach(function (client) {
        var li = document.createElement('li');
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'work-more-btn';
        b.setAttribute('data-id', client.id);
        b.setAttribute('aria-pressed', 'false');
        b.textContent = client.name;
        li.appendChild(b);
        moreList.appendChild(li);
      });
    } else {
      moreBlock.hidden = true;
    }

    if (motionReduced()) {
      field.classList.add('settled', 'settle-done');
      state.settled = true;
    } else if (state.onscreen) {
      requestSettle();
    }
    syncOrbit();
  }

  function renderMobileGrid() {
    mobileGrid.innerHTML = '';
    var sorted = clients.slice().sort(function (a, b) { return a.order - b.order; });
    sorted.forEach(function (client) {
      var wrap = document.createElement('div');
      wrap.className = 'work-m-item';

      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'work-m-btn';
      b.setAttribute('data-id', client.id);
      b.setAttribute('aria-expanded', 'false');
      b.setAttribute('aria-controls', 'work-m-detail-' + client.id);
      b.textContent = client.name;

      var d = document.createElement('div');
      d.className = 'work-m-detail';
      d.id = 'work-m-detail-' + client.id;
      d.hidden = true;

      wrap.appendChild(b);
      wrap.appendChild(d);
      mobileGrid.appendChild(wrap);
    });
  }

  function detailHTML(client) {
    var h = '';
    if (client.category.length) {
      h += '<p class="work-panel-cats">' + esc(client.category.join(' · ')) + '</p>';
    }
    if (client.description) {
      h += '<p class="work-panel-desc">' + esc(client.description) + '</p>';
    }
    if (client.services.length) {
      h += '<ul class="work-panel-services">' + client.services.map(function (s) {
        return '<li>' + esc(s) + '</li>';
      }).join('') + '</ul>';
    }
    if (client.caseStudyUrl) {
      h += '<a class="work-panel-link" href="' + esc(client.caseStudyUrl) + '">View case study</a>';
    }
    return h;
  }

  var PANEL_DEFAULT_NAME = panelName ? panelName.textContent : '';
  var PANEL_DEFAULT_DESC = panelDesc ? panelDesc.textContent : '';
  var PANEL_DEFAULT_LABEL = panelLabel ? panelLabel.textContent : '';

  function renderPanel() {
    var client = byId(state.previewId) || byId(state.committedId);
    if (!client) {
      panelLabel.textContent = PANEL_DEFAULT_LABEL;
      panelName.textContent = PANEL_DEFAULT_NAME;
      panelCats.textContent = '';
      panelDesc.textContent = PANEL_DEFAULT_DESC;
      panelDesc.hidden = false;
      panelServ.innerHTML = '';
      panelServ.hidden = true;
      panelLink.hidden = true;
      return;
    }
    panelLabel.textContent = 'Client';
    panelName.textContent = client.name;
    panelCats.textContent = client.category.join(' · ');
    panelDesc.textContent = client.description || '';
    panelDesc.hidden = !client.description;
    panelServ.innerHTML = client.services.map(function (s) {
      return '<li>' + esc(s) + '</li>';
    }).join('');
    panelServ.hidden = !client.services.length;
    if (client.caseStudyUrl) {
      panelLink.href = client.caseStudyUrl;
      panelLink.hidden = false;
    } else {
      panelLink.hidden = true;
    }
  }

  function syncPressed() {
    var btns = app.querySelectorAll('[data-id]');
    for (var i = 0; i < btns.length; i++) {
      if (btns[i].classList.contains('work-m-btn')) continue;
      btns[i].setAttribute('aria-pressed',
        btns[i].getAttribute('data-id') === state.committedId ? 'true' : 'false');
    }
    field.classList.toggle('has-active',
      !!(state.committedId || state.previewId || state.pointerInside));
  }

  /* ── SELECTION / PREVIEW ──────────────────────────────────────────── */

  var hoverTimer = null;

  function commit(id, announce) {
    state.committedId = id;
    state.previewId = null;
    renderPanel();
    syncPressed();
    syncOrbit();
    if (announce && id) {
      var c = byId(id);
      if (c) {
        announcer.textContent = [c.name, c.category.join(', '), c.description]
          .filter(Boolean).join('. ') + '.' +
          (c.caseStudyUrl ? ' Case study available.' : '');
      }
    }
  }

  function clearSelection() {
    state.committedId = null;
    state.previewId = null;
    renderPanel();
    syncPressed();
    syncOrbit();
    announcer.textContent = '';
  }

  function preview(id) {
    state.previewId = id;
    renderPanel();   // silent — announcer untouched on hover preview
    syncPressed();
  }

  /* ── ORBIT COORDINATION (rAF loop gated by class + pause state) ───── */

  function syncOrbit() {
    var allowed = state.layout === 'desktop' &&
                  !motionReduced() &&
                  state.settled;
    field.classList.toggle('orbiting', allowed);
    var paused = state.pointerInside || state.focusInside ||
                 state.panelPointerInside ||
                 !!state.committedId || !state.onscreen || document.hidden;
    if (allowed && !paused) startOrbit();
    else stopOrbit();
  }

  function requestSettle() {
    if (state.settled) { field.classList.add('settled', 'settle-done'); return; }
    if (motionReduced()) {
      field.classList.add('settled', 'settle-done');
      state.settled = true;
      syncOrbit();
      return;
    }
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        field.classList.add('settled');
        state.settled = true;
        setTimeout(function () {
          field.classList.add('settle-done');
          syncOrbit();
        }, 1000 + MAX_CONSTELLATION * 45);
      });
    });
  }

  /* ── EVENTS ───────────────────────────────────────────────────────── */

  app.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-id]');
    if (!btn) return;
    var id = btn.getAttribute('data-id');
    if (btn.classList.contains('work-m-btn')) {
      var wasOpen = btn.getAttribute('aria-expanded') === 'true';
      mobileGrid.querySelectorAll('.work-m-btn').forEach(function (b) {
        b.setAttribute('aria-expanded', 'false');
        document.getElementById(b.getAttribute('aria-controls')).hidden = true;
      });
      if (!wasOpen) {
        btn.setAttribute('aria-expanded', 'true');
        var det = document.getElementById(btn.getAttribute('aria-controls'));
        det.innerHTML = detailHTML(byId(id));
        det.hidden = false;
        var c = byId(id);
        announcer.textContent = [c.name, c.category.join(', '), c.description]
          .filter(Boolean).join('. ') + '.';
      } else {
        announcer.textContent = '';
      }
      return;
    }
    commit(id, true);
  });

  field.addEventListener('mouseover', function (e) {
    var btn = e.target.closest('.work-node');
    if (!btn) return;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(function () {
      preview(btn.getAttribute('data-id'));
    }, 150);
  });
  field.addEventListener('mouseout', function (e) {
    if (!e.target.closest('.work-node')) return;
    clearTimeout(hoverTimer);
    if (state.previewId) { state.previewId = null; renderPanel(); syncPressed(); }
  });
  field.addEventListener('mouseenter', function () {
    state.pointerInside = true; syncPressed(); syncOrbit();
  });
  field.addEventListener('mouseleave', function () {
    state.pointerInside = false;
    clearTimeout(hoverTimer);
    if (state.previewId) { state.previewId = null; renderPanel(); }
    syncPressed(); syncOrbit();
  });

  var panelEl = document.getElementById('work-panel');
  if (panelEl) {
    panelEl.addEventListener('mouseenter', function () {
      state.panelPointerInside = true; syncOrbit();
    });
    panelEl.addEventListener('mouseleave', function () {
      state.panelPointerInside = false; syncOrbit();
    });
  }

  app.addEventListener('focusin', function (e) {
    if (e.target.closest('.work-field')) { state.focusInside = true; syncOrbit(); }
  });
  app.addEventListener('focusout', function (e) {
    if (!field.contains(e.relatedTarget)) { state.focusInside = false; syncOrbit(); }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && state.committedId) clearSelection();
  });

  document.addEventListener('click', function (e) {
    if (!state.committedId) return;
    if (e.target.closest('#work-constellation')) return;
    clearSelection();
  });

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      state.onscreen = entry.isIntersecting;
      if (entry.isIntersecting && !state.settled) requestSettle();
      syncOrbit();
    });
  }, { threshold: 0.15 });
  io.observe(root);

  document.addEventListener('visibilitychange', syncOrbit);
  reduceMQ.addEventListener('change', function () {
    if (motionReduced()) {
      field.classList.add('settled', 'settle-done');
      state.settled = true;
    }
    syncOrbit();
  });

  /* ── LAYOUT MODE (mobile ≤600, tablet ≤960, desktop above) ────────── */

  function applyLayout() {
    var w = window.innerWidth;
    var mode = w <= 600 ? 'mobile' : (w <= 960 ? 'tablet' : 'desktop');
    if (mode === state.layout) return false;
    state.layout = mode;
    root.classList.remove('layout-desktop', 'layout-tablet', 'layout-mobile');
    root.classList.add('layout-' + mode);
    if (mode === 'mobile') renderMobileGrid();
    else { renderField(); renderPanel(); }
    syncOrbit();
    return true;
  }

  var resizeT;
  window.addEventListener('resize', function () {
    clearTimeout(resizeT);
    resizeT = setTimeout(function () {
      var switched = applyLayout();
      // Same layout mode but new width: re-place nodes at the new size.
      if (!switched && state.layout !== 'mobile') { renderField(); renderPanel(); }
    }, 150);
  });

  /* ── INIT ─────────────────────────────────────────────────────────── */

  app.hidden = false;
  applyLayout();

  // Node boxes are measured during layout, so metrics from a fallback
  // font (before the Cormorant/Inter web fonts arrive) would leave the
  // solver's positions wrong once the real fonts swap in. WebKit's
  // FontFaceSet.status can report 'loaded' before any face has even
  // been *requested*, so status is not a safe gate -- instead we
  // force-load the exact faces the constellation renders with and
  // re-place once they resolve. fonts.ready is kept as a second net
  // for anything else that settles later; the re-layout is idempotent
  // and costs ~1-3ms at this client count.
  function relayout() {
    if (state.layout !== 'mobile') {
      renderField();
      renderPanel();
      syncPressed();
    }
  }
  if (document.fonts && document.fonts.load) {
    Promise.all([
      document.fonts.load('300 36px "Cormorant Garamond"'),
      document.fonts.load('300 23px "Cormorant Garamond"'),
      document.fonts.load('500 12px Inter')
    ]).then(relayout).catch(function () {});
    document.fonts.ready.then(relayout);
  }
})();
