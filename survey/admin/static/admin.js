/* ============================================================
   林业野外调查即时录入系统 — Admin 管理后台前端
   纯原生 JS，桌面优先，标签面板布局。
   API 用相对路径（APPLICATION_ROOT 处理 /admin/ 前缀）。
   功能：GDB 管理 / 用户管理 / 项目管理 / 预填数据 / 导出
   ============================================================ */

'use strict';

// ── 全局状态 ──
const state = {
  currentUser: null,
  schema: null,
  projects: [],
  users: [],
  gdbFiles: [],
  currentTab: 'gdb',        // 'gdb' | 'users' | 'projects' | 'prefilled'
  // GDB
  selectedGdb: null,
  gdbLayers: [],
  selectedLayer: null,
  layerPreview: null,
  // Prefilled
  prefilledProject: null,
  prefilledTable: null,
  prefilledSubtable: '',
  prefilledRows: [],
  // Members
  memberProject: null,
  members: [],
};

const app = document.getElementById('app');

// ── 工具函数 ──
const qs = (sel, root = document) => root.querySelector(sel);
const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function toast(msg, ms = 2000) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  t.style.bottom = 'auto';
  t.style.top = '20px';
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

async function apiFetch(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = '/forest/login?next=/survey-admin/';
    throw new Error('未登录，正在跳转登录页…');
  }
  return res;
}

