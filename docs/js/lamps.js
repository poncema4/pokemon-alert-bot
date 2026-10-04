/* Fills the status lamps in the top bar on pages that do not load the map script (route, 30th guide). */
(function () {
  const C = window.PokeCommon;
  const el = document.getElementById("lamps");
  if (!el) return;
  async function draw() {
    let health = {};
    try { health = await (await fetch("health.json?ts=" + Date.now())).json(); } catch (e) { /* no health file yet */ }
    el.innerHTML = C.lampsHtml(health, Date.now());
  }
  draw();
  setInterval(draw, 30000);
})();
