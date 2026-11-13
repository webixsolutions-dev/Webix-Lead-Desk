"use strict";
// Local presentation only: never saves configuration or triggers a provider action.
const settingsWorkspace = document.querySelector('[data-settings-workspace]');
const formError = document.querySelector('[data-form-error]');
if (formError && typeof formError.showModal === 'function') {
  formError.removeAttribute('open');
  formError.showModal();
}
if (settingsWorkspace) {
  const panels = [...settingsWorkspace.querySelectorAll('[data-settings-panel]')];
  const links = [...settingsWorkspace.querySelectorAll('.settings-index a')];
  let activeSettingsPanel = null;
  const showSettings = () => {
    let target;
    try { target = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch { /* Keep the default section for malformed fragments. */ }
    const fallback = location.pathname === '/settings/linkedin' ? 'linkedin' : new URLSearchParams(location.search).has('scope') ? 'content' : settingsWorkspace.dataset.defaultSection || 'sending';
    const selected = target?.closest('[data-settings-panel]') || activeSettingsPanel || document.getElementById(fallback);
    activeSettingsPanel = selected;
    panels.forEach(panel => { panel.hidden = panel !== selected; if (panel === selected) panel.open = true; });
    links.forEach(link => {
      if (link.hash === '#' + selected.id) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  };
  settingsWorkspace.classList.add('settings-enhanced');
  showSettings();
  window.addEventListener('hashchange', showSettings);
  // Select before the existing deep-link handler scrolls to nested sections.
  document.addEventListener('click', event => {
    const link = event.target.closest('.settings-index a');
    if (!link) return;
    const selected = document.getElementById(link.hash.slice(1));
    panels.forEach(panel => { panel.hidden = panel !== selected; });
  }, true);
  document.addEventListener('invalid', event => {
    const selected = event.target.closest('[data-settings-panel]');
    if (selected) { location.hash = selected.id; showSettings(); }
  }, true);
}
const campaignSearch = document.getElementById('campaign-search');
if (campaignSearch) {
  const market = document.getElementById('campaign-market');
  const status = document.getElementById('campaign-status');
  const rows = [...document.querySelectorAll('[data-campaign-row]')];
  const filterCampaigns = () => {
    const query = campaignSearch.value.toLocaleLowerCase().trim();
    let count = 0;
    rows.forEach(row => {
      const visible = row.dataset.campaignSearch.toLocaleLowerCase().includes(query) && (!market.value || row.dataset.market === market.value) && (!status.value || row.dataset.status === status.value);
      row.hidden = !visible;
      if (visible) count++;
    });
    document.getElementById('campaign-filter-count').textContent = `${count} of ${rows.length} campaigns`;
    document.getElementById('campaign-no-matches').hidden = count > 0 || rows.length === 0;
  };
  campaignSearch.addEventListener('input', filterCampaigns);
  market.addEventListener('change', filterCampaigns);
  status.addEventListener('change', filterCampaigns);
  document.getElementById('clear-campaign-filters').addEventListener('click', () => {
    campaignSearch.value = ''; market.value = ''; status.value = ''; filterCampaigns(); campaignSearch.focus();
  });
  window.addEventListener('hashchange', () => {
    const row = document.getElementById(location.hash.slice(1))?.closest('[data-campaign-row]');
    if (row?.hidden) { campaignSearch.value = ''; market.value = ''; status.value = ''; filterCampaigns(); }
  });
}
