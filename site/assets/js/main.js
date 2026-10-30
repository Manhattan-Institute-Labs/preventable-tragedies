/* ==========================================================================
   Preventable Tragedies — site behavior (vanilla JS, no dependencies).
   Each init function checks for its own markup, so this one file is safe
   to load on every page.
   ========================================================================== */
(function () {
  'use strict';

  document.documentElement.classList.add('js');

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  var STORIES = window.STORIES || [];
  var CATEGORIES = window.CATEGORIES || {};

  /* ---------- Template filling ----------
     Markup declares what goes where:
       data-field="headline"        → textContent
       data-field="source.outlet"   → nested lookup
       data-field="category"        → human labels from CATEGORIES (a story
                                      can carry several: "A · B")
       data-field="place"           → "City, ST"
       data-href="source.url"       → <a href>
       data-photo                   → slot holding the placeholder SVG;
                                      replaced with <img> when story.photo is set */
  function get(obj, path) {
    return path.split('.').reduce(function (o, k) { return o == null ? o : o[k]; }, obj);
  }

  function fill(root, story) {
    $$('[data-field]', root).forEach(function (el) {
      var key = el.getAttribute('data-field');
      var val;
      if (key === 'category') val = CATEGORIES[story.category] || story.category;
      else if (key === 'place') val = [story.city, story.state].filter(Boolean).join(', ');
      else val = get(story, key);
      if (val != null) el.textContent = val;
    });
    $$('[data-href]', root).forEach(function (a) {
      var v = get(story, a.getAttribute('data-href'));
      if (v) a.href = v;
    });
    $$('[data-photo]', root).forEach(function (slot) {
      if (!slot._placeholder && slot.firstElementChild) slot._placeholder = slot.firstElementChild.cloneNode(true);
      if (story.photo) {
        var img = document.createElement('img');
        img.src = story.photo;
        img.alt = 'Booking photo of ' + story.name;
        img.className = 'photo';
        slot.replaceChildren(img);
      } else if (slot._placeholder) {
        slot.replaceChildren(slot._placeholder.cloneNode(true));
      }
    });
  }

  /* ---------- Landing: random case from the database ---------- */
  function initRandomCase() {
    var card = $('#random-case');
    if (!card || !STORIES.length) return;
    var featured = STORIES.filter(function (s) { return s.featured; });
    var pool = featured.length ? featured : STORIES;
    var current = -1;

    function show() {
      var i;
      do { i = Math.floor(Math.random() * pool.length); } while (pool.length > 1 && i === current);
      current = i;
      fill(card, pool[i]);
      var num = $('#random-case-num');
      if (num) num.textContent = String(STORIES.indexOf(pool[i]) + 1);
    }

    show();
    var btn = $('#random-case-next');
    if (btn) btn.addEventListener('click', show);
  }

  /* ---------- Email sign-up (front end only) ---------- */
  function initSignup() {
    $$('form[data-signup]').forEach(function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var email = form.querySelector('input[type="email"]');
        if (email && !email.checkValidity()) { email.reportValidity(); return; }
        // TODO: send to the email provider (e.g. POST form data to its API).
        var confirmEl = form.parentElement.querySelector('[data-signup-confirm]');
        form.hidden = true;
        if (confirmEl) { confirmEl.hidden = false; confirmEl.focus(); }
      });
    });
  }

  /* ---------- Stories index: filter, search, sort, load more ---------- */
  function initIndex() {
    var grid = $('#case-grid');
    var tpl = $('#story-card-template');
    if (!grid || !tpl) return;

    var chipsWrap = $('#category-chips');
    var search = $('#case-search');
    var sort = $('#case-sort');
    var countEl = $('#case-count');
    var totalEl = $('#case-total');
    var empty = $('#case-empty');
    var more = $('#load-more');
    var PAGE = 9;

    var state = { cat: 'all', q: '', sort: 'recent', limit: PAGE };
    var initialCat = new URLSearchParams(location.search).get('category');
    if (initialCat && CATEGORIES[initialCat]) state.cat = initialCat;

    var cats = [['all', 'All cases']].concat(Object.keys(CATEGORIES).map(function (k) { return [k, CATEGORIES[k]]; }));
    cats.forEach(function (c) {
      var id = c[0], label = c[1];
      var n = id === 'all' ? STORIES.length : STORIES.filter(function (s) { return s.category === id; }).length;
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.setAttribute('data-cat', id);
      b.textContent = label + ' (' + n + ')';
      b.addEventListener('click', function () { state.cat = id; state.limit = PAGE; render(); });
      chipsWrap.appendChild(b);
    });

    if (search) search.addEventListener('input', function () { state.q = search.value.trim().toLowerCase(); state.limit = PAGE; render(); });
    if (sort) sort.addEventListener('change', function () { state.sort = sort.value; render(); });
    if (more) more.addEventListener('click', function () { state.limit += PAGE; render(); });

    function num(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }
    var sorters = {
      recent: function (a, b) { return String(b.date).localeCompare(String(a.date)); },
      arrests: function (a, b) { return num(b.priorArrests) - num(a.priorArrests); },
      state: function (a, b) { return String(a.state).localeCompare(String(b.state)); }
    };

    function matchesQuery(s) {
      if (!state.q) return true;
      return [s.name, s.city, s.state, s.headline, s.summary].join(' ').toLowerCase().indexOf(state.q) !== -1;
    }

    function render() {
      $$('.chip', chipsWrap).forEach(function (b) {
        b.setAttribute('aria-pressed', String(b.getAttribute('data-cat') === state.cat));
      });
      var matches = STORIES
        .filter(function (s) { return (state.cat === 'all' || s.category === state.cat) && matchesQuery(s); })
        .sort(sorters[state.sort] || sorters.recent);
      var shown = matches.slice(0, state.limit);
      grid.replaceChildren.apply(grid, shown.map(function (s) {
        var node = tpl.content.firstElementChild.cloneNode(true);
        fill(node, s);
        return node;
      }));
      if (countEl) countEl.textContent = String(shown.length);
      if (totalEl) totalEl.textContent = String(matches.length);
      if (empty) empty.hidden = matches.length !== 0;
      if (more) more.hidden = matches.length <= state.limit;
    }

    render();
  }

  /* ---------- Facts: scrollytelling ----------
     .scrolly contains .step[data-step] blocks and chart layers [data-hl].
     The step in the middle of the viewport sets .scrolly[data-active]. */
  function initScrolly() {
    if (!('IntersectionObserver' in window)) return;
    $$('.scrolly').forEach(function (sc) {
      var steps = $$('.step[data-step]', sc);
      if (!steps.length) return;
      function activate(step) {
        sc.setAttribute('data-active', step.getAttribute('data-step'));
        steps.forEach(function (s) { s.classList.toggle('is-active', s === step); });
      }
      activate(steps[0]);
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) activate(e.target); });
      }, { rootMargin: '-45% 0px -45% 0px' });
      steps.forEach(function (s) { io.observe(s); });
    });
  }

  function init() {
    initRandomCase();
    initSignup();
    initIndex();
    initScrolly();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
