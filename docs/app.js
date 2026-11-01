(function () {
  "use strict";
  var bots = JSON.parse(document.getElementById("bot-options").textContent);
  document.querySelectorAll("select[id^=bot-]").forEach(function (sel) {
    bots.forEach(function (b) {
      var o = document.createElement("option");
      o.value = b.bot; o.textContent = b.name; sel.appendChild(o);
    });
  });
  document.querySelectorAll(".searchbar").forEach(function (bar) {
    var page = bar.id ? bar.id : bar.querySelector("input").id.replace(/^q-/, "");
    var q = bar.querySelector("input"), grp = bar.querySelector("select[id^=grp-]"),
        bot = bar.querySelector("select[id^=bot-]"), nr = bar.querySelector(".noresults");
    function apply() {
      var term = (q.value || "").toLowerCase(), g = grp.value, b = bot.value, shown = 0;
      document.querySelectorAll("article.card").forEach(function (c) {
        var ok = (!term || (c.getAttribute("data-text") || "").indexOf(term) !== -1)
              && (!g || c.getAttribute("data-group") === g)
              && (!b || c.getAttribute("data-bot") === b);
        c.hidden = !ok; if (ok) shown++;
      });
      document.querySelectorAll(".day-label").forEach(function (l) {
        var el = l.nextElementSibling, any = false;
        while (el && !el.classList.contains("day-label")) {
          if (el.tagName === "ARTICLE" && !el.hidden) { any = true; break; }
          el = el.nextElementSibling;
        }
        l.hidden = !any;
      });
      nr.style.display = shown === 0 ? "block" : "none";
    }
    [q, grp, bot].forEach(function (el) { el.addEventListener("input", apply); });
  });
})();
