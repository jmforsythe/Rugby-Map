"""Team and fixture search widgets for Folium/Leaflet maps."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core.json_utils import write_compact_json

SEARCH_SIDECAR_NAME = "search.json"

SEARCH_STYLES = """
.rugby-search-control {
  position: fixed;
  top: 42px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 999;
  width: min(420px, calc(100vw - 24px));
  font-family: 'Barlow', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
.rugby-search-control__input-wrap {
  position: relative;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
  padding: 6px 10px;
}
.rugby-search-control input[type="search"] {
  width: 100%;
  border: 0;
  outline: none;
  font-size: 15px;
  padding: 4px 2px;
  background: transparent;
  box-sizing: border-box;
}
.rugby-search-results {
  margin-top: 4px;
  max-height: 280px;
  overflow-y: auto;
  background: #fff;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}
.rugby-search-results:empty {
  display: none;
}
.rugby-search-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f0f0f0;
}
.rugby-search-item:last-child { border-bottom: 0; }
.rugby-search-item:hover,
.rugby-search-item:focus {
  background: #f5f8ff;
  outline: none;
}
.rugby-search-item__meta {
  font-size: 11px;
  color: #666;
  margin-top: 2px;
}
.rugby-search-item__name {
  font-size: 14px;
  font-weight: 500;
}
.rugby-search-hint {
  padding: 10px 12px;
  color: #888;
  font-size: 13px;
}
html[data-rugby-effective="dark"] .rugby-search-control__input-wrap,
html[data-rugby-effective="dark"] .rugby-search-results {
  background: #16213e;
  border-color: #444;
  color: #e0e0e0;
}
html[data-rugby-effective="dark"] .rugby-search-control input[type="search"] {
  color: #e0e0e0;
}
html[data-rugby-effective="dark"] .rugby-search-item {
  border-bottom-color: #2a2a4a;
}
html[data-rugby-effective="dark"] .rugby-search-item:hover,
html[data-rugby-effective="dark"] .rugby-search-item:focus {
  background: #1e2a45;
}
html[data-rugby-effective="dark"] .rugby-search-item__meta,
html[data-rugby-effective="dark"] .rugby-search-hint {
  color: #aab8d8;
}
@media (max-width: 768px) {
  .rugby-search-control {
    top: calc(var(--rugby-map-chrome-top, 56px) + 8px);
    width: min(280px, calc(100vw - 96px));
  }
  .rugby-search-control input[type="search"] { font-size: 13px; }
}
.matchday-control .rugby-search-control {
  position: static;
  transform: none;
  width: 100%;
  margin-top: 8px;
}
.matchday-control .rugby-search-control__input-wrap {
  box-shadow: none;
}
"""

TEAM_SEARCH_HTML = """
<div class="rugby-search-control" id="rugbyTeamSearchWrap">
  <div class="rugby-search-control__input-wrap">
    <input type="search" id="rugbyTeamSearchInput"
      placeholder="Search teams on this map…" autocomplete="off"
      aria-label="Search teams on this map" aria-controls="rugbyTeamSearchResults"
      aria-expanded="false" />
  </div>
  <div class="rugby-search-results" id="rugbyTeamSearchResults" role="listbox"
    aria-label="Team search results"></div>
</div>
"""

FIXTURE_SEARCH_HTML = """
<div class="rugby-search-control" id="rugbyFixtureSearchWrap">
  <div class="rugby-search-control__input-wrap">
    <input type="search" id="rugbyFixtureSearchInput"
      placeholder="Search fixtures this day…" autocomplete="off"
      aria-label="Search fixtures on the selected date" aria-controls="rugbyFixtureSearchResults"
      aria-expanded="false" />
  </div>
  <div class="rugby-search-results" id="rugbyFixtureSearchResults" role="listbox"
    aria-label="Fixture search results"></div>
</div>
"""

# Shared by team + fixture search: fly in, spiderfy co-located clusters, open popup.
MARKER_CLUSTER_REVEAL_JS = """
  function revealAndOpenMarker(marker, clusterGroup, latLng, refindMarker) {
    var map = findMap();
    if (!map) return;
    var target = latLng || (marker && marker.getLatLng && marker.getLatLng());
    if (!target) return;

    function resolveMarker() {
      if (marker) return marker;
      return refindMarker ? refindMarker() : null;
    }

    function openPopupOnce(mk) {
      if (mk && mk.openPopup) mk.openPopup();
    }

    function spiderfyAndOpen(mk) {
      if (!mk) return;
      if (!clusterGroup || typeof clusterGroup.getVisibleParent !== 'function') {
        openPopupOnce(mk);
        return;
      }
      var parent = clusterGroup.getVisibleParent(mk);
      if (parent && parent !== mk && typeof parent.spiderfy === 'function') {
        var opened = false;
        function done() {
          if (opened) return;
          opened = true;
          openPopupOnce(mk);
        }
        if (clusterGroup.once) clusterGroup.once('spiderfied', done);
        parent.spiderfy();
        setTimeout(done, 280);
        return;
      }
      openPopupOnce(mk);
    }

    var flyZoom = 14;
    if (typeof map.getMaxZoom === 'function') {
      flyZoom = Math.min(flyZoom, map.getMaxZoom());
    }

    map.flyTo(target, flyZoom, { duration: 0.6 });
    map.once('moveend', function() {
      spiderfyAndOpen(resolveMarker());
    });
  }
"""

TEAM_SEARCH_JS = (
    """
(function() {
  var searchIndex = null;
  var debounceTimer = null;
  var activeIdx = -1;

  function findMap() {
    var el = document.querySelector('.folium-map');
    if (!el || !el._leaflet_id) return null;
    var key = Object.keys(window).find(function(k) {
      return k.indexOf('map_') === 0 && window[k] instanceof L.Map;
    });
    return key ? window[key] : null;
  }
"""
    + MARKER_CLUSTER_REVEAL_JS
    + """
  function findMarkerCluster() {
    return Object.keys(window).map(function(k) { return window[k]; }).find(function(obj) {
      return obj && typeof obj.getAllChildMarkers === 'function' &&
        typeof obj.addLayer === 'function' && obj !== findMap();
    }) || null;
  }

  function collectLeafletMarkers(layer, out) {
    if (!layer) return;
    if (layer.getAllChildMarkers) {
      layer.getAllChildMarkers().forEach(function(m) { out.push(m); });
      return;
    }
    if (layer.getLatLng && layer.options && layer.options.itemName) {
      out.push(layer);
      return;
    }
    if (typeof layer.getLayers === 'function') {
      layer.getLayers().forEach(function(child) { collectLeafletMarkers(child, out); });
    }
  }

  function allMapMarkers() {
    var out = [];
    var cluster = findMarkerCluster();
    if (cluster) collectLeafletMarkers(cluster, out);
    return out;
  }

  function markerGroupSuffix(label) {
    return (label || '').replace(/\\s+-\\s+Markers$/i, '').trim();
  }

  function ensureMarkerOverlayVisible(groupName) {
    var map = findMap();
    if (!map || !window.layerControl || !groupName) return;
    var overlays = document.querySelector('.leaflet-control-layers-overlays');
    if (!overlays) return;
    var labels = overlays.querySelectorAll('label');
    for (var i = 0; i < labels.length; i++) {
      var text = (labels[i].textContent || '').replace(/\\s+/g, ' ').trim();
      if (text.indexOf(' - Markers') === -1) continue;
      if (markerGroupSuffix(text) !== groupName) continue;
      var input = labels[i].querySelector('input[type="checkbox"]');
      if (input && !input.checked) input.click();
    }
  }

  function findMarkerForEntry(entry) {
    var target = (entry.n || '').toLowerCase();
    var markers = allMapMarkers();
    var exact = markers.filter(function(m) {
      return (m.options.itemName || '').toLowerCase() === target;
    });
    if (exact.length === 1) return exact[0];
    if (exact.length > 1) {
      return exact.find(function(m) {
        var ll = m.getLatLng();
        return Math.abs(ll.lat - entry.lat) < 0.0001 && Math.abs(ll.lng - entry.lng) < 0.0001;
      }) || exact[0];
    }
    return markers.find(function(m) {
      var ll = m.getLatLng();
      return Math.abs(ll.lat - entry.lat) < 0.0001 && Math.abs(ll.lng - entry.lng) < 0.0001;
    }) || null;
  }

  function focusEntry(entry) {
    var map = findMap();
    if (!map || !entry) return;
    if (entry.g) ensureMarkerOverlayVisible(entry.g);
    function tryFocus() {
      revealAndOpenMarker(
        findMarkerForEntry(entry),
        findMarkerCluster(),
        [entry.lat, entry.lng],
        function() { return findMarkerForEntry(entry); }
      );
    }
    setTimeout(tryFocus, entry.g ? 120 : 0);
  }

  function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  function renderResults(matches) {
    var box = document.getElementById('rugbyTeamSearchResults');
    var input = document.getElementById('rugbyTeamSearchInput');
    if (!box || !input) return;
    activeIdx = -1;
    if (!matches.length) {
      box.innerHTML = '<div class="rugby-search-hint">No matching teams</div>';
      input.setAttribute('aria-expanded', 'true');
      return;
    }
    box.innerHTML = matches.map(function(entry, idx) {
      return '<div class="rugby-search-item" role="option" tabindex="-1" data-idx="' + idx + '">' +
        '<div><div class="rugby-search-item__name">' + escHtml(entry.n) + '</div>' +
        (entry.g ? '<div class="rugby-search-item__meta">' + escHtml(entry.g) + '</div>' : '') +
        '</div></div>';
    }).join('');
    input.setAttribute('aria-expanded', 'true');
    box.querySelectorAll('.rugby-search-item').forEach(function(el) {
      el.addEventListener('click', function() {
        var i = parseInt(el.getAttribute('data-idx'), 10);
        focusEntry(matches[i]);
        box.innerHTML = '';
        input.value = matches[i].n;
        input.setAttribute('aria-expanded', 'false');
      });
    });
  }

  function filterTeams(q) {
    if (!searchIndex) return;
    var query = q.trim().toLowerCase();
    var box = document.getElementById('rugbyTeamSearchResults');
    var input = document.getElementById('rugbyTeamSearchInput');
    if (!box || !input) return;
    if (query.length < 2) {
      box.innerHTML = '';
      input.setAttribute('aria-expanded', 'false');
      return;
    }
    var matches = searchIndex.filter(function(entry) {
      return entry.n.toLowerCase().indexOf(query) !== -1;
    }).slice(0, 25);
    renderResults(matches);
  }

  function loadIndex() {
    if (searchIndex) return Promise.resolve(searchIndex);
    return fetch('search.json').then(function(r) { return r.json(); }).then(function(data) {
      searchIndex = data;
      var input = document.getElementById('rugbyTeamSearchInput');
      if (input && data.length) {
        input.placeholder = 'Search ' + data.length + ' teams on this map…';
      }
      return data;
    });
  }

  function initTeamSearch() {
    var input = document.getElementById('rugbyTeamSearchInput');
    if (!input) return;
    loadIndex().catch(function(err) {
      console.warn('Could not load team search index', err);
    });
    input.addEventListener('input', function() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function() { filterTeams(input.value); }, 150);
    });
    input.addEventListener('keydown', function(e) {
      var items = document.querySelectorAll('#rugbyTeamSearchResults .rugby-search-item');
      if (!items.length) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIdx = Math.min(activeIdx + 1, items.length - 1);
        items[activeIdx].focus();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIdx = Math.max(activeIdx - 1, 0);
        items[activeIdx].focus();
      } else if (e.key === 'Enter' && activeIdx >= 0) {
        e.preventDefault();
        items[activeIdx].click();
      } else if (e.key === 'Escape') {
        document.getElementById('rugbyTeamSearchResults').innerHTML = '';
        input.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('click', function(e) {
      var wrap = document.getElementById('rugbyTeamSearchWrap');
      if (wrap && !wrap.contains(e.target)) {
        var box = document.getElementById('rugbyTeamSearchResults');
        if (box) box.innerHTML = '';
        input.setAttribute('aria-expanded', 'false');
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTeamSearch);
  } else {
    initTeamSearch();
  }
})();
"""
)

FIXTURE_SEARCH_JS = (
    """
(function() {
  var searchIndex = null;
  var debounceTimer = null;

  function selectedDate() {
    var sel = document.getElementById('matchday-select');
    return sel ? sel.value : '';
  }

  function entriesForSelectedDate() {
    if (!searchIndex) return [];
    var d = selectedDate();
    if (!d) return searchIndex;
    return searchIndex.filter(function(entry) { return entry.d === d; });
  }

  function updateFixtureSearchPlaceholder() {
    var input = document.getElementById('rugbyFixtureSearchInput');
    if (!input) return;
    var n = entriesForSelectedDate().length;
    input.placeholder = n
      ? ('Search ' + n.toLocaleString() + ' fixtures this day…')
      : 'Search fixtures this day…';
  }

  function clearFixtureSearchResults() {
    var input = document.getElementById('rugbyFixtureSearchInput');
    var box = document.getElementById('rugbyFixtureSearchResults');
    if (input) {
      input.value = '';
      input.setAttribute('aria-expanded', 'false');
    }
    if (box) box.innerHTML = '';
  }

  function onSelectedDateChange() {
    clearFixtureSearchResults();
    updateFixtureSearchPlaceholder();
  }

  function findMap() {
    var el = document.querySelector('.folium-map');
    if (!el || !el._leaflet_id) return null;
    var key = Object.keys(window).find(function(k) {
      return k.indexOf('map_') === 0 && window[k] instanceof L.Map;
    });
    return key ? window[key] : null;
  }
"""
    + MARKER_CLUSTER_REVEAL_JS
    + """
  function collectLeafletMarkers(layer, out) {
    if (!layer) return;
    if (layer.getAllChildMarkers) {
      layer.getAllChildMarkers().forEach(function(m) { out.push(m); });
      return;
    }
    if (layer.getLatLng && layer.options && layer.options.itemName) {
      out.push(layer);
      return;
    }
    if (typeof layer.getLayers === 'function') {
      layer.getLayers().forEach(function(child) { collectLeafletMarkers(child, out); });
    }
  }

  function allMapMarkers() {
    var out = [];
    var cluster = window[parentClusterVar];
    if (cluster) collectLeafletMarkers(cluster, out);
    return out;
  }

  function ensureTierVisible(tierKey) {
    var map = findMap();
    if (!map || tierKey === undefined || tierKey === null) return;
    var key = String(tierKey);
    if (typeof tierUserVisible !== 'undefined') tierUserVisible[key] = true;
    var proxy = tierProxies && tierProxies[key];
    if (proxy && map && !map.hasLayer(proxy)) map.addLayer(proxy);
    var overlays = document.querySelector('.leaflet-control-layers-overlays');
    if (!overlays || !tierLabels || !tierLabels[key]) return;
    var want = tierLabels[key];
    var labels = overlays.querySelectorAll('label');
    for (var i = 0; i < labels.length; i++) {
      var text = (labels[i].textContent || '').replace(/\\s+/g, ' ').trim();
      if (text !== want) continue;
      var input = labels[i].querySelector('input[type="checkbox"]');
      if (input && !input.checked) input.click();
    }
  }

  function findMarkerForFixture(entry) {
    var label = (entry.label || '').toLowerCase();
    var markers = allMapMarkers();
    var exact = markers.filter(function(m) {
      return (m.options.itemName || '').toLowerCase() === label;
    });
    if (exact.length === 1) return exact[0];
    return markers.find(function(m) {
      var ll = m.getLatLng();
      return Math.abs(ll.lat - entry.lat) < 0.0001 && Math.abs(ll.lng - entry.lng) < 0.0001;
    }) || null;
  }

  function focusFixture(entry) {
    var map = findMap();
    if (!map || !entry) return;
    ensureTierVisible(entry.t);
    function tryFocus() {
      revealAndOpenMarker(
        findMarkerForFixture(entry),
        window[parentClusterVar],
        [entry.lat, entry.lng],
        function() { return findMarkerForFixture(entry); }
      );
    }
    setTimeout(tryFocus, 150);
  }

  function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  }

  function renderResults(matches) {
    var box = document.getElementById('rugbyFixtureSearchResults');
    var input = document.getElementById('rugbyFixtureSearchInput');
    if (!box || !input) return;
    if (!matches.length) {
      box.innerHTML = '<div class="rugby-search-hint">No matching fixtures this day</div>';
      input.setAttribute('aria-expanded', 'true');
      return;
    }
    box.innerHTML = matches.map(function(entry, idx) {
      var meta = entry.time ? escHtml(entry.time) : '';
      return '<div class="rugby-search-item" role="option" tabindex="-1" data-idx="' + idx + '">' +
        '<div><div class="rugby-search-item__name">' + escHtml(entry.label) + '</div>' +
        (meta ? '<div class="rugby-search-item__meta">' + meta + '</div>' : '') +
        '</div></div>';
    }).join('');
    input.setAttribute('aria-expanded', 'true');
    box.querySelectorAll('.rugby-search-item').forEach(function(el) {
      el.addEventListener('click', function() {
        var i = parseInt(el.getAttribute('data-idx'), 10);
        var entry = matches[i];
        box.innerHTML = '';
        input.setAttribute('aria-expanded', 'false');
        focusFixture(entry);
      });
    });
  }

  function fixtureMatches(entry, query) {
    var hay = (entry.h + ' ' + entry.a + ' ' + entry.label).toLowerCase();
    return hay.indexOf(query) !== -1;
  }

  function filterFixtures(q) {
    if (!searchIndex) return;
    var query = q.trim().toLowerCase();
    var box = document.getElementById('rugbyFixtureSearchResults');
    var input = document.getElementById('rugbyFixtureSearchInput');
    if (!box || !input) return;
    if (query.length < 2) {
      box.innerHTML = '';
      input.setAttribute('aria-expanded', 'false');
      return;
    }
    var matches = entriesForSelectedDate().filter(function(entry) {
      return fixtureMatches(entry, query);
    }).slice(0, 25);
    renderResults(matches);
  }

  function loadIndex() {
    if (searchIndex) return Promise.resolve(searchIndex);
    var base = typeof dataBaseUrl !== 'undefined' ? dataBaseUrl : 'data/';
    return fetch(base + 'search.json').then(function(r) { return r.json(); }).then(function(data) {
      searchIndex = data;
      updateFixtureSearchPlaceholder();
      return data;
    });
  }

  function initFixtureSearch() {
    var input = document.getElementById('rugbyFixtureSearchInput');
    if (!input) return;
    loadIndex().catch(function(err) {
      console.warn('Could not load fixture search index', err);
    });
    var sel = document.getElementById('matchday-select');
    if (sel) sel.addEventListener('change', onSelectedDateChange);
    input.addEventListener('input', function() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function() { filterFixtures(input.value); }, 150);
    });
    input.addEventListener('focus', function() {
      function showResults() { filterFixtures(input.value); }
      if (searchIndex) showResults();
      else loadIndex().then(showResults);
    });
    document.addEventListener('click', function(e) {
      var wrap = document.getElementById('rugbyFixtureSearchWrap');
      if (wrap && !wrap.contains(e.target)) {
        var box = document.getElementById('rugbyFixtureSearchResults');
        if (box) box.innerHTML = '';
        input.setAttribute('aria-expanded', 'false');
      }
    });
    window.rugbyRefreshFixtureSearch = onSelectedDateChange;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFixtureSearch);
  } else {
    initFixtureSearch();
  }
})();
"""
)


def team_search_rows(items: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build compact team rows for a map-local ``search.json`` sidecar."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        name = str(it["name"])
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "n": name,
                "lat": round(float(it["latitude"]), 6),
                "lng": round(float(it["longitude"]), 6),
                "g": str(it["group"]),
            }
        )
    rows.sort(key=lambda row: row["n"].lower())
    return rows


def write_team_search_sidecar(output_path: Path, rows: list[dict[str, Any]]) -> None:
    write_compact_json(output_path.parent / SEARCH_SIDECAR_NAME, rows)


def inject_team_search(output_path: Path) -> None:
    """Post-save injection of team search chrome (idempotent)."""
    text = output_path.read_text(encoding="utf-8")
    if 'id="rugbyTeamSearchWrap"' in text:
        return
    style_block = f"<style>{SEARCH_STYLES}</style>"
    if style_block not in text:
        head_end = text.lower().find("</head>")
        if head_end != -1:
            text = text[:head_end] + style_block + text[head_end:]
    payload = TEAM_SEARCH_HTML + f"<script>{TEAM_SEARCH_JS}</script>"
    body_end = text.lower().rfind("</body>")
    if body_end == -1:
        return
    output_path.write_text(text[:body_end] + payload + text[body_end:], encoding="utf-8")
