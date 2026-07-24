(function () {
  var KEY = "devlog-theme";

  function current() {
    var t = document.documentElement.getAttribute("data-theme");
    return t === "dark" ? "dark" : "light";
  }

  function apply(theme) {
    var next = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(KEY, next);
    } catch (_) {}
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      var label = next === "dark" ? "Light" : "Dark";
      btn.setAttribute("aria-label", "Switch to " + label.toLowerCase() + " theme");
      var text = btn.querySelector(".label");
      if (text) text.textContent = label;
    }
  }

  function toggle() {
    apply(current() === "dark" ? "light" : "dark");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", toggle);
    apply(current());
  });

  window.__devlogTheme = { apply: apply, toggle: toggle, current: current };
})();
