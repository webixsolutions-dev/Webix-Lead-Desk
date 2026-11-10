(() => {
  'use strict';
  const panel = document.querySelector('#review-panel');
  if (!panel) return;
  const content = panel.querySelector('#review-panel-content');
  const status = panel.querySelector('#panel-status');
  let trigger = null, currentKey = '', busy = false, changed = false;
  const items = () => [...document.querySelectorAll('main [data-review-item]')];
  const safeURL = href => {
    const url = new URL(href, location.href);
    return url.origin === location.origin ? url : null;
  };
  const refreshQueue = async () => {
    if (!document.querySelector('#work-queue')) return;
    try {
      const response = await fetch('/', {credentials: 'same-origin'});
      if (!response.ok || new URL(response.url).pathname === '/login') return;
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const queue = doc.querySelector('#work-queue');
      if (queue) document.querySelector('#work-queue').replaceChildren(...queue.childNodes);
    } catch (_) { /* Keep existing queue; never retry a mutation. */ }
  };
  async function load(href, options = {}) {
    const url = safeURL(href);
    if (!url || busy) return;
    busy = true;
    status.textContent = options.method ? 'Saving…' : 'Loading…';
    content.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(url, {credentials: 'same-origin', ...options});
      if (new URL(response.url).pathname === '/login') { location.assign('/login'); return; }
      const text = await response.text();
      if (!response.headers.get('content-type')?.includes('text/html')) {
        let detail = 'Unable to complete this action.';
        try { detail = JSON.parse(text).detail || detail; } catch (_) { /* No untrusted HTML. */ }
        status.textContent = String(detail);
        return;
      }
      const doc = new DOMParser().parseFromString(text, 'text/html');
      const main = doc.querySelector('#main-content');
      if (!main) { status.textContent = 'This view needs the full page. Close the panel to continue.'; return; }
      // Scripts are never executed from fetched pages; use the existing server's escaped content.
      main.querySelectorAll('script').forEach(node => node.remove());
      main.querySelectorAll('form').forEach(form => {
        if (!form.hasAttribute('action')) form.setAttribute('action', response.url);
      });
      content.replaceChildren(...main.childNodes);
      status.textContent = response.ok ? (options.method ? 'Saved. Review the updated record below.' : '') : 'Review the errors below.';
      changed = false;
      const hash = new URL(response.url).hash;
      if (hash) {
        const target = [...content.querySelectorAll('[id]')].find(n => '#' + n.id === hash);
        if (target) { let parent = target; while (parent && parent !== content) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; } }
      }
      content.scrollTop = 0;
      if (options.method && response.ok) await refreshQueue();
    } catch (_) {
      status.textContent = options.method ? 'The result could not be confirmed. Check the record before submitting again.' : 'Could not load this view. Try opening the full page.';
    } finally { busy = false; content.removeAttribute('aria-busy'); }
  }
  function open(link) {
    trigger = link;
    currentKey = link.dataset.reviewItem || '';
    if (!panel.open) panel.showModal();
    load(link.href);
  }
  function move(direction) {
    if (busy) return;
    if (changed) { status.textContent = 'Save or reload your edits before moving to another item.'; return; }
    const list = items();
    const index = list.findIndex(n => n.dataset.reviewItem === currentKey);
    const next = list[index + direction] || (index < 0 ? list[0] : null);
    if (next) open(next); else status.textContent = 'No more items in this direction.';
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-review-item]');
    if (!link || panel.contains(link) || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault(); open(link);
  });
  document.querySelector('#start-day')?.addEventListener('click', () => { if (items()[0]) open(items()[0]); });
  panel.querySelector('[data-panel-close]').addEventListener('click', () => panel.close());
  panel.querySelector('[data-panel-next]').addEventListener('click', () => move(1));
  panel.querySelector('[data-panel-previous]').addEventListener('click', () => move(-1));
  panel.addEventListener('close', () => { if (trigger?.isConnected) trigger.focus(); });
  content.addEventListener('input', () => { changed = true; });
  content.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link || link.target === '_blank' || link.hasAttribute('download') || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const url = safeURL(link.href);
    if (!url || link.getAttribute('href').startsWith('#') || /\/(?:download|export|preview)(?:\/|$)/.test(url.pathname)) return;
    event.preventDefault();
    if (changed) { status.textContent = 'Save your edits before opening another view.'; return; }
    load(url);
  });
  content.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    event.preventDefault();
    const button = event.submitter;
    if (busy || (!button?.formNoValidate && !form.reportValidity())) return;
    const action = button?.getAttribute('formaction') || form.action;
    const method = (button?.getAttribute('formmethod') || form.method || 'get').toLowerCase();
    const data = new URLSearchParams();
    for (const [key, value] of new FormData(form, button)) {
      if (typeof value !== 'string') { status.textContent = 'Open the full page for file uploads.'; return; }
      data.append(key, value);
    }
    if (method === 'get') { const url = safeURL(action); if (url) { url.search = data; load(url); } }
    else load(action, {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: data.toString()});
  });
  panel.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || busy || event.target.closest('input,textarea,select,[contenteditable="true"]')) return;
    const key = event.key.toLowerCase();
    if (key === 'j' || key === 'k') { event.preventDefault(); move(key === 'j' ? 1 : -1); }
    if (key === 's') {
      const reason = content.querySelector('[data-skip-reason], form[action$="/reject"] [name="reason"]');
      if (reason) { event.preventDefault(); let p=reason.parentElement; while(p && p!==content) { if(p.tagName==='DETAILS') p.open=true; p=p.parentElement; } reason.focus(); }
    }
    if (key === 'a') {
      const form = content.querySelector('form[action$="/approve"]');
      if (form) { event.preventDefault(); const button=form.querySelector('button[type="submit"],button:not([type])'); form.requestSubmit(button || undefined); }
    }
  });
  // Read-only polling never regenerates drafts or touches an open editor.
  let polling = false;
  setInterval(async () => {
    const target = document.querySelector('#command-activity');
    if (!target || document.hidden || polling) return;
    polling = true;
    try { const response = await fetch('/command/activity', {credentials:'same-origin'}); if (response.ok && new URL(response.url).pathname !== '/login') target.innerHTML = await response.text(); }
    catch (_) { /* Preserve last recorded activity. */ }
    finally { polling = false; }
  }, 20000);
})();
