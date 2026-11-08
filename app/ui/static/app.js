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


const csvFile = document.getElementById('csv-file');
if (csvFile) csvFile.addEventListener('change', async () => {
  const file = csvFile.files[0];
  const error = document.getElementById('csv-error');
  if (!file) return;
  if (file.size > 1000000) { error.textContent = 'Choose a CSV smaller than 1 MB.'; return; }
  try { document.getElementById('csv-text').value = await file.text(); error.textContent = ''; }
  catch { error.textContent = 'Could not read this file. Paste its CSV contents instead.'; }
});