async function fetchJSON(url, options) {
  const res = await apiFetch(url, options);
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`;
    try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

function getTableDef(tableId) {
  if (!state.schema) return null;
  return state.schema.tables.find(t => t.id === tableId) || null;
}

// ── 初始化 ──
async function init() {
  renderShell();
  try {
    const me = await fetchJSON('api/me');
    state.currentUser = me;
  } catch (e) {
    toast('加载用户信息失败：' + e.message, 3000);
    return;
  }
  try {
    const [schemaData, projData] = await Promise.all([
      fetchJSON('api/schema'),
      fetchJSON('api/projects'),
    ]);
    state.schema = schemaData;
    state.projects = projData.projects || [];
  } catch (e) {
    toast('加载失败：' + e.message, 3000);
  }
  renderTopBar();
  await switchTab('gdb');
}

// ── 主渲染 ──
function renderShell() {
  app.innerHTML = `
    <div class="app-shell" style="max-width:1200px">
      <header class="topbar" id="topbar"></header>
      <nav class="admin-tabs" id="adminTabs">
        <button class="active" data-action="switch-tab" data-tab="gdb">GDB 管理</button>
        <button data-action="switch-tab" data-tab="users">用户管理</button>
        <button data-action="switch-tab" data-tab="projects">项目管理</button>
        <button data-action="switch-tab" data-tab="prefilled">预填数据</button>
      </nav>
      <div class="admin-body" id="adminBody" style="flex:1 1 auto; overflow-y:auto;">
        <div class="admin-loading">加载中…</div>
      </div>
    </div>
    <div id="modalRoot"></div>
  `;
}

function renderTopBar() {
  const tb = qs('#topbar');
  if (!tb) return;
  const displayName = state.currentUser ? (state.currentUser.display_name || state.currentUser.username || '') : '';
  tb.innerHTML = `
    <div class="topbar-left">
      <span class="topbar-title" style="font-size:16px">林业调查管理后台</span>
    </div>
    <div class="topbar-right">
      <span class="user-display" style="max-width:120px" title="${escapeHtml(displayName)}">${escapeHtml(displayName)}</span>
      <button class="btn-logout" data-action="logout" title="登出">登出</button>
    </div>
  `;
}

async function switchTab(tab) {
  state.currentTab = tab;
  qsa('#adminTabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  const body = qs('#adminBody');
  if (!body) return;
  body.innerHTML = '<div class="admin-loading">加载中…</div>';
  try {
    if (tab === 'gdb') {
      await loadGdbTab();
    } else if (tab === 'users') {
      await loadUsersTab();
    } else if (tab === 'projects') {
      await loadProjectsTab();
    } else if (tab === 'prefilled') {
      await loadPrefilledTab();
    }
  } catch (e) {
    body.innerHTML = `<div class="admin-error">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

// ════════════════════════════════════════════
// GDB 管理标签页
// ════════════════════════════════════════════

async function loadGdbTab() {
  const body = qs('#adminBody');
  body.innerHTML = `
    <div class="admin-form">
      <h4>上传 GDB 文件（zip 格式）</h4>
      <div class="admin-form-row">
        <input type="file" id="gdbFile" accept=".zip" class="f-input" style="flex:1 1 auto; min-width:200px">
        <button class="btn-confirm admin-submit" data-action="gdb-upload">上传</button>
      </div>
      <div id="gdbUploadResult"></div>
      <div class="upload-hint">
        ⚠ 上传 .zip 格式的 GDB 文件包。<b>项目名自动从 GDB 图层属性读取</b>，无需手动输入。系统按图层名分类（人工造林/封山育林/退化林修复），非分类图层不读取。
      </div>
    </div>
    <div id="gdbListWrap"></div>
    <div id="gdbDetailWrap"></div>
  `;
  await loadGdbFiles();
}

async function loadGdbFiles() {
  const wrap = qs('#gdbListWrap');
  if (!wrap) return;
  try {
    const j = await fetchJSON('api/gdb');
    state.gdbFiles = j.files || [];
  } catch (e) {
    state.gdbFiles = [];
  }
  if (!state.gdbFiles.length) {
    wrap.innerHTML = '<div class="admin-loading">暂无 GDB 文件</div>';
    return;
  }
  const rows = state.gdbFiles.map(f => {
    const date = (f.uploaded_at || '').replace('T', ' ').slice(0, 16);
    const layerCount = (f.layers || []).length;
    const projName = (state.projects.find(p => p.id === f.project_id) || {}).name || f.project_id || '—';
    return `<tr class="admin-user-row">
      <td>${escapeHtml(f.file_name)}</td>
      <td>${escapeHtml(projName)}</td>
      <td>${layerCount}</td>
      <td>${escapeHtml(date)}</td>
      <td>${escapeHtml(f.uploaded_by || '—')}</td>
      <td class="admin-actions-cell">
        <button class="btn-admin-action" data-action="gdb-view-layers" data-gid="${f.id}">查看图层</button>
        <button class="btn-admin-action warn" data-action="gdb-delete" data-gid="${f.id}">删除</button>
      </td>
    </tr>`;
  }).join('');
  wrap.innerHTML = `
    <h4 style="margin:14px 0 8px; color:var(--green-d)">GDB 文件列表</h4>
    <table class="admin-table">
      <thead><tr><th>文件名</th><th>项目</th><th>图层数</th><th>上传时间</th><th>上传者</th><th>操作</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function uploadGdb() {
  const fi = qs('#gdbFile');
  if (!fi || !fi.files || !fi.files[0]) { toast('请选择 zip 文件'); return; }

  const fd = new FormData();
  fd.append('file', fi.files[0]);
  toast('正在上传…', 1500);
  try {
    const res = await apiFetch('api/gdb/upload', { method: 'POST', body: fd });
    const j = await res.json();
    if (!res.ok) {
      toast('上传失败：' + (j.error || res.status), 4000);
      return;
    }
    // 展示结果卡片（新返回格式：project/categories/layers/skipped_layers/imported）
    const resultEl = qs('#gdbUploadResult');
    const proj = j.project || {};
    const cats = j.categories || [];
    const skipped = j.skipped_layers || [];
    const imported = j.imported || 0;
    const layers = j.layers || {};
    const layerDetail = Object.keys(layers)
      .map(ln => `${escapeHtml(ln)}（${escapeHtml(layers[ln].category || '—')}）: 导入 ${layers[ln].imported || 0}`)
      .join('<br>');
    const skippedHtml = skipped.length
      ? `<div class="upload-hint warn-text">未读取图层（非分类）：${skipped.map(s => escapeHtml(s.name)).join('、')}</div>`
      : '';
    if (resultEl) {
      resultEl.className = 'upload-result-card';
      resultEl.innerHTML = `
        <div class="upload-result-title">✅ 上传成功</div>
        <div class="upload-result-row"><b>项目：</b>${escapeHtml(proj.name || '—')}（自动创建）</div>
        <div class="upload-result-row"><b>分类：</b>${cats.map(escapeHtml).join('、') || '—'}</div>
        <div class="upload-result-row"><b>导入小班：</b>${imported} 条</div>
        ${layerDetail ? `<div class="upload-result-detail">${layerDetail}</div>` : ''}
        ${skippedHtml}
      `;
    }
    toast(`上传成功：项目「${proj.name || ''}」，共导入 ${imported} 条小班`, 3500);
    fi.value = '';
    await loadGdbFiles();
  } catch (e) {
    toast('上传失败：' + e.message, 3000);
  }
}

async function deleteGdb(gid) {
  if (!confirm('确认删除该 GDB 文件？关联的 GeoJSON 也将删除。')) return;
  try {
    await fetchJSON(`api/gdb/${gid}`, { method: 'DELETE' });
    toast('已删除');
    state.selectedGdb = null;
    qs('#gdbDetailWrap').innerHTML = '';
    await loadGdbFiles();
  } catch (e) {
    toast('删除失败：' + e.message, 2500);
  }
}

async function viewGdbLayers(gid) {
  const detail = qs('#gdbDetailWrap');
  if (!detail) return;
  const f = state.gdbFiles.find(x => x.id === gid);
  state.selectedGdb = f || null;
  try {
    const j = await fetchJSON(`api/gdb/${gid}/layers`);
    state.gdbLayers = j.layers || [];
  } catch (e) {
    state.gdbLayers = (f && f.layers) || [];
  }
  const layerBtns = state.gdbLayers.map(l =>
    `<button class="btn-admin-action" data-action="gdb-preview-layer" data-gid="${gid}" data-layer="${escapeHtml(l)}">${escapeHtml(l)}</button>`
  ).join('');
  detail.innerHTML = `
    <h4 style="margin:14px 0 8px; color:var(--green-d)">GDB 图层 — ${escapeHtml(f ? f.file_name : gid)}</h4>
    <div class="admin-actions-cell" style="margin-bottom:10px">${layerBtns}</div>
    <div id="layerPreviewWrap"></div>
    <div class="admin-form" style="margin-top:12px">
      <h4>生成 GeoJSON</h4>
      <div class="admin-form-row">
        <input id="geojsonLayer" class="f-input" placeholder="图层名（如：抚育区）" value="${escapeHtml(state.gdbLayers[0] || '抚育区')}">
        <button class="btn-confirm admin-submit" data-action="gdb-gen-geojson" data-gid="${gid}">生成 GeoJSON</button>
      </div>
      <div class="upload-hint">生成后 User 端可在地图上查看小班面。</div>
    </div>
  `;
}

async function previewLayer(gid, layer) {
  const wrap = qs('#layerPreviewWrap');
  if (!wrap) return;
  wrap.innerHTML = '<div class="admin-loading">加载图层预览…</div>';
  state.selectedLayer = layer;
  try {
    const j = await fetchJSON(`api/gdb/${gid}/layers/${encodeURIComponent(layer)}`);
    state.layerPreview = j;
    const fields = j.fields || [];
    const rows = j.rows || [];
    const ths = fields.map(f => `<th>${escapeHtml(f.name)}<br><small>${escapeHtml(f.dtype || '')}</small></th>`).join('');
    const trs = rows.map(r =>
      `<tr>${fields.map(f => `<td>${escapeHtml(r[f.name] !== undefined ? r[f.name] : '')}</td>`).join('')}</tr>`
    ).join('');
    wrap.innerHTML = `
      <h4 style="margin:10px 0 6px">图层预览：${escapeHtml(layer)}（前 ${rows.length} 行）</h4>
      <div style="overflow-x:auto">
        <table class="admin-table">
          <thead><tr>${ths}</tr></thead>
          <tbody>${trs}</tbody>
        </table>
      </div>
    `;
  } catch (e) {
    wrap.innerHTML = `<div class="admin-error">预览失败：${escapeHtml(e.message)}</div>`;
  }
}

async function generateGeojson(gid) {
  const inp = qs('#geojsonLayer');
  const layer = inp ? inp.value.trim() : '';
  if (!layer) { toast('请输入图层名'); return; }
  toast('正在生成 GeoJSON…', 1500);
  try {
    const j = await fetchJSON(`api/gdb/${gid}/geojson`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ layer }),
    });
    toast(`已生成 ${j.polygon_count} 个面、${j.centroid_count} 个质心`);
  } catch (e) {
    toast('生成失败：' + e.message, 3000);
  }
}

// ════════════════════════════════════════════
// 用户管理标签页
// ════════════════════════════════════════════

async function loadUsersTab() {
  const body = qs('#adminBody');
  try {
    const j = await fetchJSON('api/users');
    state.users = j.users || [];
  } catch (e) {
    state.users = [];
  }
  body.innerHTML = renderUsersTable(state.users) + renderCreateUserForm();
}

function renderUsersTable(users) {
  const rows = users.map(u => {
    const status = u.is_active
      ? '<span class="user-status active">启用</span>'
      : '<span class="user-status disabled">禁用</span>';
    const adminBadge = u.is_admin ? '<span class="badge-admin">管理员</span>' : '';
    const toggleBtn = u.is_active
      ? `<button class="btn-admin-action warn" data-action="user-toggle" data-uid="${u.id}" data-active="0">禁用</button>`
      : `<button class="btn-admin-action" data-action="user-toggle" data-uid="${u.id}" data-active="1">启用</button>`;
    const lastLogin = u.last_login_at || u.last_login ? String(u.last_login_at || u.last_login).replace('T', ' ').slice(0, 16) : '—';
    return `<tr class="admin-user-row">
      <td>${escapeHtml(u.username)}</td>
      <td>${escapeHtml(u.display_name || '')}</td>
      <td>${adminBadge}</td>
      <td>${status}</td>
      <td>${escapeHtml(lastLogin)}</td>
      <td class="admin-actions-cell">
        ${toggleBtn}
        <button class="btn-admin-action" data-action="user-reset-pwd" data-uid="${u.id}">重置密码</button>
      </td>
    </tr>`;
  }).join('');
  return `
    <h4 style="margin:0 0 8px; color:var(--green-d)">用户列表（${users.length}）</h4>
    <table class="admin-table">
      <thead><tr><th>用户名</th><th>显示名</th><th>角色</th><th>状态</th><th>最后登录</th><th>操作</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderCreateUserForm() {
  return `
    <div class="admin-form">
      <h4>添加用户</h4>
      <div class="admin-form-row">
        <input id="auUsername" class="f-input" placeholder="用户名 *" style="flex:1 1 120px">
        <input id="auPassword" type="password" class="f-input" placeholder="密码 *（至少6位）" style="flex:1 1 140px">
        <input id="auDisplay" class="f-input" placeholder="显示名" style="flex:1 1 120px">
        <label class="admin-check"><input type="checkbox" id="auIsAdmin"> 管理员</label>
        <button class="btn-confirm admin-submit" data-action="user-create">添加</button>
      </div>
    </div>
  `;
}

async function createUser() {
  const username = (qs('#auUsername').value || '').trim();
  const password = (qs('#auPassword').value || '').trim();
  const display_name = (qs('#auDisplay').value || '').trim();
  const is_admin = qs('#auIsAdmin').checked;
  if (!username) { toast('请输入用户名'); return; }
  if (!password || password.length < 6) { toast('密码至少6位'); return; }
  try {
    await fetchJSON('api/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, display_name, is_admin }),
    });
    toast('用户已创建');
    await loadUsersTab();
  } catch (e) {
    toast('创建失败：' + e.message, 2500);
  }
}

async function toggleUser(uid, active) {
  try {
    await fetchJSON(`api/users/${uid}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_active: active === '1' || active === true }),
    });
    toast('已更新');
    await loadUsersTab();
  } catch (e) {
    toast('操作失败：' + e.message, 2500);
  }
}

async function resetPassword(uid) {
  const pwd = prompt('请输入新密码（至少6位）');
  if (!pwd) return;
  if (pwd.length < 6) { toast('密码至少6位'); return; }
  try {
    await fetchJSON(`api/users/${uid}/password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pwd }),
    });
    toast('密码已重置');
  } catch (e) {
    toast('重置失败：' + e.message, 2500);
  }
}

