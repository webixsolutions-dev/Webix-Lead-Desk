'use strict';
async function checked(response) {
  if (!response.ok) { let detail = 'Request failed. Reload and try again.'; try { detail = (await response.json()).detail || detail; } catch (_) {} throw new Error(detail); }
  return response;
}
async function digest(bytes) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(x => x.toString(16).padStart(2, '0')).join('');
}
document.querySelectorAll('[data-media-upload]').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault(); const status = form.querySelector('[data-upload-status]'); const button = form.querySelector('button'); button.disabled = true;
  try {
    const file = form.elements.asset.files[0]; if (!file || file.size > 25000000) throw new Error('Choose a file up to 25 MB.');
    if (!crypto.subtle) throw new Error('Use HTTPS or localhost to verify uploads securely.');
    const csrf = form.elements.csrf_token.value;
    status.textContent = 'Checking file…';
    const data = await file.arrayBuffer(); const hash = await digest(data);
    const body = new URLSearchParams({csrf_token: csrf, profile_id: form.dataset.profile, kind: form.dataset.kind, size: String(file.size), digest: hash});
    const item = await (await checked(await fetch('/linkedin/media/new', {method:'POST', body}))).json();
    for (let at = 0, number = 0; at < data.byteLength; at += item.chunk_bytes, number++) {
      await checked(await fetch(`/linkedin/media/${item.id}/${number}`, {method:'PUT', headers:{'X-CSRF-Token':csrf}, body:data.slice(at, at + item.chunk_bytes)}));
      status.textContent = `Uploading ${Math.min(100, Math.round((at + item.chunk_bytes) / data.byteLength * 100))}%`;
    }
    await checked(await fetch(`/linkedin/media/${item.id}/finish`, {method:'POST', body:new URLSearchParams({csrf_token:csrf})}));
    status.textContent = 'Verified. Reloading your asset list…'; window.location.reload();
  } catch (error) { status.textContent = error.message; button.disabled = false; }
}));
document.querySelectorAll('[data-media-download]').forEach(button => button.addEventListener('click', async () => {
  button.disabled = true;
  try {
    const chunks = []; for (let at = 0, i = 0; at < Number(button.dataset.size); at += 1000000, i++) {
      chunks.push(await (await checked(await fetch(`/linkedin/media/${button.dataset.mediaDownload}/chunk/${i}`))).arrayBuffer());
    }
    const blob = new Blob(chunks, {type:'application/octet-stream'}); const bytes = await blob.arrayBuffer();
    if (await digest(bytes) !== button.dataset.digest) throw new Error('File integrity check failed.');
    const ext = button.dataset.kind === 'video' ? 'mp4' : new Uint8Array(bytes)[0] === 137 ? 'png' : 'jpg';
    const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `approved-post.${ext}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (error) { window.alert(error.message); } finally { button.disabled = false; }
}));
document.querySelectorAll('[data-post-copy]').forEach(button => button.addEventListener('click', async () => {
  const field = document.getElementById(button.dataset.postCopy); field.focus(); field.select();
  try { await navigator.clipboard.writeText(field.value); button.textContent = 'Copied'; } catch (_) { button.textContent = 'Selected — press Ctrl/Cmd+C'; }
}));
