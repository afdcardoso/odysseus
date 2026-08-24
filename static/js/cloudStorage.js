import uiModule from './ui.js';
import { makeWindowDraggable } from './windowDrag.js';
import * as Modals from './modalManager.js';

let _open = false;
let _source = null;
let _path = '';
let _pageToken = '';
let _files = [];

function esc(value) {
  return uiModule.esc(value);
}

function closeCloudStorage() {
  if (!_open && !Modals.isMinimized('cloud-storage-modal')) return;
  if (Modals.isRegistered('cloud-storage-modal')) {
    Modals.close('cloud-storage-modal');
    return;
  }
  _teardown();
}

function _teardown() {
  _open = false;
  _source = null;
  _path = '';
  _pageToken = '';
  _files = [];
  document.getElementById('tool-cloud-storage-btn')?.classList.remove('active');
  document.getElementById('cloud-storage-modal')?.remove();
}

async function _loadSources(modal) {
  const select = modal.querySelector('#cloud-storage-source');
  const browser = modal.querySelector('#cloud-storage-browser');
  try {
    const response = await fetch('/api/file-sources', { credentials: 'same-origin' });
    const data = response.ok ? await response.json() : { sources: [] };
    const sources = (data.sources || []).filter(source => source.provider === 'google_drive' || source.provider === 'webdav');
    select.innerHTML = sources.map(source => `<option value="${esc(source.id)}">${esc(source.display_name || source.provider)}</option>`).join('');
    if (!sources.length) {
      _source = null;
      browser.innerHTML = '<div class="admin-empty">No cloud storage sources connected.</div>';
      return;
    }
    _source = sources[0];
    await _browse(modal);
  } catch (_) {
    browser.innerHTML = '<div style="color:var(--red);font-size:12px;padding:8px 0">Could not load cloud storage sources.</div>';
  }
}

async function _browse(modal, path = '', pageToken = '') {
  if (!_source) return;
  const browser = modal.querySelector('#cloud-storage-browser');
  const message = modal.querySelector('#cloud-storage-message');
  _path = path;
  _pageToken = pageToken;
  browser.innerHTML = '<div style="font-size:12px;opacity:.6;padding:12px 0">Loading files...</div>';
  try {
    const query = new URLSearchParams({ path, page_token: pageToken });
    const response = await fetch(`/api/file-sources/${encodeURIComponent(_source.id)}/browse?${query}`, { credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Could not browse files');
    _files = data.files || [];
    const folders = _files.filter(file => file.is_folder);
    const files = _files.filter(file => !file.is_folder);
    browser.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:10px;">
        <button type="button" class="admin-btn-sm" id="cloud-storage-root">Root</button>
        ${_path ? '<span style="opacity:.45">/</span><span style="font-size:11px;opacity:.7">Current folder</span>' : ''}
        <button type="button" class="admin-btn-sm" id="cloud-storage-import" style="margin-left:auto">Import selected</button>
        ${data.next_page_token ? '<button type="button" class="admin-btn-sm" id="cloud-storage-next">More</button>' : ''}
      </div>
      <div style="display:flex;flex-direction:column;gap:2px;">
        ${folders.map((file, index) => `<button type="button" class="admin-btn-sm" data-cloud-folder="${index}" style="text-align:left;justify-content:flex-start">${esc(file.name)}</button>`).join('')}
        ${files.map((file, index) => `<label style="display:flex;align-items:center;gap:7px;font-size:12px;padding:5px 2px"><input type="checkbox" data-cloud-file="${index}"> <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(file.name)}</span></label>`).join('')}
        ${!folders.length && !files.length ? '<span style="font-size:12px;opacity:.55;padding:8px 0">This folder is empty.</span>' : ''}
      </div>`;
    modal.querySelector('#cloud-storage-root')?.addEventListener('click', () => _browse(modal));
    modal.querySelector('#cloud-storage-next')?.addEventListener('click', () => _browse(modal, _path, data.next_page_token));
    modal.querySelectorAll('[data-cloud-folder]').forEach(button => {
      button.addEventListener('click', () => _browse(modal, folders[Number(button.dataset.cloudFolder)].path));
    });
    modal.querySelector('#cloud-storage-import')?.addEventListener('click', async () => {
      const selected = [...modal.querySelectorAll('[data-cloud-file]:checked')].map(input => files[Number(input.dataset.cloudFile)]);
      if (!selected.length) {
        message.textContent = 'Select one or more files first.';
        message.style.color = 'var(--red)';
        return;
      }
      message.textContent = 'Importing and indexing...';
      message.style.color = '';
      const result = await fetch(`/api/file-sources/${encodeURIComponent(_source.id)}/import`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files: selected }),
      });
      const body = await result.json().catch(() => ({}));
      message.textContent = result.ok ? `Imported ${body.imported_count || 0} file(s).` : (body.detail || 'Import failed');
      message.style.color = result.ok ? 'var(--green,#50fa7b)' : 'var(--red)';
    });
  } catch (error) {
    browser.innerHTML = '';
    message.textContent = error.message || 'Could not browse files';
    message.style.color = 'var(--red)';
  }
}

export function openCloudStorage() {
  if (Modals.isRegistered('cloud-storage-modal') && Modals.isMinimized('cloud-storage-modal')) {
    Modals.restore('cloud-storage-modal');
    return;
  }
  if (_open) return;
  _open = true;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'cloud-storage-modal';
  modal.innerHTML = `
    <div class="modal-content" style="max-width:720px;min-height:360px;">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M17.5 19H9a7 7 0 1 1 6.71-9H17a5 5 0 0 1 .5 9Z"/><path d="M12 12v6"/><path d="m9 15 3 3 3-3"/></svg>Cloud Storage</h4>
        <button class="modal-close" id="cloud-storage-close" aria-label="Close Cloud Storage">&times;</button>
      </div>
      <div style="padding:10px 14px 14px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
          <label for="cloud-storage-source" style="font-size:12px;opacity:.7">Source</label>
          <select id="cloud-storage-source" class="settings-select" style="flex:1;min-width:0"></select>
          <button type="button" class="admin-btn-sm" id="cloud-storage-connect">Connect source</button>
        </div>
        <div id="cloud-storage-message" style="font-size:11px;min-height:16px;margin-bottom:4px"></div>
        <div id="cloud-storage-browser"></div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  const content = modal.querySelector('.modal-content');
  makeWindowDraggable(modal, { content, header: modal.querySelector('.modal-header') });
  Modals.register('cloud-storage-modal', {
    railBtnId: 'rail-cloud-storage',
    sidebarBtnId: 'tool-cloud-storage-btn',
    closeFn: _teardown,
    restoreFn: () => { modal.style.display = ''; },
  });
  modal.querySelector('#cloud-storage-close').addEventListener('click', closeCloudStorage);
  modal.querySelector('#cloud-storage-source').addEventListener('change', event => {
    _source = { id: event.target.value };
    _browse(modal);
  });
  modal.querySelector('#cloud-storage-connect').addEventListener('click', () => {
    import('./settings.js').then(settings => settings.open('integrations')).catch(() => {});
  });
  document.getElementById('tool-cloud-storage-btn')?.classList.add('active');
  _loadSources(modal);
}

export function isCloudStorageOpen() {
  return _open;
}

export function closeCloudStorageWindow() {
  closeCloudStorage();
}

const cloudStorageModule = { openCloudStorage, isCloudStorageOpen, closeCloudStorage: closeCloudStorageWindow };
export default cloudStorageModule;