// ════════════════════════════════════════════
// 项目管理标签页
// ════════════════════════════════════════════

async function loadProjectsTab() {
  const body = qs('#adminBody');
  try {
    const j = await fetchJSON('api/projects');
    state.projects = j.projects || [];
  } catch (e) { /* 降级使用缓存 */ }
  body.innerHTML = renderProjectsTable() + renderMembersPanel();
}

function renderProjectsTable() {
  const rows = state.projects.map(p => {
    const date = (p.created_at || '').replace('T', ' ').slice(0, 16);
    return `<tr class="admin-user-row">
      <td>${escapeHtml(p.name)}</td>
      <td>${escapeHtml(p.creator || '—')}</td>
      <td>${escapeHtml(p.township || '—')}</td>
      <td>${escapeHtml(date)}</td>
      <td class="admin-actions-cell">
        <button class="btn-admin-action" data-action="proj-view-members" data-pid="${p.id}">成员</button>
        <button class="btn-admin-action" data-action="proj-export-base" data-pid="${p.id}">基本信息</button>
        <button class="btn-admin-action" data-action="proj-export-samples" data-pid="${p.id}">样地</button>
        <button class="btn-admin-action" data-action="proj-export-tracks" data-pid="${p.id}">轨迹GPX</button>
        <button class="btn-admin-action warn" data-action="proj-delete" data-pid="${p.id}" data-name="${escapeHtml(p.name)}">删除</button>
      </td>
    </tr>`;
  }).join('');
  return `
    <h4 style="margin:0 0 8px; color:var(--green-d)">项目列表（${state.projects.length}）</h4>
    <table class="admin-table">
      <thead><tr><th>项目名</th><th>创建人</th><th>乡镇</th><th>创建时间</th><th>操作</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderMembersPanel() {
  const projOpts = state.projects.map(p =>
    `<option value="${p.id}" ${state.memberProject === p.id ? 'selected' : ''}>${escapeHtml(p.name)}</option>`
  ).join('');
  const userOpts = (state.users || []).map(u =>
    `<option value="${u.id}">${escapeHtml(u.display_name || u.username)} (${escapeHtml(u.username)})</option>`
  ).join('');
  let membersHtml = '<div class="admin-loading">请选择项目查看成员</div>';
  if (state.memberProject && state.members.length) {
    membersHtml = '<table class="admin-table"><thead><tr><th>用户名</th><th>显示名</th><th>操作</th></tr></thead><tbody>' +
      state.members.map(m => `<tr class="admin-user-row">
        <td>${escapeHtml(m.username)}</td>
        <td>${escapeHtml(m.display_name || '')}</td>
        <td><button class="btn-admin-action warn" data-action="member-remove" data-uid="${m.user_id || m.id}">移除</button></td>
      </tr>`).join('') +
      '</tbody></table>';
  } else if (state.memberProject) {
    membersHtml = '<div class="admin-loading">该项目暂无成员</div>';
  }
  return `
    <div class="admin-form">
      <h4>项目成员管理</h4>
      <div class="admin-form-row">
        <select id="memberProjSelect" class="admin-select">
          <option value="">选择项目</option>
          ${projOpts}
        </select>
      </div>
      <div id="membersList" style="margin:8px 0">${membersHtml}</div>
      <div class="admin-form-row">
        <select id="memberUserSelect" class="admin-select">
          <option value="">选择用户</option>
          ${userOpts}
        </select>
        <button class="btn-confirm admin-submit" data-action="member-add">添加成员</button>
      </div>
    </div>
  `;
}

async function deleteProject(pid, name) {
  if (!confirm(`确认删除项目「${name || pid}」？\n该操作将一并删除其所有关联数据（批次/小班/录入记录/预填/成员）及 GDB 文件，且不可恢复！`)) return;
  try {
    await fetchJSON(`api/projects/${pid}`, { method: 'DELETE' });
    toast('项目已删除');
    // 若当前正在查看该项目成员，重置
    if (state.memberProject === pid) {
      state.memberProject = null;
      state.members = [];
    }
    await loadProjectsTab();
  } catch (e) {
    toast('删除失败：' + e.message, 3000);
  }
}

function exportBase(pid) {
  window.open(`api/projects/${pid}/export_base`, '_blank');
}

function exportSamples(pid) {
  window.open(`api/projects/${pid}/export_samples`, '_blank');
}

function exportTracks(pid) {
  window.open(`api/projects/${pid}/export_tracks`, '_blank');
}

async function viewMembers(pid) {
  state.memberProject = pid;
  const list = qs('#membersList');
  if (list) list.innerHTML = '<div class="admin-loading">加载中…</div>';
  try {
    const j = await fetchJSON(`api/projects/${pid}/members`);
    state.members = j.members || [];
  } catch (e) {
    state.members = [];
  }
  // 确保用户列表已加载（用于添加成员下拉）
  if (!state.users.length) {
    try {
      const uj = await fetchJSON('api/users');
      state.users = uj.users || [];
    } catch (e) {}
  }
  // 重新渲染整个项目管理标签以反映选中状态
  await loadProjectsTab();
  // 恢复选中值
  const sel = qs('#memberProjSelect');
  if (sel) sel.value = pid;
}

async function addMember() {
  const pid = qs('#memberProjSelect') ? qs('#memberProjSelect').value : '';
  const uid = qs('#memberUserSelect') ? qs('#memberUserSelect').value : '';
  if (!pid) { toast('请选择项目'); return; }
  if (!uid) { toast('请选择用户'); return; }
  const user = (state.users || []).find(u => String(u.id) === String(uid));
  const username = user ? user.username : '';
  try {
    await fetchJSON(`api/projects/${pid}/members`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: Number(uid), username }),
    });
    toast('成员已添加');
    await viewMembers(pid);
  } catch (e) {
    toast('添加失败：' + e.message, 2500);
  }
}

async function removeMember(uid) {
  const pid = state.memberProject;
  if (!pid) return;
  if (!confirm('确认移除该成员？')) return;
  try {
    await fetchJSON(`api/projects/${pid}/members/${uid}`, { method: 'DELETE' });
    toast('已移除');
    await viewMembers(pid);
  } catch (e) {
    toast('移除失败：' + e.message, 2500);
  }
}

// ════════════════════════════════════════════
// 预填数据标签页
// ════════════════════════════════════════════

async function loadPrefilledTab() {
  const body = qs('#adminBody');
  const projOpts = state.projects.map(p =>
    `<option value="${p.id}" ${state.prefilledProject === p.id ? 'selected' : ''}>${escapeHtml(p.name)}</option>`
  ).join('');
  const tableOpts = (state.schema ? state.schema.tables : []).map(t =>
    `<option value="${t.id}" ${state.prefilledTable === t.id ? 'selected' : ''}>${escapeHtml(t.name)}</option>`
  ).join('');
  body.innerHTML = `
    <h4 style="margin:0 0 8px; color:var(--green-d)">预填数据编辑</h4>
    <div class="admin-form-row">
      <select id="pfProjSelect" class="admin-select">
        <option value="">选择项目 *</option>
        ${projOpts}
      </select>
      <select id="pfTableSelect" class="admin-select">
        <option value="">选择表 *</option>
        ${tableOpts}
      </select>
      <select id="pfSubtableSelect" class="admin-select" style="display:none">
        <option value="">选择子表</option>
      </select>
      <button class="btn-confirm admin-submit" data-action="pf-load">加载</button>
    </div>
    <div class="upload-hint">
      预填数据（黄色列）由管理员预先录入，外业调查时自动显示为只读。选择项目+表后可查看和编辑。
    </div>
    <div id="pfRowsWrap"></div>
    <div id="pfEditWrap"></div>
  `;
  // 如果已选中，恢复子表选择
  if (state.prefilledTable) {
    updateSubtableOptions();
  }
  // 如果有已加载的数据，直接显示
  if (state.prefilledProject && state.prefilledTable && state.prefilledRows.length) {
    renderPrefilledRows();
  }
}

function updateSubtableOptions() {
  const t = getTableDef(state.prefilledTable);
  const subSel = qs('#pfSubtableSelect');
  if (!subSel) return;
  if (t && t.has_subtables && t.subtables && t.subtables.length) {
    subSel.style.display = '';
    subSel.innerHTML = '<option value="">选择子表</option>' +
      t.subtables.map(s => `<option value="${s.id}" ${state.prefilledSubtable === s.id ? 'selected' : ''}>${escapeHtml(s.name)}</option>`).join('');
  } else {
    subSel.style.display = 'none';
    state.prefilledSubtable = '';
  }
}

async function loadPrefilled() {
  const pid = qs('#pfProjSelect') ? qs('#pfProjSelect').value : '';
  const tid = qs('#pfTableSelect') ? qs('#pfTableSelect').value : '';
  if (!pid) { toast('请选择项目'); return; }
  if (!tid) { toast('请选择表'); return; }
  state.prefilledProject = pid;
  state.prefilledTable = tid;
  const t = getTableDef(tid);
  if (t && t.has_subtables) {
    const subSel = qs('#pfSubtableSelect');
    if (subSel) state.prefilledSubtable = subSel.value || '';
  } else {
    state.prefilledSubtable = '';
  }
  const wrap = qs('#pfRowsWrap');
  if (wrap) wrap.innerHTML = '<div class="admin-loading">加载中…</div>';
  try {
    const j = await fetchJSON(`api/projects/${pid}/prefilled/${tid}`);
    state.prefilledRows = (j.rows || []).filter(r =>
      state.prefilledSubtable ? (r.subtable_id || '') === state.prefilledSubtable : true
    );
  } catch (e) {
    state.prefilledRows = [];
  }
  renderPrefilledRows();
}

function renderPrefilledRows() {
  const wrap = qs('#pfRowsWrap');
  if (!wrap) return;
  const t = getTableDef(state.prefilledTable);
  if (!t) { wrap.innerHTML = '<div class="admin-error">表定义未加载</div>'; return; }
  const cols = t.prefilled_columns || [];
  if (!cols.length) {
    wrap.innerHTML = '<div class="admin-loading">该表无预填列</div>';
    return;
  }
  if (!state.prefilledRows.length) {
    wrap.innerHTML = '<div class="admin-loading">暂无预填数据，在下方添加</div>';
  } else {
    const ths = ['序号'].concat(cols.map(c => `<th>${escapeHtml(c.label)}</th>`), '<th>操作</th>').join('');
    const trs = state.prefilledRows.map((r, i) => {
      const data = r.data || {};
      const tds = cols.map(c => `<td>${escapeHtml(data[c.key] !== undefined ? data[c.key] : '')}</td>`).join('');
      return `<tr class="admin-user-row">
        <td>${i + 1}</td>
        ${tds}
        <td class="admin-actions-cell">
          <button class="btn-admin-action" data-action="pf-edit" data-idx="${i}">编辑</button>
          <button class="btn-admin-action warn" data-action="pf-delete" data-idx="${i}">删除</button>
        </td>
      </tr>`;
    }).join('');
    wrap.innerHTML = `
      <table class="admin-table">
        <thead><tr>${ths}</tr></thead>
        <tbody>${trs}</tbody>
      </table>
    `;
  }
  // 渲染添加/编辑表单
  renderPrefilledEditForm();
}

function renderPrefilledEditForm(editIdx) {
  const wrap = qs('#pfEditWrap');
  if (!wrap) return;
  const t = getTableDef(state.prefilledTable);
  if (!t) return;
  const cols = t.prefilled_columns || [];
  const isEdit = editIdx !== undefined && editIdx !== null;
  const existing = isEdit ? (state.prefilledRows[editIdx] || {}) : {};
  const existingData = existing.data || {};
  const fieldsHtml = cols.map(c => {
    const v = existingData[c.key] !== undefined ? existingData[c.key] : '';
    return `<div class="admin-form-row">
      <label style="flex:0 0 120px; font-size:13px; color:var(--text-2); font-weight:600">${escapeHtml(c.label)}</label>
      <input class="f-input" data-pf-key="${escapeHtml(c.key)}" value="${escapeHtml(v)}" placeholder="${escapeHtml(c.label)}">
    </div>`;
  }).join('');
  wrap.innerHTML = `
    <div class="admin-form">
      <h4>${isEdit ? '编辑预填行 #' + (editIdx + 1) : '添加预填行'}</h4>
      ${fieldsHtml}
      <div class="admin-form-row" style="margin-top:8px">
        <button class="btn-confirm admin-submit" data-action="pf-save" data-idx="${isEdit ? editIdx : ''}">${isEdit ? '保存修改' : '添加'}</button>
        ${isEdit ? '<button class="btn-cancel admin-submit" data-action="pf-cancel-edit">取消</button>' : ''}
      </div>
    </div>
  `;
}

async function savePrefilledRow(idxStr) {
  const pid = state.prefilledProject;
  const tid = state.prefilledTable;
  if (!pid || !tid) { toast('请先选择项目和表'); return; }
  const inputs = qsa('[data-pf-key]');
  const data = {};
  for (const inp of inputs) {
    data[inp.dataset.pfKey] = inp.value;
  }
  const isEdit = idxStr !== '' && idxStr !== undefined && idxStr !== null;
  const rowIndex = isEdit ? (state.prefilledRows[Number(idxStr)].row_index || Number(idxStr)) : state.prefilledRows.length;
  const payload = {
    subtable_id: state.prefilledSubtable || '',
    row_index: rowIndex,
    data,
  };
  try {
    await fetchJSON(`api/projects/${pid}/prefilled/${tid}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    toast(isEdit ? '已保存修改' : '已添加');
    // 重新加载
    await loadPrefilled();
  } catch (e) {
    toast('保存失败：' + e.message, 2500);
  }
}

