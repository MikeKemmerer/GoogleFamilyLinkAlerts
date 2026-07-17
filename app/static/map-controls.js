/* Shared Leaflet control helpers for the self-hosted location maps
 * (Status page device/child maps, History page location-history map).
 * No CDN, no charting/UI library -- just a small custom Leaflet control. */
function addRecenterControl(map, recenterFn) {
  var control = L.control({ position: "topleft" });
  control.onAdd = function () {
    var container = L.DomUtil.create("div", "leaflet-bar leaflet-control leaflet-recenter-control");
    var link = L.DomUtil.create("a", "", container);
    link.href = "#";
    link.title = "Re-center map";
    link.setAttribute("role", "button");
    link.setAttribute("aria-label", "Re-center map");
    link.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#icon-map-pin"></use></svg>';
    L.DomEvent.on(link, "click", function (event) {
      L.DomEvent.stop(event);
      recenterFn();
    });
    // Leaflet controls swallow click-drag so the map underneath doesn't
    // pan while interacting with the button.
    L.DomEvent.disableClickPropagation(container);
    return container;
  };
  control.addTo(map);
}
