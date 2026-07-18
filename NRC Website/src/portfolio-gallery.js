/**
 * NRC Selected Work — 3D circular project gallery.
 *
 * Replaces the previous orbital constellation. Client content is authored
 * semantically in index.html (.work-data-list); this module reads it from
 * the DOM -- it never owns the copy. The gallery is a 3D ring (CSS
 * rotateY/translateZ) that rotates on its own, continuously, at a slow
 * fixed pace -- there is no scroll-linked section and no sticky viewport.
 * Below 600px it becomes a horizontal scroll-snap strip instead of a
 * shrunk ring. Motion is a single requestAnimationFrame loop that writes
 * one transform-driving custom property on the ring plus small numeric
 * custom properties per card (--depth, --card-effective) -- no per-frame
 * layout reads, no per-card loops, no continuous filter/backdrop-filter
 * animation.
 */

(function () {
  'use strict';

  var root = document.getElementById('work-constellation');
  if (!root) return;

  var dataList = root.querySelector('.work-data-list');
  if (!dataList) return;

  var galleryRoot, viewport, ring, mobileTrack, announcer;

  /* ── DATA — parsed from the semantic list; never duplicated in JS ─── */

  function parseClients() {
    var items = dataList.querySelectorAll(':scope > li');
    var out = [];
    for (var i = 0; i < items.length; i++) {
      var li = items[i];
      var nameEl = li.querySelector('.work-data-name, h3');
      var catEl = li.querySelector('.work-data-category');
      var descEl = li.querySelector('.work-data-description');
      if (!nameEl) continue;
      var services = (li.getAttribute('data-services') || '')
        .split('|').map(function (s) { return s.trim(); }).filter(Boolean);
      var imgSrc = li.getAttribute('data-image-src') || '';
      out.push({
        id: li.getAttribute('data-client-id') || 'client-' + i,
        client: nameEl.textContent.trim(),
        category: catEl ? catEl.textContent.split('·').map(function (c) {
          return c.trim();
        }).filter(Boolean) : [],
        description: descEl ? descEl.textContent.trim() : '',
        services: services,
        slug: li.getAttribute('data-client-id') || 'client-' + i,
        // Optional future image layer. Populating data-image-src/-alt/
        // -position on the <li> is enough to light this up later --
        // nothing else in this module needs to change (see renderCard).
        image: imgSrc ? {
          src: imgSrc,
          alt: li.getAttribute('data-image-alt') || '',
          position: li.getAttribute('data-image-position') || 'center'
        } : null,
        href: li.getAttribute('data-case-study-url') || null,
        order: parseInt(li.getAttribute('data-order') || String((i + 1) * 10), 10)
      });
    }
    out.sort(function (a, b) { return a.order - b.order; });
    return out;
  }

  var clients = parseClients();

  /* ── STATE ────────────────────────────────────────────────────────── */

  var state = {
    ringAngle: 0,          // current visual rotation, degrees -- auto-rotation only
    activeElapsed: 0,      // seconds of actual rotation time accumulated so far --
                           // only ever advances while the loop is running, so it
                           // freezes exactly during any pause and never resets
                           // once the gallery has first started rotating
    lastFrameT: null,
    pointerInside: false,
    focusInside: false,
    onscreen: false,
    isMobile: false,
    running: false,
    rafId: null
  };

  var reduceMQ = window.matchMedia('(prefers-reduced-motion: reduce)');
  var mobileMQ = window.matchMedia('(max-width: 600px)');
  function motionReduced() { return reduceMQ.matches; }

  // Two-phase pace, named so each part is easy to find and retune:
  // a brief brisk intro so visitors immediately read the ring as
  // circular, then a smooth ease down to a much slower, easier-to-read
  // steady pace. All driven by state.activeElapsed (see above), not
  // wall-clock time, so pausing/resuming never skips or repeats part
  // of the curve.
  var INTRO_SECONDS = 9;          // brisk-pace duration (8-10s requested)
  var EASE_SECONDS = 6;           // smooth transition length from brisk to slow
  var FAST_ROTATE_SECONDS = 30;   // brisk pace: one revolution every 30s
  var SLOW_ROTATE_SECONDS = 58;   // settled pace: one revolution every ~58s (55-60s requested)
  var FAST_DEG_PER_SEC = 360 / FAST_ROTATE_SECONDS;
  var SLOW_DEG_PER_SEC = 360 / SLOW_ROTATE_SECONDS;
  var FRONT_ON = 0.90;         // depth threshold: becomes "front" (hysteresis pair)
  var FRONT_OFF = 0.80;

  // Classic smoothstep: eases both into and out of the transition so the
  // rate of change itself has no sudden corner, not just the angle.
  function smoothstep(x) {
    x = x < 0 ? 0 : (x > 1 ? 1 : x);
    return x * x * (3 - 2 * x);
  }

  function currentDegPerSec(activeElapsed) {
    if (activeElapsed <= INTRO_SECONDS) return FAST_DEG_PER_SEC;
    var progress = (activeElapsed - INTRO_SECONDS) / EASE_SECONDS;
    if (progress >= 1) return SLOW_DEG_PER_SEC;
    var eased = smoothstep(progress);
    return FAST_DEG_PER_SEC + (SLOW_DEG_PER_SEC - FAST_DEG_PER_SEC) * eased;
  }

  /* ── CARD MARKUP ──────────────────────────────────────────────────── */

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function cardInnerHTML(c, index) {
    var h = '<div class="work-card-glass">';
    h += '<div class="work-card-content">';
    h += '<p class="work-card-eyebrow">' + String(index + 1).padStart(2, '0');
    if (c.category.length) h += ' <span class="work-card-eyebrow-div" aria-hidden="true">&middot;</span> ' + esc(c.category.join(' / '));
    h += '</p>';
    h += '<h3 class="work-card-name">' + esc(c.client) + '</h3>';
    if (c.description) h += '<p class="work-card-desc">' + esc(c.description) + '</p>';
    if (c.services.length) {
      h += '<ul class="work-card-services">' + c.services.map(function (s) {
        return '<li>' + esc(s) + '</li>';
      }).join('') + '</ul>';
    }
    if (c.href) h += '<span class="work-card-cta">View project <span aria-hidden="true">&rarr;</span></span>';
    h += '</div></div>';
    return h;
  }

  // The optional image layer: only ever created when data supplies one.
  // It is inserted *beneath* the glass content surface (first child), so
  // adding real photography later is a pure data change -- this function
  // is the only place that needs to know about `image` at all.
  function imageLayerHTML(c) {
    if (!c.image) return '';
    return '<div class="work-card-image" style="background-image:url(\'' + esc(c.image.src) +
      '\');background-position:' + esc(c.image.position || 'center') + ';" role="img" aria-label="' +
      esc(c.image.alt || '') + '"></div>';
  }

  function buildRingCard(c, index, angle) {
    var el = document.createElement(c.href ? 'a' : 'article');
    el.className = 'work-card';
    el.setAttribute('data-id', c.id);
    var label = c.client + (c.category.length ? ' — ' + c.category.join(', ') : '');
    el.setAttribute('aria-label', label);
    if (c.href) {
      el.href = c.href;
    } else {
      el.tabIndex = 0; // focusable so keyboard users can pause/inspect every card,
                        // exactly as hover does for pointer users -- there is no
                        // interactive affordance implied, just a stopping point.
    }
    el.style.setProperty('--card-angle', angle + 'deg');
    el.innerHTML = imageLayerHTML(c) + cardInnerHTML(c, index);
    // Attached per-card rather than delegated from the ring: pointerenter/
    // pointerleave don't bubble, so a single delegated listener on an
    // ancestor won't reliably fire for individual card targets. Five
    // listeners is a trivial cost either way.
    el.addEventListener('pointermove', onCardPointerMove);
    el.addEventListener('pointerleave', function () {
      el.style.removeProperty('--px');
      el.style.removeProperty('--py');
    });
    return el;
  }

  function buildMobileCard(c, index) {
    var el = document.createElement(c.href ? 'a' : 'article');
    el.className = 'work-card work-card--mobile';
    el.setAttribute('data-id', c.id);
    var label = c.client + (c.category.length ? ' — ' + c.category.join(', ') : '');
    el.setAttribute('aria-label', label);
    if (c.href) el.href = c.href;
    el.innerHTML = imageLayerHTML(c) + cardInnerHTML(c, index);
    return el;
  }

  /* ── RENDER (once; both presentations are pure CSS switches after) ── */

  var ringCards = []; // { el, baseAngle }

  function render() {
    var n = clients.length;
    ring.innerHTML = '';
    mobileTrack.innerHTML = '';
    ringCards = [];

    clients.forEach(function (c, i) {
      var angle = (360 / n) * i;
      var ringEl = buildRingCard(c, i, angle);
      // Initial value before the rAF loop's first tick -- avoids an
      // undamped flash of the full (undampened) rotation on first paint.
      ringEl.style.setProperty('--card-effective', normalize180(angle) + 'deg');
      ring.appendChild(ringEl);
      ringCards.push({ el: ringEl, baseAngle: angle });

      var mobileEl = buildMobileCard(c, i);
      mobileTrack.appendChild(mobileEl);
    });
  }

  /* ── ROTATION LOOP — single rAF, transform-only continuous writes ─── */

  function normalize180(deg) {
    var d = deg % 360;
    if (d > 180) d -= 360;
    if (d < -180) d += 360;
    return d;
  }

  function frame(ts) {
    if (!state.running) return;
    if (state.lastFrameT == null) state.lastFrameT = ts;
    var dt = (ts - state.lastFrameT) / 1000;
    state.lastFrameT = ts;

    // Continuous auto-rotation only -- the loop never runs at all while
    // any pause condition holds (see syncRotation), so there is nothing
    // further to gate here; every tick this runs, the ring should turn.
    state.activeElapsed += dt;
    var degPerSec = currentDegPerSec(state.activeElapsed);
    state.ringAngle = (state.ringAngle + degPerSec * dt) % 360;
    ring.style.setProperty('--ring-angle', state.ringAngle.toFixed(2) + 'deg');

    for (var i = 0; i < ringCards.length; i++) {
      var card = ringCards[i];
      var effective = normalize180(card.baseAngle + state.ringAngle);
      var depth = Math.max(0, Math.cos(effective * Math.PI / 180));
      card.el.style.setProperty('--depth', depth.toFixed(3));
      // Normalized to [-180,180] before it ever reaches CSS: the raw
      // (baseAngle + ringAngle) sum grows unbounded as the ring spins,
      // and feeding that directly into a dampening counter-rotation in
      // CSS produced wildly wrong (often mirrored/backface) results
      // once the sum passed 180deg. Writing the already-normalized
      // value here keeps the CSS-side calc() correct at any rotation.
      card.el.style.setProperty('--card-effective', effective.toFixed(2) + 'deg');
      var isFront = card.el.classList.contains('is-front');
      if (!isFront && depth > FRONT_ON) card.el.classList.add('is-front');
      else if (isFront && depth < FRONT_OFF) card.el.classList.remove('is-front');
    }

    state.rafId = requestAnimationFrame(frame);
  }

  function startLoop() {
    if (state.running) return;
    state.running = true;
    state.lastFrameT = null;
    state.rafId = requestAnimationFrame(frame);
  }

  function stopLoop() {
    if (!state.running) return;
    state.running = false;
    if (state.rafId) cancelAnimationFrame(state.rafId);
    state.rafId = null;
    state.lastFrameT = null;
    // ringAngle / per-card --depth values are left exactly as they were --
    // nothing snaps back, because no other rule overrides them meanwhile.
  }

  function syncRotation() {
    var allowed = !state.isMobile && !motionReduced() && state.onscreen;
    var paused = state.pointerInside || state.focusInside || document.hidden;
    if (allowed && !paused) startLoop();
    else stopLoop();
  }

  /* ── POINTER-RESPONSIVE SHEEN — only the hovered card, rAF-batched ── */

  var sheenPending = null; // { el, px, py } | null
  var sheenRafId = null;

  function flushSheen() {
    sheenRafId = null;
    if (!sheenPending) return;
    sheenPending.el.style.setProperty('--px', sheenPending.px + '%');
    sheenPending.el.style.setProperty('--py', sheenPending.py + '%');
    sheenPending = null;
  }

  function onCardPointerMove(e) {
    if (mobileMQ.matches) return; // disabled on touch-first / narrow layouts
    var el = e.currentTarget;
    var r = el.getBoundingClientRect(); // one read, only on an actual pointer move on the hovered card
    var px = ((e.clientX - r.left) / r.width) * 100;
    var py = ((e.clientY - r.top) / r.height) * 100;
    sheenPending = { el: el, px: px.toFixed(1), py: py.toFixed(1) };
    if (sheenRafId == null) sheenRafId = requestAnimationFrame(flushSheen);
  }

  /* ── EVENTS ───────────────────────────────────────────────────────── */

  function attachEvents() {
    viewport.addEventListener('mouseenter', function () {
      state.pointerInside = true; syncRotation();
    });
    viewport.addEventListener('mouseleave', function () {
      state.pointerInside = false; syncRotation();
    });
    ring.addEventListener('focusin', function () {
      state.focusInside = true; syncRotation();
    });
    ring.addEventListener('focusout', function (e) {
      if (!ring.contains(e.relatedTarget)) { state.focusInside = false; syncRotation(); }
    });

    document.addEventListener('visibilitychange', syncRotation);
    reduceMQ.addEventListener('change', syncRotation);

    var resizeT;
    window.addEventListener('resize', function () {
      clearTimeout(resizeT);
      resizeT = setTimeout(function () {
        state.isMobile = mobileMQ.matches;
        syncRotation();
      }, 150);
    });

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        state.onscreen = entry.isIntersecting;
        syncRotation();
      });
    }, { threshold: 0.1 });
    io.observe(galleryRoot);
  }

  /* ── INIT (with a safe fallback if anything goes wrong) ────────────── */

  function init() {
    if (!clients.length) {
      // Nothing to show -- leave the semantic fallback list visible rather
      // than mount an empty rotating stage.
      console.warn('[NRC] Selected Work gallery: no client entries found; showing the static list instead.');
      return;
    }

    galleryRoot = root.querySelector('.work-gallery');
    viewport = document.getElementById('work-gallery-viewport');
    ring = document.getElementById('work-gallery-ring');
    mobileTrack = document.getElementById('work-gallery-mobile');
    announcer = document.getElementById('work-gallery-announcer');
    if (!galleryRoot || !viewport || !ring || !mobileTrack) {
      throw new Error('required gallery elements missing from markup');
    }

    render();
    state.isMobile = mobileMQ.matches;
    attachEvents();

    galleryRoot.hidden = false;
    state.onscreen = false; // corrected by the IntersectionObserver's first callback
    syncRotation();
  }

  try {
    init();
  } catch (err) {
    // Fail safe: never leave the section empty. The semantic list is
    // still in the DOM and unhidden (html.js only hides it once the
    // gallery mounts successfully -- see the CSS guard below), so a
    // thrown error here simply means visitors keep seeing that list.
    console.warn('[NRC] Selected Work gallery failed to initialize; showing the static list instead.', err);
    if (galleryRoot) galleryRoot.hidden = true;
    document.documentElement.classList.add('work-gallery-failed');
  }
})();