async function deletePrefilledRow(idx) {
  // 预填数据 API 仅支持覆盖式保存（POST upsert），删除 = 保存空行
  // 此处简化：从本地列表中移除并提示（后端需另行实现 DELETE 接口）
  if (!confirm('确认删除该预填行？')) return;
  state.prefilledRows.splice(idx, 1);
  toast('已从列表移除（需重新保存生效）');
  renderPrefilledRows();
}

// ── 事件委托 ──
app.addEventListener('click', async (e) => {
  const t = e.target.closest('[data-action]');
  if (!t) return;
  const action = t.dataset.action;
  switch (action) {
    case 'switch-tab':
      await switchTab(t.dataset.tab);
      break;
    case 'logout':
      window.location.href = '/forest/logout';
      break;
    // GDB
    case 'gdb-upload':
      await uploadGdb();
      break;
    case 'gdb-delete':
      await deleteGdb(t.dataset.gid);
      break;
    case 'gdb-view-layers':
      await viewGdbLayers(t.dataset.gid);
      break;
    case 'gdb-preview-layer':
      await previewLayer(t.dataset.gid, t.dataset.layer);
      break;
    case 'gdb-gen-geojson':
      await generateGeojson(t.dataset.gid);
      break;
    // Users
    case 'user-create':
      await createUser();
      break;
    case 'user-toggle':
      await toggleUser(t.dataset.uid, t.dataset.active);
      break;
    case 'user-reset-pwd':
      await resetPassword(t.dataset.uid);
      break;
    // Projects
    case 'proj-export-base':
      exportBase(t.dataset.pid);
      break;
    case 'proj-export-samples':
      exportSamples(t.dataset.pid);
      break;
    case 'proj-export-tracks':
      exportTracks(t.dataset.pid);
      break;
    case 'proj-delete':
      await deleteProject(t.dataset.pid, t.dataset.name);
      break;
    case 'proj-view-members':
      await viewMembers(t.dataset.pid);
      break;
    case 'member-add':
      await addMember();
      break;
    case 'member-remove':
      await removeMember(t.dataset.uid);
      break;
    // Prefilled
    case 'pf-load':
      state.prefilledTable = qs('#pfTableSelect').value;
      updateSubtableOptions();
      await loadPrefilled();
      break;
    case 'pf-edit':
      renderPrefilledEditForm(Number(t.dataset.idx));
      break;
    case 'pf-delete':
      await deletePrefilledRow(Number(t.dataset.idx));
      break;
    case 'pf-save':
      await savePrefilledRow(t.dataset.idx);
      break;
    case 'pf-cancel-edit':
      renderPrefilledEditForm();
      break;
  }
});

// change 事件（select 切换）
app.addEventListener('change', (e) => {
  const t = e.target;
  if (t.id === 'pfTableSelect') {
    state.prefilledTable = t.value;
    state.prefilledSubtable = '';
    updateSubtableOptions();
    qs('#pfRowsWrap').innerHTML = '';
    qs('#pfEditWrap').innerHTML = '';
  } else if (t.id === 'pfSubtableSelect') {
    state.prefilledSubtable = t.value;
    if (state.prefilledProject && state.prefilledTable) {
      loadPrefilled();
    }
  } else if (t.id === 'memberProjSelect') {
    if (t.value) {
      viewMembers(t.value);
    } else {
      state.memberProject = null;
      state.members = [];
    }
  }
});

// 启动
init();
