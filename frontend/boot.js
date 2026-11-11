// Applies the saved theme and menu state before first paint, so the page never flashes the wrong one.
// (A separate file rather than an inline script, so the Content-Security-Policy can forbid inline scripts.)
try {
  var t = localStorage.getItem("jf-theme");
  if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  if (localStorage.getItem("jf-rail") === "collapsed") document.documentElement.setAttribute("data-rail", "collapsed");
} catch (e) { /* storage blocked: use the defaults */ }
