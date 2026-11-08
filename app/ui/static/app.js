"use strict";

const navigationToggle = document.getElementById("nav-toggle");
const sidebar = document.getElementById("sidebar");
if (navigationToggle && sidebar) {
  navigationToggle.addEventListener("click", () => {
    const expanded = navigationToggle.getAttribute("aria-expanded") === "true";
    navigationToggle.setAttribute("aria-expanded", String(!expanded));
    navigationToggle.setAttribute("aria-label", expanded ? "Open navigation" : "Close navigation");
    sidebar.classList.toggle("is-open", !expanded);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      sidebar.classList.remove("is-open");
      navigationToggle.setAttribute("aria-expanded", "false");
      navigationToggle.setAttribute("aria-label", "Open navigation");
      navigationToggle.focus();
    }
  });
}

const leadFilter = document.getElementById("lead-filter");
if (leadFilter) {
  leadFilter.addEventListener("input", () => {
    const query = leadFilter.value.toLocaleLowerCase().trim();
    let visible = 0;
    document.querySelectorAll("[data-search]").forEach((card) => {
      const matches = (card.dataset.search || "").toLocaleLowerCase().includes(query);
      card.hidden = !matches;
      if (matches) visible += 1;
    });
    document.getElementById("no-matches").hidden = visible > 0;
  });
}

