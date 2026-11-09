"use strict";

const navigationToggle = document.getElementById("nav-toggle");
const sidebar = document.getElementById("sidebar");
const navigationBackdrop = document.getElementById("nav-backdrop");
const navigationClose = document.getElementById("nav-close");
if (navigationToggle && sidebar) {
  const setNavigation = (open) => {
    navigationToggle.setAttribute("aria-expanded", String(open));
    navigationToggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    sidebar.classList.toggle("is-open", open);
    document.body.classList.toggle("nav-open", open);
    if (navigationBackdrop) navigationBackdrop.hidden = !open;
    if (open) (navigationClose || sidebar.querySelector("a")).focus();
    else navigationToggle.focus();
  };
  navigationToggle.addEventListener("click", () => setNavigation(navigationToggle.getAttribute("aria-expanded") !== "true"));
  navigationClose?.addEventListener("click", () => setNavigation(false));
  navigationBackdrop?.addEventListener("click", () => setNavigation(false));
  document.addEventListener("keydown", (event) => {
    if (!sidebar.classList.contains("is-open")) return;
    if (event.key === "Escape") setNavigation(false);
    if (event.key === "Tab") {
      const items = [...sidebar.querySelectorAll('a, button:not([disabled])')].filter((item) => item.getClientRects().length);
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  window.matchMedia("(min-width: 721px)").addEventListener("change", (event) => {
    if (event.matches && sidebar.classList.contains("is-open")) {
      sidebar.classList.remove("is-open");
      document.body.classList.remove("nav-open");
      navigationToggle.setAttribute("aria-expanded", "false");
      navigationToggle.setAttribute("aria-label", "Open navigation");
      if (navigationBackdrop) navigationBackdrop.hidden = true;
    }
  });
}

// Deep links open the relevant section and its parent groups, including nested forms.
const revealSection = (target) => {
  let node = target;
  while (node) {
    if (node.tagName === "DETAILS") node.open = true;
    node = node.parentElement;
  }
};
const revealHash = () => {
  let id;
  try { id = decodeURIComponent(window.location.hash.slice(1)); } catch { return; }
  const target = document.getElementById(id);
  if (!target) return;
  revealSection(target);
  target.scrollIntoView({ block: "start" });
};
window.addEventListener("hashchange", revealHash);
revealHash();
document.addEventListener("click", (event) => {
  const link = event.target.closest('a[href^="#"]');
  if (!link || link.getAttribute("href") === "#") return;
  const target = document.getElementById(link.getAttribute("href").slice(1));
  if (target) { revealSection(target); target.scrollIntoView({ block: "start" }); }
});
document.addEventListener("invalid", (event) => revealSection(event.target), true);

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

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy-target]');
  if (!button) return;
  const field = document.getElementById(button.dataset.copyTarget);
  const status = button.nextElementSibling;
  if (!field) return;
  try { await navigator.clipboard.writeText(field.value); status.textContent = 'Copied'; }
  catch { field.focus(); field.select(); status.textContent = 'Select and copy the text manually'; }
});
