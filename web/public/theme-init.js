/* Applies the stored theme before first paint, avoiding a flash of the wrong
   theme. Kept as an external file (not inline) so the CSP can forbid inline
   scripts entirely. Must stay synchronous and in <head>. */
(function () {
  try {
    var t = localStorage.getItem("conductor_theme");
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  } catch (e) {}
})();
