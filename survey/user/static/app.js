/* ============================================================
   林业野外调查即时录入系统 — User 端前端
   纯原生 JS，单页应用。打包为 Android app。
   API 用相对路径（APPLICATION_ROOT 处理 /survey/ 前缀）。
   功能：调查 / 打卡 / 轨迹 / 照片 / 导出
   ============================================================ */

'use strict';

// ── 全局状态 ──
const state = {
  schema: null,           // 从 api/schema 加载的表定义
  currentTable: null,     // 当前选中的表 id
  project: null,          // 当前项目
  projects: [],           // 项目列表
  records: {},            // {table_id: [records]}
  formData: {},           // 当前输入栏的数据
  user: '',               // 验收人（从 api/me 自动获取）
  currentUser: null,      // 当前登录用户（来自 api/me）
  collapsed: {},          // {groupKey: true} 折叠状态
  busy: false,
  // 三级导航视图: 'projects' | 'sc_list' | 'survey' | 'survey_grid'
  view: 'projects',
  // 小班信息
  scAllRows: [],          // 当前项目下全部小班（客户端搜索用）
  scFilteredRows: [],     // 搜索过滤后的小班
  scViewMode: 'list',     // 'list' | 'map'
  subcompartment: null,   // 当前选中的小班
  subcompartmentData: null, // 当前小班完整字段（含 prefilled 映射后的数据）
  editRecordId: null,     // 当前小班+表对应的唯一调查记录 id（一对一：有则编辑、无则新建）
  scExtras: null,         // 当前小班的扩展数据（打卡/轨迹/照片）
  _map: null,             // Leaflet 地图实例
  _scTracking: false,     // 是否正在记录轨迹
  // 网格调查
  gridTable: 'table1',    // 网格当前表
  gridTownship: '',       // 网格乡镇筛选（空=全部）
  gridVillage: '',        // 网格村筛选（空=全部）
  _grid: null,            // jspreadsheet 实例
  _gridSurveyMap: {},     // {subcompartment_id: data} 已有调查数据
  subcompartmentPrefilledMap: {}, // {sc_id: prefilled_dict} 网格黄色列取值
  // 级联筛选：项目 → 分类 → 县/乡/村/林班 → 小班
  gridCategory: '',                // 当前分类（''=全部分类）
  gridSubcompartment: null,        // 当前选中的小班对象
  gridCategories: [],              // 项目可选分类列表
  gridScRows: null,                // 按分类过滤后的小班集合（null=用 scAllRows）
  _gridRowFields: [],              // 当前两列表格每行的字段信息（用于行号→字段映射）
};

// 分类→表映射
const CATEGORY_TO_TABLE = {
  '人工造林': 'table1',
  '封山育林': 'table2',
  '退化林修复': 'table3',
  '水利水保': 'table4',
  '草原': 'table5',
};

const app = document.getElementById('app');

// ── 工具函数 ──
const qs = (sel, root = document) => root.querySelector(sel);
const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function todayStr() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function toast(msg, ms = 1800) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

// ── 原生权限（Capacitor App 内）──
// 等待 Capacitor Bridge 注入完成（远程 URL 模式下 bridge 可能在页面脚本之后才就绪）
function waitForCapacitor(timeout = 3000) {
  return new Promise(resolve => {
    const start = Date.now();
    (function poll() {
      if (window.Capacitor && typeof window.Capacitor.isNativePlatform === 'function') {
        resolve(true);
        return;
      }
      if (Date.now() - start >= timeout) { resolve(false); return; }
      setTimeout(poll, 80);
    })();
  });
}
function isApp() {
  // 多重特征：isNativePlatform 或 原生插件存在（AppPermissions 仅在原生壳注册）
  return !!(
    window.Capacitor &&
    ((typeof window.Capacitor.isNativePlatform === 'function' && window.Capacitor.isNativePlatform()) ||
     (window.Capacitor.Plugins && window.Capacitor.Plugins.AppPermissions))
  );
}
function permPlugin() {
  return (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.AppPermissions) || null;
}
async function permState(type) {
  const p = permPlugin();
  if (!p) return { granted: true, state: 'unsupported' };
  try { return await p.check({ type }); } catch (e) { return { granted: true, state: 'unknown' }; }
}
async function permRequest(type) {
  const p = permPlugin();
  if (!p) return { granted: true, state: 'unsupported' };
  try { return await p.request({ type }); } catch (e) { return { granted: false, state: 'denied' }; }
}
function permOpenSettings() {
  const p = permPlugin();
  if (p) { try { p.openSettings(); } catch (e) {} }
}
const PERM_LABEL = { location: '定位', camera: '相机' };

// 权限不足提示弹窗：可跳转系统设置
function showPermDialog(type, detail) {
  const root = qs('#modalRoot');
  const label = PERM_LABEL[type] || '权限';
  root.innerHTML = `
    <div class="modal-mask" data-action="close-modal-mask">
      <div class="modal modal-help">
        <div class="help-header">
          <h3>需要${label}权限</h3>
          <button class="btn-icon" data-action="close-modal">✕</button>
        </div>
        <div class="help-body">
          <p>${detail || `本功能需要「${label}」权限才能正常使用，请在系统设置中开启${label}权限。`}</p>
        </div>
        <div class="help-footer" style="display:flex;gap:10px;justify-content:flex-end;padding:12px 16px;border-top:1px solid #eee;">
          <button class="btn-cancel" data-action="close-modal">知道了</button>
          <button class="btn-confirm" data-action="perm-open-settings" data-type="${type}">去设置</button>
        </div>
      </div>
    </div>`;
}

// 定位失败统一处理：权限被拒时给出明确提示
function handleGeoError(err) {
  if (err && err.code === 1) { // PERMISSION_DENIED
    if (isApp()) {
      showPermDialog('location', '未获得定位权限，无法获取 GPS 坐标。请在系统设置中允许「定位」，然后重试。');
    } else {
      toast('未授权定位权限：请在浏览器设置中允许定位后重试', 3000);
    }
    return;
  }
  if (err && err.code === 3) { toast('定位超时，请确认手机定位开关已开启', 2500); return; }
  toast('定位失败：' + (err && err.message ? err.message : '未知错误'), 2500);
}

// 统一 fetch 封装：401 自动跳转登录页
async function apiFetch(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = '/forest/login?next=/survey/';
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

// ── Schema 辅助 ──
function getTableDef(tableId) {
  if (!state.schema) return null;
  return state.schema.tables.find(t => t.id === tableId) || null;
}

function activeDef() {
  // 一对一模型：表无子表概念，activeDef 即当前表定义
  return getTableDef(state.currentTable);
}

function activeInputColumns() {
  const d = activeDef();
  return d ? (d.input_columns || []) : [];
}

function activePrefilledColumns() {
  const d = activeDef();
  return d ? (d.prefilled_columns || []) : [];
}

function computeGroups(fields) {
  const order = [];
  const map = {};
  for (const f of fields) {
    const g = f.group || '其他';
    if (!map[g]) { map[g] = []; order.push(g); }
    map[g].push(f);
  }
  return order.map(name => ({ name, fields: map[name] }));
}

// ── 初始化 ──
async function init() {
  renderShell();
  // 启动即预先申请默认权限（定位/相机）；内部等待 Capacitor Bridge 就绪，不阻塞页面渲染
  checkAppPermissions();
  try {
    const me = await fetchJSON('api/me');
    state.currentUser = me;
    if (!state.user && (me.display_name || me.username)) {
      state.user = me.display_name || me.username;
    }
  } catch (e) {
    if (!state.currentUser) {
      toast('加载用户信息失败：' + e.message, 3000);
      return;
    }
  }
  try {
    const [schemaData, projData] = await Promise.all([
      fetchJSON('api/schema'),
      fetchJSON('api/projects'),
    ]);
    state.schema = schemaData;
    state.projects = projData.projects || [];
    if (state.schema && state.schema.tables.length) {
      state.currentTable = state.schema.tables[0].id;
    }
  } catch (e) {
    toast('加载失败：' + e.message, 3000);
  }
  // 未选择项目时自动恢复最后一次调查的项目（无记录则取列表第一个）
  let lastPid = null;
  try { lastPid = localStorage.getItem('hqz_survey_last_project'); } catch (e) {}
  const autoProject = state.projects.find(p => p.id === lastPid) || state.projects[0];
  if (autoProject) {
    await enterProject(autoProject.id);
    return;
  }
  state.view = 'survey_grid';
  renderApp();
}

// App 内启动时主动检查定位/相机权限，缺失则先申请；被拒后提示用户
async function checkAppPermissions() {
  // 远程 URL 模式下 Bridge 可能晚于页面脚本注入，先等待就绪（浏览器/未就绪时静默跳过）
  await waitForCapacitor(3000);
  if (!isApp()) return;
  try {
    const loc = await permState('location');
    const cam = await permState('camera');
    if (loc.granted && cam.granted) return;
    await Promise.all([
      loc.granted ? Promise.resolve() : permRequest('location'),
      cam.granted ? Promise.resolve() : permRequest('camera'),
    ]);
    const loc2 = await permState('location');
    const cam2 = await permState('camera');
    if (!loc2.granted) {
      showPermDialog('location', '未获得定位权限，将无法使用「获取GPS/打卡/轨迹」功能。请在系统设置中允许「定位」。');
    } else if (!cam2.granted) {
      showPermDialog('camera', '未获得相机权限，将无法使用「拍照」功能。请在系统设置中允许「相机」。');
    }
  } catch (e) { /* 忽略：非必要不阻塞进入页面 */ }
}

function initFormData(def) {
  const fd = {};
  if (!def) return fd;
  for (const f of (def.input_columns || [])) {
    if (f.type === 'sample_array') {
      // 样方子数组：默认空数组，加载已有记录时由 initFormDataForActive 覆盖
      fd[f.key] = [];
    } else if (f.type === 'date' && f.default === 'today') {
      fd[f.key] = todayStr();
    } else if (f.type === 'checkbox') {
      fd[f.key] = f.default !== undefined ? !!f.default : false;
    } else if (f.default !== undefined && f.default !== '') {
      fd[f.key] = f.default;
    } else {
      fd[f.key] = '';
    }
  }
  return fd;
}

function initFormDataForActive() {
  // 一对一：若当前小班已有该表的调查记录，则把其数据预填进编辑框
  const rec = findExistingRecord();
  state.editRecordId = rec ? rec.id : null;
  const fd = initFormData(activeDef());
  if (rec && rec.data) {
    for (const k of Object.keys(rec.data)) {
      if (k in fd) fd[k] = rec.data[k];
    }
  }
  state.formData = fd;
  applyUserDefault();
}

function applyUserDefault() {
  if (!state.user) return;
  const fd = state.formData;
  if ('inspector' in fd && !fd.inspector) fd.inspector = state.user;
  if ('surveyor' in fd && !fd.surveyor) fd.surveyor = state.user;
}

// ── 主渲染 ──
function renderShell() {
  const bs = qs('#boot-splash');
  if (bs) bs.remove();
  app.innerHTML = `
    <div class="app-shell">
      <header class="topbar" id="topbar"></header>
      <div id="pageBody" class="page-body"></div>
    </div>
    <div id="modalRoot"></div>
  `;
}

function renderApp() {
  renderTopBar();
  const body = qs('#pageBody');
  if (!body) return;
  if (state.view === 'projects') {
    body.innerHTML = renderProjectsPage();
  } else if (state.view === 'sc_list') {
    body.innerHTML = renderScListPage();
    bindScListPage();
  } else if (state.view === 'survey') {
    body.innerHTML = renderSurveyPage();
    bindSurveyPage();
  } else if (state.view === 'survey_grid') {
    body.innerHTML = renderSurveyGridPage();
    bindSurveyGridPage();
  } else {
    body.innerHTML = '<div class="empty-hint">未知页面</div>';
  }
}

function renderTopBar() {
  const tb = qs('#topbar');
  if (!tb) return;
  const displayName = state.currentUser ? (state.currentUser.display_name || state.currentUser.username || '') : '';
  const backBtn = state.view === 'sc_list'
    ? '<button class="btn-back" data-action="go-projects" title="返回项目列表">‹</button>'
    : ((state.view === 'survey' || state.view === 'survey_grid')
      ? '<button class="btn-back" data-action="go-sc-list" title="返回小班列表">‹</button>'
      : '');
  let viewTitle = '';
  if (state.view === 'projects') {
    viewTitle = '<span class="topbar-title">项目列表</span>';
  } else if (state.view === 'sc_list' && state.project) {
    viewTitle = `<span class="topbar-title">${escapeHtml(state.project.name || '')}</span>`;
  } else if (state.view === 'survey' && state.subcompartment) {
    viewTitle = `<span class="topbar-title">${escapeHtml(state.subcompartment.subcompartment_label || '')}</span>`;
  } else if (state.view === 'survey_grid') {
    const tdef = getGridTableDef(state.gridTable);
    const catLabel = state.gridCategory || '全部分类';
    const scLabel = state.gridSubcompartment ? (state.gridSubcompartment.subcompartment_label || '') : '';
    const tail = scLabel ? ` · ${escapeHtml(scLabel)}` : '';
    viewTitle = `<span class="topbar-title">📊 ${escapeHtml(catLabel)}${tail} · ${escapeHtml((tdef && tdef.name) || '网格调查')}</span>`;
  }
  tb.innerHTML = `
    <div class="topbar-left">
      ${backBtn}
      ${viewTitle}
    </div>
    <div class="topbar-right">
      <button class="btn-icon help-btn" data-action="open-help" title="使用说明">?</button>
      ${(state.view === 'survey' || state.view === 'survey_grid') ? '<button class="btn-export" data-action="export">导出</button>' : ''}
      <span class="user-display" title="${escapeHtml(displayName)}">${escapeHtml(displayName)}</span>
      <button class="btn-logout" data-action="logout" title="登出">登出</button>
    </div>
  `;
}

// ════════════════════════════════════════════
// Page 1: 项目列表页
// ════════════════════════════════════════════

function renderProjectsPage() {
  const list = state.projects;
  const header = `
    <div class="page-header">
      <h2 class="page-title">项目列表</h2>
      <div class="page-header-actions">
        <span class="sc-list-count">共 ${list.length} 个项目</span>
      </div>
    </div>
  `;
  if (!list.length) {
    return `<div class="page-batches">
      ${header}
      <div class="empty-hint">暂无可用项目，请联系管理员分配项目权限</div>
    </div>`;
  }
  const cards = list.map(p => {
    const date = (p.created_at || '').replace('T', ' ').slice(0, 16);
    return `<div class="batch-card" data-action="enter-project" data-pid="${p.id}">
      <div class="batch-card-head">
        <span class="batch-card-name">${escapeHtml(p.name)}</span>
      </div>
      <div class="batch-card-meta">
        ${p.creator ? `<span>👤 ${escapeHtml(p.creator)}</span>` : ''}
        ${p.township ? `<span>📍 ${escapeHtml(p.township)}</span>` : ''}
      </div>
      <div class="batch-card-foot">
        <span class="batch-card-file">📅 ${escapeHtml(date)}</span>
      </div>
    </div>`;
  }).join('');
  return `<div class="page-batches">
    ${header}
    <div class="batch-list">${cards}</div>
  </div>`;
}

async function enterProject(pid) {
  const p = state.projects.find(x => x.id === pid);
  if (!p) return;
  state.project = p;
  // 记住最后调查的项目，下次启动自动恢复
  try { localStorage.setItem('hqz_survey_last_project', pid); } catch (e) {}
  await goToSurveyGrid();
}

// ════════════════════════════════════════════
// Page 2: 小班列表 / 地图页
// ════════════════════════════════════════════

function renderScListPage() {
  if (!state.project) {
    return `<div class="page-sc-list"><div class="empty-hint">未选择项目</div></div>`;
  }
  const rows = state.scFilteredRows || state.scAllRows || [];
  const mode = state.scViewMode || 'list';
  const header = `
    <div class="page-header">
      <h2 class="page-title">小班列表</h2>
      <div class="page-header-actions">
        <button class="sc-action-btn ${mode === 'list' ? 'primary' : ''}" data-action="sc-toggle-view" data-mode="list">列表</button>
        <button class="sc-action-btn ${mode === 'map' ? 'primary' : ''}" data-action="sc-toggle-view" data-mode="map">地图</button>
        <button class="sc-action-btn primary" data-action="go-survey-grid">📊 网格调查</button>
        <span class="sc-list-count">${rows.length} 个</span>
      </div>
    </div>
  `;
  if (mode === 'map') {
    return `<div class="page-sc-list">
      ${header}
      <div id="scMap" style="flex:1 1 auto; min-height:300px; border-radius:10px; overflow:hidden; margin:0 14px 14px;"></div>
    </div>`;
  }
  const searchBar = `
    <div class="sc-search-bar">
      <input id="scSearch" class="sc-search"
             placeholder="搜索：乡镇 村 林班 小班（空格分词，如「平山 39 1」或「39-1」）"
             autocomplete="off">
    </div>
  `;
  if (!rows.length) {
    return `<div class="page-sc-list">
      ${header}
      ${searchBar}
      <div class="empty-hint">${state.scAllRows.length ? '无匹配小班' : '该项目无小班数据'}</div>
    </div>`;
  }
  const county = state.project.township || '';
  const listHtml = rows.map(r => renderScListRow(r, county)).join('');
  return `<div class="page-sc-list">
    ${header}
    ${searchBar}
    <div class="sc-list">${listHtml}</div>
  </div>`;
}

function bindScListPage() {
  if (state.scViewMode === 'map') {
    setTimeout(() => initScMap(), 50);
  }
}

function renderScListRow(r, county) {
  const city = r.city || '';
  const township = r.township || '';
  const village = r.village || '';
  const fc = r.forest_compartment || '';
  const sc = r.subcompartment || '';
  const crumbs = [
    { label: '州市', val: city },
    { label: '乡镇', val: township },
    { label: '县', val: county },
    { label: '村', val: village },
    { label: '林班', val: fc },
    { label: '小班', val: sc },
  ].filter(c => c.val !== '' && c.val !== null && c.val !== undefined);
  const crumbHtml = crumbs.map(c =>
    `<span class="crumb"><span class="crumb-label">${escapeHtml(c.label)}</span><span class="crumb-val">${escapeHtml(c.val)}</span></span>`
  ).join('<span class="crumb-sep">›</span>');
  const metaParts = [];
  if (r.tending_area) metaParts.push(`抚育面积 ${r.tending_area} 亩`);
  if (r.tree_species) metaParts.push(`树种: ${r.tree_species}`);
  if (r.forest_type) metaParts.push(r.forest_type);
  if (r.ownership) metaParts.push(r.ownership);
  const meta = metaParts.join(' · ');
  return `<div class="sc-list-row" data-scid="${r.id}">
    <div class="sc-row-main">
      <div class="sc-row-crumbs">${crumbHtml}</div>
      ${meta ? `<div class="sc-row-meta">${escapeHtml(meta)}</div>` : ''}
    </div>
    <div class="sc-row-actions">
      <button class="sc-action-btn primary" data-action="sc-enter-survey" data-scid="${r.id}">调查</button>
      <button class="sc-action-btn" data-action="sc-edit" data-scid="${r.id}">编辑</button>
      <button class="sc-action-btn" data-action="sc-row-photos" data-scid="${r.id}">图片</button>
      <button class="sc-action-btn" data-action="sc-row-track" data-scid="${r.id}">轨迹</button>
      <button class="sc-action-btn" data-action="sc-row-checkin" data-scid="${r.id}">打卡</button>
    </div>
  </div>`;
}

// 客户端小班搜索（原服务端 api/subcompartments/search 已不存在）
let _scSearchTimer = null;
function onScSearchInput(val) {
  if (!state.scAllRows) return;
  if (_scSearchTimer) clearTimeout(_scSearchTimer);
  _scSearchTimer = setTimeout(() => {
    const q = (val || '').trim().toLowerCase();
    if (!q) {
      state.scFilteredRows = state.scAllRows;
    } else {
      const terms = q.split(/\s+/);
      state.scFilteredRows = state.scAllRows.filter(r => {
        const hay = [
          r.city, r.township, r.village,
          r.forest_compartment, r.subcompartment, r.subcompartment_label,
          r.tree_species, r.forest_type, r.ownership,
        ].filter(x => x != null).join(' ').toLowerCase();
        return terms.every(t => hay.indexOf(t) >= 0);
      });
    }
    const list = qs('.sc-list');
    if (list) {
      const county = (state.project && state.project.township) || '';
      if (!state.scFilteredRows.length) {
        list.innerHTML = '<div class="empty-hint">无匹配小班</div>';
      } else {
        list.innerHTML = state.scFilteredRows.map(r => renderScListRow(r, county)).join('');
      }
    }
    const cntEl = qs('.sc-list-count');
    if (cntEl) cntEl.textContent = `${state.scFilteredRows.length} 个`;
  }, 200);
}

// ── Leaflet 地图渲染 ──
function initScMap() {
  const mapEl = qs('#scMap');
  if (!mapEl) return;
  if (!window.L) { toast('地图库未加载'); return; }
  if (state._map) { state._map.remove(); state._map = null; }
  state._map = L.map('scMap').setView([24.3, 102.5], 11);
  // 卫星影像底图（Esri World Imagery，WGS-84，与现有小班面天然对齐，无需坐标转换）
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics',
    maxZoom: 19,
  }).addTo(state._map);
  fetch(`api/projects/${state.project.id}/geojson`)
    .then(r => r.ok ? r.json() : { type: 'FeatureCollection', features: [] })
    .then(geojson => {
      const features = geojson.features || [];
      if (!features.length) {
        toast('该项目暂无地图数据', 2500);
        return;
      }
      const layer = L.geoJSON(geojson, {
        style: { color: '#2e7d32', weight: 2, fillColor: '#4caf50', fillOpacity: 0.2 },
        onEachFeature: (feature, lyr) => {
          const props = feature.properties || {};
          const sc = matchPolygonToSc(props);
          const label = sc ? sc.subcompartment_label : (props['小班'] ? `小班 ${props['小班']}` : '未知小班');
          const propsHtml = Object.entries(props)
            .filter(([k]) => !k.startsWith('_'))
            .map(([k, v]) => `<b>${escapeHtml(k)}</b>: ${escapeHtml(v)}`).join('<br>');
          lyr.bindPopup(`<div style="min-width:180px"><b>${escapeHtml(label)}</b><br>${propsHtml}<br>` +
            `<button class="btn-confirm" style="margin-top:6px;width:100%" onclick="window._mapEnterSurvey('${sc ? sc.id : ''}')">调查此小班</button>` +
            `<button class="btn-secondary" style="margin-top:4px;width:100%" onclick="window._mapEditSubcompartment('${sc ? sc.id : ''}')">编辑小班属性</button></div>`);
        },
      }).addTo(state._map);
      try { state._map.fitBounds(layer.getBounds(), { padding: [20, 20] }); } catch (e) {}
    })
    .catch(e => toast('加载地图失败：' + e.message, 2500));
}

window._mapEnterSurvey = async function (scId) {
  if (!scId) { toast('未匹配到小班记录，请在列表中搜索'); return; }
  if (state._map) { state._map.remove(); state._map = null; }
  await goToSurvey(scId);
};

window._mapEditSubcompartment = async function (scId) {
  if (!scId) { toast('未匹配到小班记录，请在列表中搜索'); return; }
  await openEditSubcompartmentModal(scId);
};

// ── 编辑小班属性 ──
// 可编辑的关键字段（GDB 属性，改存 data_json，不动 GDB 源）
const EDITABLE_SC_FIELDS = [
  { key: '乡镇', label: '乡镇', type: 'text' },
  { key: '村', label: '村', type: 'text' },
  { key: '林班', label: '林班', type: 'text' },
  { key: '小班', label: '小班', type: 'text' },
  { key: '优势树', label: '优势树种', type: 'text' },
  { key: '林种', label: '林种', type: 'text' },
  { key: '土地权', label: '土地权属', type: 'text' },
  { key: '小班面', label: '小班面积(亩)', type: 'number' },
  { key: '经营面', label: '经营面积(亩)', type: 'number' },
  { key: '起源', label: '起源', type: 'text' },
  { key: '龄组', label: '龄组', type: 'text' },
  { key: '郁闭度', label: '郁闭度', type: 'text' },
  { key: '海拔', label: '海拔(m)', type: 'number' },
  { key: '坡向', label: '坡向', type: 'text' },
  { key: '坡度', label: '坡度', type: 'text' },
  { key: '坡位', label: '坡位', type: 'text' },
  { key: '土壤类', label: '土壤类型', type: 'text' },
  { key: '调查人', label: '调查人', type: 'text' },
  { key: '备注', label: '备注', type: 'textarea' },
];

async function openEditSubcompartmentModal(scId) {
  try {
    const j = await fetchJSON(`api/subcompartments/rows/${scId}`);
    const row = j.row;
    const data = row.data || {};
    const label = row.subcompartment_label || '小班';
    const root = qs('#modalRoot');
    const fieldsHtml = EDITABLE_SC_FIELDS.map(f => {
      const val = data[f.key] != null ? String(data[f.key]) : '';
      if (f.type === 'textarea') {
        return `<div class="sc-edit-field"><label>${escapeHtml(f.label)}</label>
          <textarea data-sc-key="${escapeHtml(f.key)}" rows="2">${escapeHtml(val)}</textarea></div>`;
      }
      return `<div class="sc-edit-field"><label>${escapeHtml(f.label)}</label>
        <input type="${f.type === 'number' ? 'number' : 'text'}"
               data-sc-key="${escapeHtml(f.key)}" value="${escapeHtml(val)}"></div>`;
    }).join('');
    root.innerHTML = `
      <div class="modal-mask" data-action="close-modal-mask">
        <div class="modal" id="scEditModal" onclick="event.stopPropagation()">
          <div class="modal-header">
            <h3>编辑小班属性 — ${escapeHtml(label)}</h3>
            <button class="btn-icon" data-action="close-modal">✕</button>
          </div>
          <div class="modal-body sc-edit-body">
            <div class="sc-edit-hint">修改后保存将更新本地数据，不影响原始 GDB 文件。</div>
            <div class="sc-edit-grid">${fieldsHtml}</div>
          </div>
          <div class="modal-footer">
            <button class="btn-secondary" data-action="close-modal">取消</button>
            <button class="btn-confirm" data-action="save-sc-edit" data-sc-id="${scId}">保存</button>
          </div>
        </div>
      </div>`;
  } catch (e) {
    toast('加载小班失败：' + e.message, 2500);
  }
}

async function saveSubcompartmentEdit(scId) {
  const modal = qs('#scEditModal');
  if (!modal) return;
  const inputs = modal.querySelectorAll('[data-sc-key]');
  const data = {};
  inputs.forEach(inp => {
    const key = inp.getAttribute('data-sc-key');
    data[key] = inp.value.trim();
  });
  try {
    const resp = await fetch(`api/subcompartments/rows/${scId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data }),
    });
    if (!resp.ok) {
      const e = await resp.json().catch(() => ({}));
      throw new Error(e.error || '保存失败');
    }
    const j = await resp.json();
    toast('小班属性已保存', 1500);
    closeModal();
    // 刷新小班列表缓存
    if (state.project) {
      try {
        const lj = await fetchJSON(`api/projects/${state.project.id}/subcompartments`);
        state.scAllRows = lj.rows || [];
        state.scFilteredRows = state.scAllRows.slice();
      } catch (e) {}
    }
  } catch (e) {
    toast('保存失败：' + e.message, 2500);
  }
}

function matchPolygonToSc(props) {
  const rows = state.scAllRows || [];
  const township = String(props['乡镇'] || '').trim();
  const fc = String(props['林班'] || '').trim();
  const sc = String(props['小班'] || '').trim();
  let match = rows.find(r =>
    String(r.township || '').trim() === township &&
    String(r.forest_compartment || '').trim() === fc &&
    String(r.subcompartment || '').trim() === sc
  );
  if (match) return match;
  match = rows.find(r =>
    String(r.forest_compartment || '').trim() === fc &&
    String(r.subcompartment || '').trim() === sc
  );
  if (match) return match;
  if (sc) {
    match = rows.find(r => String(r.subcompartment || '').trim() === sc);
  }
  return match || null;
}

// ════════════════════════════════════════════
// Page 3: 调查填写页（5 表录入）
// ════════════════════════════════════════════

// ════════════════════════════════════════════
// Page: 网格调查页（jspreadsheet，行=小班，列=字段）
// ════════════════════════════════════════════

// 网格可编辑表清单（一对一模型：所有表顶层，无子表）
function gridTableList() {
  const tables = (state.schema && state.schema.tables) || [];
  return tables.map(t => ({ id: t.id, name: t.name }));
}

// 按 id 解析网格表定义
function getGridTableDef(tableId) {
  if (!state.schema) return null;
  return state.schema.tables.find(t => t.id === tableId) || null;
}

async function goToSurveyGrid() {
  if (!state.project) { goToProjects(); return; }
  // 加载该项目的全部小班（含 prefilled）
  try {
    const j = await fetchJSON(`api/projects/${state.project.id}/subcompartments`);
    state.scAllRows = j.rows || [];
    state.scFilteredRows = state.scAllRows;
    const pfMap = {};
    state.scAllRows.forEach(r => { pfMap[r.id] = r.prefilled || {}; });
    state.subcompartmentPrefilledMap = pfMap;
  } catch (e) {
    state.scAllRows = [];
    state.subcompartmentPrefilledMap = {};
  }
  // 加载该项目的分类清单
  try {
    const cj = await fetchJSON(`api/projects/${state.project.id}/categories`);
    state.gridCategories = cj.categories || [];
  } catch (e) {
    state.gridCategories = [];
  }
  // 默认选第一个分类（若有）
  if (state.gridCategories.length && !state.gridCategory) {
    state.gridCategory = state.gridCategories[0];
    state.gridTable = CATEGORY_TO_TABLE[state.gridCategory] || 'table1';
  }
  // 按分类过滤小班
  await loadGridScRows();
  state.view = 'survey_grid';
  renderApp();
}

// 按分类过滤小班（不再按 project_name）
async function loadGridScRows() {
  if (!state.project) { state.gridScRows = []; return; }
  const cat = state.gridCategory;
  if (!cat) { state.gridScRows = state.scAllRows || []; return; }
  state.gridScRows = (state.scAllRows || []).filter(r => (r.category || '') === cat);
}

function renderSurveyGridPage() {
  if (!state.project) {
    return `<div class="page-survey-grid"><div class="empty-hint">未选择项目</div></div>`;
  }
  return `<div class="page-survey-grid">
    <div id="gridToolbarWrap">${renderGridToolbar()}</div>
    <div id="gridContainer" class="grid-container"></div>
  </div>`;
}

// 工具栏级联：项目 → 分类 → 乡 → 村 → 小班（已去除林班）
function renderGridToolbar() {
  const hint = '选定小班后两列编辑，自动保存';
  const baseRows = state.gridScRows || state.scAllRows || [];
  const projects = state.projects || [];

  // 项目下拉
  const pfHtml = projects.map(p =>
    `<option value="${escapeHtml(p.id)}" ${state.project && p.id === state.project.id ? 'selected' : ''}>${escapeHtml(p.name)}</option>`
  ).join('');

  // 分类下拉
  const catOpts = (state.gridCategories || []).map(c =>
    `<option value="${escapeHtml(c)}" ${c === state.gridCategory ? 'selected' : ''}>${escapeHtml(c)}</option>`
  ).join('');
  const catHtml = `<select id="gridCategoryFilter" data-action="grid-filter-category" title="分类">
    <option value="">全部分类</option>
    ${catOpts}
  </select>`;

  // 乡镇：分类过滤后的全部小班
  const townships = distinctVals(baseRows, 'township').sort();
  const tfHtml = townships.map(tw =>
    `<option value="${escapeHtml(tw)}" ${tw === state.gridTownship ? 'selected' : ''}>${escapeHtml(tw)}</option>`
  ).join('');

  // 村：受已选乡镇级联
  const afterTown = state.gridTownship ? baseRows.filter(r => r.township === state.gridTownship) : baseRows;
  const villages = distinctVals(afterTown, 'village').sort();
  const vfHtml = villages.map(v =>
    `<option value="${escapeHtml(v)}" ${v === state.gridVillage ? 'selected' : ''}>${escapeHtml(v)}</option>`
  ).join('');

  // 小班下拉（级联到最后）
  const scRows = getGridFilteredRows();
  const scOpts = scRows.map(r =>
    `<option value="${escapeHtml(r.id)}" ${state.gridSubcompartment && r.id === state.gridSubcompartment.id ? 'selected' : ''}>${escapeHtml(r.subcompartment_label || '')}</option>`
  ).join('');
  const scHtml = `<select id="gridSubcompartmentFilter" data-action="grid-filter-subcompartment" title="小班">
    <option value="">选择小班</option>
    ${scOpts}
  </select>`;

  // 操作按钮（选定小班后显示）
  const scBtns = state.gridSubcompartment ? `
    <button class="btn-grid-action" data-action="sc-photo">照片</button>
    <button class="btn-grid-action" data-action="sc-track">轨迹</button>
    <button class="btn-grid-action" data-action="sc-checkin">打卡</button>
  ` : '';

  return `<div class="grid-toolbar">
    <select id="gridProjectFilter" data-action="grid-filter-project" title="切换项目">
      ${pfHtml}
    </select>
    ${catHtml}
    <select id="gridTownshipFilter" data-action="grid-filter-township" title="乡镇">
      <option value="">全部乡镇</option>
      ${tfHtml}
    </select>
    <select id="gridVillageFilter" data-action="grid-filter-village" title="村">
      <option value="">全部村</option>
      ${vfHtml}
    </select>
    ${scHtml}
    ${scBtns}
    <span class="grid-hint">${hint}</span>
  </div>`;
}

// 按分类+县+乡+村过滤后的小班列表
function getGridFilteredRows() {
  let rows = state.gridScRows || state.scAllRows || [];
  if (state.gridTownship) rows = rows.filter(r => r.township === state.gridTownship);
  if (state.gridVillage) rows = rows.filter(r => r.village === state.gridVillage);
  return rows;
}

function distinctVals(rows, key) {
  return Array.from(new Set((rows || []).map(r => r[key] || '').filter(Boolean)));
}

// 仅重渲染工具栏（级联筛选后刷新下游选项，不重建网格）
function refreshGridToolbar() {
  const wrap = qs('#gridToolbarWrap');
  if (wrap) wrap.innerHTML = renderGridToolbar();
}

async function bindSurveyGridPage() {
  if (!window.jspreadsheet) { toast('网格库未加载'); return; }
  await renderSurveyForm();
}

// 加载选定小班的当前表调查数据
async function loadGridSubcompartmentData() {
  if (!state.gridSubcompartment || !state.gridTable) { state._gridSurveyMap = {}; return; }
  try {
    const url = `api/projects/${state.project.id}/survey/${state.gridTable}/rows`;
    const j = await fetchJSON(url);
    const map = {};
    (j.rows || []).forEach(r => { map[r.subcompartment_id] = r.data || {}; });
    state._gridSurveyMap = map;
  } catch (e) { state._gridSurveyMap = {}; }
}

// 公式计算：根据 formula 名和数据计算 computed 字段值
function computeFieldValue(formula, data) {
  const sVals = [1,2,3,4,5].map(i => data['survival_'+i]).filter(v => v !== '' && v != null && !isNaN(Number(v)));
  switch (formula) {
    case 't1_sample_count':
      return sVals.length || '';
    case 't1_avg_survival': {
      if (!sVals.length) return '';
      const sum = sVals.reduce((s, v) => s + Number(v), 0);
      return (sum / sVals.length).toFixed(2);
    }
    case 't1_avg_survival_rate': {
      if (!sVals.length) return '';
      const sum = sVals.reduce((s, v) => s + Number(v), 0);
      const avg = sum / sVals.length;
      const sa = Number(data['sample_area']) || 0;
      const ma = Number(data['mu_area']) || 0;
      const mc = Number(data['mu_design_count']) || 0;
      if (!sa || !ma || !mc) return '';
      return ((avg * ma) / (sa * mc)).toFixed(6);
    }
    default: return '';
  }
}

// 默认模式两列表格：选定小班后，渲染该小班当前表的两列 Excel（左 label，右值）
async function renderSurveyForm() {
  const container = qs('#gridContainer');
  if (!container) return;
  if (!state.gridSubcompartment) {
    if (state._grid) { try { state._grid.destroy(); } catch (e) {} state._grid = null; }
    container.innerHTML = '<div class="empty-hint">请选择小班</div>';
    state._gridRowFields = [];
    return;
  }
  const sc = state.gridSubcompartment;
  const tdef = getGridTableDef(state.gridTable);
  if (!tdef) {
    if (state._grid) { try { state._grid.destroy(); } catch (e) {} state._grid = null; }
    container.innerHTML = '<div class="empty-hint">未找到表定义</div>';
    return;
  }
  // 加载该小班的调查数据
  await loadGridSubcompartmentData();
  const pf = sc.prefilled || state.subcompartmentPrefilledMap[sc.id] || {};
  const sv = state._gridSurveyMap[sc.id] || {};
  const prefilledCols = tdef.prefilled_columns || [];
  const inputCols = tdef.input_columns || [];
  // 构建两列数据 + 行字段信息
  const rowFields = [];
  const data = [];
  prefilledCols.forEach(p => {
    rowFields.push({ kind: 'prefilled', key: p.key, label: p.label, type: p.type || 'text' });
    const v = pf[p.key] != null ? String(pf[p.key]) : '';
    data.push([p.label, v]);
  });
  inputCols.forEach(f => {
    // sample_array 在默认模式不显示
    if (f.type === 'sample_array') return;
    if (f.type === 'photo' || f.type === 'gps' || f.type === 'checkin' || f.type === 'track') return;
    // computed 字段：只读 + 自动计算
    if (f.type === 'computed') {
      rowFields.push({ kind: 'computed', key: f.key, label: f.label, type: f.type, formula: f.formula });
      const cv = computeFieldValue(f.formula, sv);
      data.push([f.label, String(cv)]);
      return;
    }
    // readOnly 输入字段（从密点文件读取等）
    rowFields.push({ kind: 'input', key: f.key, label: f.label, type: f.type, options: f.options || [], readOnly: !!f.readOnly });
    let v = sv[f.key];
    if (v == null) v = '';
    // 只读字段空值时回填小班预填值（每亩面积/每亩设计株树 等）
    if (f.readOnly && v === '' && pf[f.key] != null) v = String(pf[f.key]);
    if (f.type === 'checkbox') v = (v === true || v === 'true' || v === 1 || v === '1' || v === '有') ? '是' : '否';
    data.push([f.label, String(v)]);
  });
  state._gridRowFields = rowFields;
  // 列定义：字段 / 值
  const cols = [
    { title: '字段', width: 140, type: 'text', readOnly: true },
    { title: '值', width: 200, type: 'text', readOnly: false },
  ];
  if (state._grid) { try { state._grid.destroy(); } catch (e) {} state._grid = null; }
  container.innerHTML = '<div id="gridEl"></div>';
  // 预填/computed/readOnly 单元格只读配置
  const cellsConfig = {};
  rowFields.forEach((f, i) => {
    if (f.kind === 'prefilled' || f.kind === 'computed' || (f.kind === 'input' && f.readOnly)) {
      cellsConfig[`B${i + 1}`] = { readOnly: true };
    }
  });
  // 表格高度：行高28px × 行数 + 表头32 + 边距，让所有行完整显示，外层容器滚动
  const rowH = 28;
  const headerH = 32;
  const tableH = data.length * rowH + headerH + 4;
  state._grid = jspreadsheet(qs('#gridEl'), {
    data: data,
    columns: cols,
    cells: cellsConfig,
    contextMenu: false,
    allowInsertRow: false,
    allowManualInsertRow: false,
    allowDeleteRow: false,
    tableOverflow: false,
    tableWidth: '100%',
    onchange: (instance, cell, x, y, value) => onGridCellChange(x, y, value),
  });
  // 让 gridEl 内的 jss 容器和 table 自然撑开高度，不截断
  const gridEl2 = qs('#gridEl');
  if (gridEl2) {
    gridEl2.style.overflow = 'visible';
    const jss = gridEl2.querySelector('.jss');
    if (jss) {
      jss.style.overflow = 'visible';
      jss.style.maxHeight = 'none';
      jss.style.height = 'auto';
    }
    const tbl = gridEl2.querySelector('table');
    if (tbl) tbl.style.height = 'auto';
  }
  void tableH;
  // 后处理：单元格背景色 + enum 下拉框
  const gridEl = qs('#gridEl');
  if (gridEl) {
    rowFields.forEach((f, i) => {
      const td = gridEl.querySelector(`tbody tr:nth-child(${i + 1}) td:nth-child(3)`);
      if (!td) return;
      if (f.kind === 'prefilled' || (f.kind === 'input' && f.readOnly)) {
        td.style.backgroundColor = '#fff3cd';
      } else if (f.kind === 'computed') {
        td.style.backgroundColor = '#e3f2fd';
      }
      // enum 字段注入 select 下拉框
      if (f.kind === 'input' && f.type === 'enum' && f.options && f.options.length && !f.readOnly) {
        const curVal = data[i][1] || '';
        const select = document.createElement('select');
        select.className = 'cell-enum-select';
        select.style.cssText = 'width:100%;height:100%;border:none;background:transparent;font-size:13px;cursor:pointer;';
        select.innerHTML = '<option value=""></option>' + f.options.map(o =>
          `<option value="${escapeHtml(o)}" ${o === curVal ? 'selected' : ''}>${escapeHtml(o)}</option>`
        ).join('');
        select.addEventListener('change', () => {
          const val = select.value;
          state._grid.setValueFromCoords(1, i, val);
        });
        td.innerHTML = '';
        td.appendChild(select);
      }
      // checkbox 字段注入 是/否 下拉框
      if (f.kind === 'input' && f.type === 'checkbox' && !f.readOnly) {
        const curVal = data[i][1] === '是' ? '是' : '否';
        const select = document.createElement('select');
        select.className = 'cell-enum-select';
        select.style.cssText = 'width:100%;height:100%;border:none;background:transparent;font-size:13px;cursor:pointer;';
        select.innerHTML = `<option value="是" ${curVal === '是' ? 'selected' : ''}>是</option>`
          + `<option value="否" ${curVal === '否' ? 'selected' : ''}>否</option>`;
        select.addEventListener('change', () => {
          state._grid.setValueFromCoords(1, i, select.value);
        });
        td.innerHTML = '';
        td.appendChild(select);
      }
    });
  }
  // 表格下方签字卡片：验收人员 / 配合验收人员
  renderSignCards(sc, sv);
}

// 签字卡片：表格下方两个签字区
function renderSignCards(sc, sv) {
  const container = qs('#gridContainer');
  if (!container) return;
  let signBar = qs('#signBar');
  if (!signBar) {
    signBar = document.createElement('div');
    signBar.id = 'signBar';
    signBar.className = 'sign-bar';
    container.appendChild(signBar);
  }
  const inspectorSign = sv.inspector_sign || '';
  const coSign = sv.co_inspector_sign || '';
  signBar.innerHTML = `
    <div class="sign-card" data-action="sign-open" data-key="inspector_sign">
      <div class="sign-title">验收人员签字</div>
      <div class="sign-area">${inspectorSign
      ? `<img src="${inspectorSign}" alt="签名">`
      : '<span class="sign-placeholder">点击签字</span>'}</div>
    </div>
    <div class="sign-card" data-action="sign-open" data-key="co_inspector_sign">
      <div class="sign-title">配合验收人员签字</div>
      <div class="sign-area">${coSign
      ? `<img src="${coSign}" alt="签名">`
      : '<span class="sign-placeholder">点击签字</span>'}</div>
    </div>
  `;
}

// 签字 modal：全屏 canvas 签字
function openSignModal(key) {
  let modal = qs('#signModal');
  if (modal) modal.remove();
  modal = document.createElement('div');
  modal.id = 'signModal';
  modal.className = 'sign-modal';
  modal.innerHTML = `
    <div class="sign-modal-box">
      <div class="sign-modal-header">
        <span class="sign-modal-title">${key === 'inspector_sign' ? '验收人员' : '配合验收人员'}签字</span>
        <button class="sign-modal-close" data-action="sign-close">×</button>
      </div>
      <canvas id="signCanvas" class="sign-canvas"></canvas>
      <div class="sign-modal-actions">
        <button class="btn-grid-action" data-action="sign-clear">清除</button>
        <button class="btn-grid-action btn-primary" data-action="sign-save">保存</button>
      </div>
    </div>
  `;
  app.appendChild(modal);
  modal.style.display = 'flex';
  // 初始化 canvas
  const canvas = qs('#signCanvas');
  const box = modal.querySelector('.sign-modal-box');
  const rect = box.getBoundingClientRect();
  canvas.width = Math.min(rect.width - 24, window.innerWidth - 24);
  canvas.height = Math.min(window.innerHeight - 180, 360);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = '#1a1a1a';
  ctx.lineWidth = 2.5;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  let drawing = false;
  let lastX = 0, lastY = 0;
  const getPos = (e) => {
    const r = canvas.getBoundingClientRect();
    const t = e.touches ? e.touches[0] : e;
    return { x: t.clientX - r.left, y: t.clientY - r.top };
  };
  const start = (e) => { e.preventDefault(); drawing = true; const p = getPos(e); lastX = p.x; lastY = p.y; };
  const move = (e) => {
    if (!drawing) return;
    e.preventDefault();
    const p = getPos(e);
    ctx.beginPath();
    ctx.moveTo(lastX, lastY);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    lastX = p.x; lastY = p.y;
  };
  const end = () => { drawing = false; };
  canvas.addEventListener('mousedown', start);
  canvas.addEventListener('mousemove', move);
  canvas.addEventListener('mouseup', end);
  canvas.addEventListener('mouseleave', end);
  canvas.addEventListener('touchstart', start, { passive: false });
  canvas.addEventListener('touchmove', move, { passive: false });
  canvas.addEventListener('touchend', end);
  modal._signKey = key;
  modal._signCanvas = canvas;
}

function closeSignModal() {
  const modal = qs('#signModal');
  if (modal) modal.remove();
}

function clearSignCanvas() {
  const canvas = qs('#signCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

async function saveSign() {
  const modal = qs('#signModal');
  if (!modal) return;
  const key = modal._signKey;
  const canvas = modal._signCanvas;
  if (!key || !canvas) return;
  // 检查是否有内容（非全白）
  const ctx = canvas.getContext('2d');
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let hasInk = false;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i] < 250 || data[i + 1] < 250 || data[i + 2] < 250) { hasInk = true; break; }
  }
  if (!hasInk) { toast('请先签字', 1500); return; }
  const dataUrl = canvas.toDataURL('image/png');
  closeSignModal();
  // 保存到 data_json
  const sc = state.gridSubcompartment;
  if (!sc) return;
  const existing = Object.assign({}, state._gridSurveyMap[sc.id] || {});
  existing[key] = dataUrl;
  existing['inspector'] = state.user || '';
  state._gridSurveyMap[sc.id] = existing;
  try {
    await fetch(`api/projects/${state.project.id}/survey/${state.gridTable}/rows`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subcompartment_id: sc.id, data: existing, inspector: state.user || '' }),
    });
    toast('签字已保存', 1200);
  } catch (e) {
    toast('保存失败：' + e.message, 2200);
  }
  // 刷新签字卡片
  renderSignCards(sc, existing);
}

// 根据样方模式选择渲染入口
function initSurveyGrid() {
  renderSurveyForm();
}

// 单元格变更：针对选定小班保存（两列表格：y 行对应字段索引）
async function onGridCellChange(x, y, value) {
  if (!state.gridSubcompartment) return;
  const sc = state.gridSubcompartment;
  const rowFields = state._gridRowFields || [];
  const f = rowFields[y];
  if (!f || f.kind !== 'input' || f.readOnly) return; // 预填/computed/readOnly 行忽略
  const existing = Object.assign({}, state._gridSurveyMap[sc.id] || {});
  let v = value;
  if (f.type === 'checkbox') v = (value === true || value === 'true' || value === 1 || value === '1' || value === '是' || value === '有');
  if (f.type === 'percent') v = value === '' ? '' : Number(value);
  if (f.type === 'number') v = value === '' ? '' : Number(value);
  existing[f.key] = v;
  existing['inspector'] = state.user || '';
  // 重算 computed 字段并更新网格
  rowFields.forEach((cf, idx) => {
    if (cf.kind !== 'computed') return;
    const cv = computeFieldValue(cf.formula, existing);
    existing[cf.key] = cv;
    if (state._grid && cv !== '') state._grid.setValueFromCoords(1, idx, cv);
  });
  state._gridSurveyMap[sc.id] = existing;
  try {
    await fetch(`api/projects/${state.project.id}/survey/${state.gridTable}/rows`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subcompartment_id: sc.id, data: existing, inspector: state.user || '' }),
    });
  } catch (e) {
    toast('保存失败：' + e.message, 2200);
  }
}

function renderSurveyPage() {
  if (!state.subcompartment) {
    return `<div class="page-survey"><div class="empty-hint">未选择小班</div></div>`;
  }
  return `<div class="page-survey">
    <nav class="tabs-wrap" id="tabs"></nav>
    <div class="progress-wrap" id="progress"></div>
    <main class="content" id="content"></main>
  </div>`;
}

function bindSurveyPage() {
  renderTableTabs();
  renderProgress();
  renderContent();
}

function renderTableTabs() {
  const wrap = qs('#tabs');
  if (!wrap || !state.schema) return;
  wrap.innerHTML = state.schema.tables.map(t => {
    const cnt = countRecords(t.id);
    const active = t.id === state.currentTable ? 'active' : '';
    return `<button class="tab-btn ${active}" data-action="switch-table" data-table="${t.id}">
      ${escapeHtml(t.name.replace('验收', '').replace('现场', ''))}
      <span class="tab-count">${cnt}</span>
    </button>`;
  }).join('');
}

// ── 导航 ──
function goToProjects() {
  if (state._map) { state._map.remove(); state._map = null; }
  state.view = 'projects';
  state.subcompartment = null;
  state.subcompartmentData = null;
  state.scExtras = null;
  state.scAllRows = [];
  state.scFilteredRows = [];
  renderApp();
}

async function goToScList() {
  if (!state.project) { goToProjects(); return; }
  if (state._map) { state._map.remove(); state._map = null; }
  state.view = 'sc_list';
  state.subcompartment = null;
  state.subcompartmentData = null;
  state.scViewMode = 'list';
  try {
    const j = await fetchJSON(`api/projects/${state.project.id}/subcompartments`);
    state.scAllRows = j.rows || [];
    state.scFilteredRows = state.scAllRows;
    // 构建预填映射（网格黄色列直接取值）
    const pfMap = {};
    state.scAllRows.forEach(r => { pfMap[r.id] = r.prefilled || {}; });
    state.subcompartmentPrefilledMap = pfMap;
  } catch (e) {
    state.scAllRows = [];
    state.scFilteredRows = [];
    state.subcompartmentPrefilledMap = {};
  }
  renderApp();
}

async function goToSurvey(scId) {
  if (!scId) return;
  if (state._map) { state._map.remove(); state._map = null; }
  try {
    const j = await fetchJSON(`api/subcompartments/rows/${scId}`);
    state.subcompartment = j.row;
    state.subcompartmentData = { prefilled: j.prefilled || {}, row: j.row };
    state.scExtras = j.extras || null;
    state.view = 'survey';
    if (state.project && state.currentTable) {
      await loadRecords(state.currentTable);
      initFormDataForActive();
    }
    renderApp();
  } catch (e) {
    toast('加载小班信息失败：' + e.message, 2500);
  }
}

function countRecords(tid) {
  return (state.records[tid] || []).length;
}

function countActiveRecords() {
  const tid = state.currentTable;
  let recs = state.records[tid] || [];
  if (state.subcompartment) {
    recs = recs.filter(r => (r.subcompartment_id || '') === state.subcompartment.id);
  }
  return recs.length;
}

function renderProgress() {
  const wrap = qs('#progress');
  if (!wrap) return;
  const def = getTableDef(state.currentTable);
  if (!def) { wrap.innerHTML = ''; return; }
  const target = def.data_rows || 5;
  const filled = countActiveRecords();
  const pct = target > 0 ? Math.min(100, Math.round(filled / target * 100)) : 0;
  wrap.innerHTML = `
    <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
    <div class="progress-label"><span>${escapeHtml(def.name)}</span><span>已填 ${filled} / 目标 ${target}</span></div>
  `;
}

function renderContent() {
  const c = qs('#content');
  if (!c) return;
  if (!state.project) {
    c.innerHTML = `<div class="empty-hint">请返回选择项目</div>`;
    return;
  }
  c.innerHTML = `
    <section class="prefilled" id="prefilled">${renderPrefilledInfo()}</section>
    <section class="edit-box" id="editBox">${renderEditBox()}</section>
  `;
}

function renderPrefilledInfo() {
  const cols = activePrefilledColumns();
  let data = null;
  if (state.subcompartmentData && state.subcompartmentData.prefilled) {
    data = state.subcompartmentData.prefilled;
  }
  if (!cols.length) {
    if (state.subcompartment) {
      return `<div class="prefilled-title">📋 当前小班：<b>${escapeHtml(state.subcompartment.subcompartment_label)}</b></div>
        <div class="prefilled-empty">本表无预填列</div>`;
    }
    return `<div class="prefilled-title">📋 预填信息</div><div class="prefilled-empty">本表无预填列</div>`;
  }
  const items = cols.map(f => {
    let v = data ? data[f.key] : '';
    if (v === undefined || v === null || v === '') v = '—';
    if (f.unit) v = `${v}${f.unit}`;
    return `<span class="prefilled-item"><span class="pf-label">${escapeHtml(f.label)}:</span><span class="pf-val">${escapeHtml(v)}</span></span>`;
  }).join('');
  const scLabel = state.subcompartment ? state.subcompartment.subcompartment_label : '';
  const title = scLabel
    ? `📋 当前小班：<b>${escapeHtml(scLabel)}</b>`
    : `📋 预填信息`;
  return `<div class="prefilled-title">${title}</div>
    <div class="prefilled-grid">${items}</div>`;
}

// 调查↔小班 一对一：找到当前小班+当前表对应的唯一记录
function findExistingRecord() {
  if (!state.subcompartment || !state.currentTable) return null;
  const recs = state.records[state.currentTable] || [];
  const scId = state.subcompartment.id;
  return recs.find(r => (r.subcompartment_id || '') === scId) || null;
}

// 编辑框：替换原来的「已填记录列表 + 底部输入栏」，实现一对一编辑
function renderEditBox() {
  if (!state.subcompartment) {
    return `<div class="empty-hint">未选择小班，请先在小班列表中选择后再填写调查</div>`;
  }
  const def = activeDef();
  if (!def) return '';
  const groups = computeGroups(activeInputColumns());
  const groupsHtml = groups.map(g => renderGroup(g)).join('');
  const modeBadge = state.editRecordId
    ? `<span class="edit-mode-badge">已存在记录 · 编辑模式</span>`
    : `<span class="edit-mode-badge edit-new">新建记录</span>`;
  return `
    <div class="editbox-header">
      <span class="inputbar-title">✏️ ${escapeHtml(def.name)}</span>
      ${modeBadge}
      <button class="btn-collapse-all" data-action="collapse-all">全部收起</button>
    </div>
    <div class="editbox-scroll">${groupsHtml}</div>
    <button class="btn-add" data-action="save-survey">保存</button>
  `;
}

function renderGroup(g) {
  const key = groupKey(g.name);
  const collapsed = state.collapsed[key] ? 'collapsed' : '';
  const fieldsHtml = g.fields.map(f => renderField(f)).join('');
  return `<div class="group ${collapsed}" data-group-key="${escapeHtml(key)}">
    <div class="group-head" data-action="toggle-group" data-group="${escapeHtml(key)}">
      <span>${escapeHtml(g.name)}</span><span class="arrow">▼</span>
    </div>
    <div class="group-body">${fieldsHtml}</div>
  </div>`;
}

function groupKey(name) {
  return `${state.currentTable}|${name}`;
}

function renderField(f) {
  const span = f.col_span || 'full';
  const req = f.required ? '<span class="req">*</span>' : '';
  const unit = f.unit ? `(${f.unit})` : '';
  const label = `<label>${escapeHtml(f.label)}${unit}${req}</label>`;
  return `<div class="field col-${span}">${label}${renderControl(f)}</div>`;
}

function renderControl(f) {
  const v = state.formData[f.key];
  switch (f.type) {
    case 'enum':
      return `<div class="enum-btns">${(f.options || []).map(opt =>
        `<button class="enum-btn ${v === opt ? 'active' : ''}" data-action="enum-select" data-field="${f.key}" data-value="${escapeHtml(opt)}">${escapeHtml(opt)}</button>`
      ).join('')}</div>`;
    case 'number': {
      const attrs = numAttrs(f);
      return `<input type="number" class="f-input" data-field="${f.key}" value="${escapeHtml(v)}" ${attrs}>`;
    }
    case 'percent':
      return `<div class="input-with-suffix"><input type="number" class="f-input" data-field="${f.key}" value="${escapeHtml(v)}" min="0" max="100" step="1"><span class="suffix">%</span></div>`;
    case 'date':
      return `<input type="date" class="f-input" data-field="${f.key}" value="${escapeHtml(v)}">`;
    case 'text':
      return `<input type="text" class="f-input" data-field="${f.key}" value="${escapeHtml(v)}" placeholder="请输入">`;
    case 'textarea':
      return `<textarea class="f-input f-textarea" data-field="${f.key}" rows="2" placeholder="请输入">${escapeHtml(v)}</textarea>`;
    case 'checkbox': {
      const on = !!v;
      return `<div class="toggle-row">
        <button class="toggle ${on ? 'on' : ''}" data-action="toggle-checkbox" data-field="${f.key}"><span class="toggle-knob"></span></button>
        <span class="toggle-label ${on ? 'yes' : 'no'}">${on ? '有' : '无'}</span>
      </div>`;
    }
    case 'gps':
      return `<div class="gps-row">
        <span class="gps-val" data-gps-val="${f.key}">${v ? escapeHtml(v) : '--'}</span>
        <button class="btn-gps" data-action="get-gps" data-field="${f.key}">获取GPS</button>
      </div>`;
    case 'photo':
      return `<div class="photo-row">
        <span class="photo-name" data-photo-name="${f.key}">${v ? escapeHtml(v) : '未拍照'}</span>
        <label class="btn-photo">拍照/选择
          <input type="file" accept="image/*" capture="environment" class="f-photo" data-field="${f.key}" hidden>
        </label>
      </div>`;
    case 'sample_array':
      return renderSamplePanel(f);
    default:
      return `<input type="text" class="f-input" data-field="${f.key}" value="${escapeHtml(v)}">`;
  }
}

// ── 样方子数组编辑面板（table5 samples）──
function renderSamplePanel(f) {
  const arr = state.formData[f.key];
  const samples = Array.isArray(arr) ? arr : [];
  let html = `<div class="sample-panel">
    <div class="sample-panel-head">
      <span class="sample-count">共 ${samples.length} 个样方</span>
      <button class="btn-sample-add" data-action="sample-add" data-sample-key="${f.key}">+ 添加样方</button>
    </div>`;
  if (!samples.length) {
    html += `<div class="sample-empty">暂无样方，点击「添加样方」开始录入</div>`;
  } else {
    html += `<div class="sample-list">`;
    samples.forEach((s, i) => { html += renderSampleCard(f, s, i); });
    html += `</div>`;
  }
  html += `</div>`;
  return html;
}

function renderSampleCard(f, sample, idx) {
  const fieldsHtml = (f.sample_fields || []).map(sf => renderSampleField(sf, sample, idx, f.key)).join('');
  return `<div class="sample-card" data-sample-idx="${idx}">
    <div class="sample-card-head">
      <span class="sample-card-title">样方 ${idx + 1}</span>
      <button class="btn-sample-del" data-action="sample-del" data-sample-key="${f.key}" data-sample-idx="${idx}" title="删除样方">✕</button>
    </div>
    <div class="sample-card-body">${fieldsHtml}</div>
  </div>`;
}

function renderSampleField(sf, sample, idx, sampleKey) {
  const v = sample[sf.key];
  const req = sf.required ? '<span class="req">*</span>' : '';
  const unit = sf.unit ? `(${sf.unit})` : '';
  const label = `<label>${escapeHtml(sf.label)}${unit}${req}</label>`;
  const ctrl = renderSampleControl(sf, v, sampleKey, idx);
  const span = sf.col_span || 'half';
  return `<div class="field col-${span}">${label}${ctrl}</div>`;
}

function renderSampleControl(sf, v, sampleKey, idx) {
  const da = `data-sample-key="${sampleKey}" data-sample-idx="${idx}" data-sample-field="${sf.key}"`;
  switch (sf.type) {
    case 'number': {
      const attrs = numAttrs(sf);
      return `<input type="number" class="f-input" ${da} value="${escapeHtml(v)}" ${attrs}>`;
    }
    case 'percent':
      return `<div class="input-with-suffix"><input type="number" class="f-input" ${da} value="${escapeHtml(v)}" min="0" max="100" step="1"><span class="suffix">%</span></div>`;
    case 'date':
      return `<input type="date" class="f-input" ${da} value="${escapeHtml(v)}">`;
    case 'text':
      return `<input type="text" class="f-input" ${da} value="${escapeHtml(v)}" placeholder="请输入">`;
    case 'textarea':
      return `<textarea class="f-input f-textarea" ${da} rows="2" placeholder="请输入">${escapeHtml(v)}</textarea>`;
    case 'gps':
      // 样方 GPS：经度/纬度同填，按钮同时填两个字段
      return `<div class="gps-row">
        <span class="gps-val" data-gps-val="${sf.key}" data-sample-key="${sampleKey}" data-sample-idx="${idx}">${v != null && v !== '' ? escapeHtml(v) : '--'}</span>
        <button class="btn-gps" data-action="get-gps-sample" data-sample-key="${sampleKey}" data-sample-idx="${idx}">获取GPS</button>
      </div>`;
    default:
      return `<input type="text" class="f-input" ${da} value="${escapeHtml(v)}">`;
  }
}

function numAttrs(f) {
  let s = '';
  if (f.min !== undefined) s += `min="${f.min}" `;
  if (f.max !== undefined) s += `max="${f.max}" `;
  s += `step="${f.step || 'any'}" `;
  return s;
}

// ── 数据加载 ──
async function loadRecords(tid) {
  if (!state.project) { state.records[tid] = []; return; }
  try {
    // 一对一模型：走 survey/<tid>/rows 端点，返回 {rows: [...]}
    const j = await fetchJSON(`api/projects/${state.project.id}/survey/${tid}/rows`);
    state.records[tid] = j.rows || [];
  } catch (e) {
    state.records[tid] = [];
  }
}

// ── 切换表 ──
async function switchTable(tableId) {
  if (tableId === state.currentTable) return;
  state.currentTable = tableId;
  await loadRecords(tableId);
  initFormDataForActive();
  renderTableTabs();
  renderProgress();
  renderContent();
}

// ── 保存（调查↔小班 一对一：upsert，有则更新无则新建）──
async function saveSurvey() {
  if (state.busy) return;
  const def = activeDef();
  if (!def) return;
  if (!state.subcompartment) {
    toast('请先从小班列表选择小班');
    return;
  }
  for (const f of (def.input_columns || [])) {
    // sample_array 字段：校验每个样方条目的必填项
    if (f.type === 'sample_array') {
      const arr = state.formData[f.key];
      if (Array.isArray(arr)) {
        for (let i = 0; i < arr.length; i++) {
          for (const sf of (f.sample_fields || [])) {
            if (sf.required && (arr[i][sf.key] === undefined || arr[i][sf.key] === null || arr[i][sf.key] === '')) {
              toast(`样方${i + 1}：请填写 ${sf.label}`);
              return;
            }
          }
        }
      }
      continue;
    }
    if (f.required) {
      const v = state.formData[f.key];
      if (v === undefined || v === null || v === '') {
        toast(`请填写：${f.label}`);
        for (const g of qsa('.group')) {
          if (qs(`[data-field="${f.key}"]`, g)) {
            g.classList.remove('collapsed');
            delete state.collapsed[g.dataset.groupKey];
            break;
          }
        }
        const inp = qs(`[data-field="${f.key}"]`);
        if (inp && inp.focus) inp.focus();
        return;
      }
    }
  }
  state.busy = true;
  const tid = state.currentTable;
  const pid = state.project.id;
  const data = Object.assign({}, state.formData);
  const inspector = data.inspector || data.surveyor || state.user || '';
  const scId = state.subcompartment.id;
  const isUpdate = !!state.editRecordId;

  try {
    // 一对一模型：统一走 PUT survey/<tid>/rows upsert
    const savedRec = await fetchJSON(`api/projects/${pid}/survey/${tid}/rows`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subcompartment_id: scId, data, inspector }),
    });
    if (!state.records[tid]) state.records[tid] = [];
    const idx = state.records[tid].findIndex(r => r.id === savedRec.id);
    if (idx >= 0) state.records[tid][idx] = Object.assign({}, state.records[tid][idx], savedRec);
    else state.records[tid].push(savedRec);
    // 一对一：保存后保持编辑状态（不清空），后续编辑即更新同一记录
    state.editRecordId = savedRec.id;
  } catch (e) {
    state.busy = false;
    toast('保存失败：' + e.message, 2500);
    return;
  }

  state.busy = false;
  renderProgress();
  renderContent();
  toast(isUpdate ? '已更新记录' : '已保存记录');
}

// ── 导出 ──
function exportData() {
  if (!state.project) { toast('请先选择项目'); return; }
  const url = `api/projects/${state.project.id}/export`;
  window.open(url, '_blank');
}

// ── GPS ──
function getGPS() {
  if (!navigator.geolocation) { toast('设备不支持定位'); return; }
  toast('正在获取定位…', 1500);
  navigator.geolocation.getCurrentPosition(pos => {
    const lng = pos.coords.longitude.toFixed(6);
    const lat = pos.coords.latitude.toFixed(6);
    state.formData.longitude = lng;
    state.formData.latitude = lat;
    const lngEl = qs('[data-gps-val="longitude"]');
    const latEl = qs('[data-gps-val="latitude"]');
    if (lngEl) lngEl.textContent = lng;
    if (latEl) latEl.textContent = lat;
    toast('定位成功');
  }, err => {
    handleGeoError(err);
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
}

// 样方 GPS：同时填入经度/纬度到指定样方条目
function getSampleGPS(sampleKey, idx) {
  if (!navigator.geolocation) { toast('设备不支持定位'); return; }
  toast('正在获取定位…', 1500);
  navigator.geolocation.getCurrentPosition(pos => {
    const lng = pos.coords.longitude.toFixed(6);
    const lat = pos.coords.latitude.toFixed(6);
    const arr = state.formData[sampleKey];
    if (!Array.isArray(arr) || !arr[idx]) { toast('定位成功但样方已失效，请重试'); return; }
    arr[idx].longitude = lng;
    arr[idx].latitude = lat;
    // 更新显示值
    const lngEl = qs(`[data-gps-val="longitude"][data-sample-key="${sampleKey}"][data-sample-idx="${idx}"]`);
    const latEl = qs(`[data-gps-val="latitude"][data-sample-key="${sampleKey}"][data-sample-idx="${idx}"]`);
    if (lngEl) lngEl.textContent = lng;
    if (latEl) latEl.textContent = lat;
    toast('定位成功');
  }, err => {
    handleGeoError(err);
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
}

function closeModal() { const root = qs('#modalRoot'); if (root) root.innerHTML = ''; }

function openHelpModal() {
  const root = qs('#modalRoot');
  root.innerHTML = `
    <div class="modal-mask" data-action="close-modal-mask">
      <div class="modal modal-help" id="helpModal">
        <div class="help-header">
          <h3>使用说明</h3>
          <button class="btn-icon" data-action="close-modal">✕</button>
        </div>
        <div class="help-body">
          <div class="help-section">
            <h4>一、快速上手</h4>
            <ol>
              <li><b>选择项目</b>：在项目列表点击项目卡片进入</li>
              <li><b>查找小班</b>：在小班列表搜索框输入乡镇/村/林班/小班号，或切换地图视图点击小班面</li>
              <li><b>选择表格</b>：点击顶部标签切换表1~表5</li>
              <li><b>录入数据</b>：每个小班每张表仅一条记录，按分组填写字段后点击"保存"</li>
              <li><b>网格直填</b>：也可在小班列表点击"📊 网格调查"，在 Excel 式表格中直接编辑单元格</li>
              <li><b>样方调查</b>：表5（草原）含样方子表，在详情页点击"添加样方"录入多条样方数据</li>
              <li><b>打卡/轨迹/照片</b>：在小班列表点击对应按钮</li>
              <li><b>导出数据</b>：在调查页点击右上角"导出"，下载 Excel 汇总文件</li>
            </ol>
          </div>
          <div class="help-section">
            <h4>二、输入栏操作</h4>
            <ul>
              <li><b>分组折叠</b>：点击分组标题可展开/收起，"全部收起"一键折叠</li>
              <li><b>单选按钮</b>：合格/不合格等枚举字段，点击大按钮选择</li>
              <li><b>有/无开关</b>：管理情况等字段，点击切换"有"或"无"</li>
              <li><b>GPS定位</b>：经纬度字段点击"获取GPS"自动定位（需授权定位权限）</li>
              <li><b>拍照</b>：图片字段点击按钮调用手机相机</li>
              <li><b>样方增删</b>：表5样方面板点击"+ 添加样方"新增，点 ✕ 删除单个样方</li>
            </ul>
          </div>
          <div class="help-section">
            <h4>三、地图使用</h4>
            <ul>
              <li><b>切换地图</b>：在小班列表页点击"地图"按钮切换到地图视图</li>
              <li><b>点击小班</b>：点击地图上的小班面，弹窗中点击"调查此小班"进入录入</li>
              <li><b>地图匹配</b>：系统会根据乡镇/林班/小班号自动匹配，若未匹配到请在列表中手动搜索</li>
            </ul>
          </div>
          <div class="help-section">
            <h4>四、常见问题</h4>
            <ul>
              <li><b>GPS无法获取？</b> 确认手机定位已开启，浏览器已授权定位权限</li>
              <li><b>数据保存？</b> 点击"保存"上传到服务器，网格页失焦自动保存</li>
              <li><b>导出失败？</b> 确保有网络连接，导出需访问服务器</li>
              <li><b>必填项标 *</b> 红色星号标记的字段为必填，不填无法保存</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  `;
}

// ── 小班扩展面板（打卡/轨迹/照片）──
async function openScPanelForRow(scId) {
  if (!scId) return;
  try {
    const j = await fetchJSON(`api/subcompartments/rows/${scId}`);
    state.subcompartment = j.row;
    state.subcompartmentData = { prefilled: j.prefilled || {}, row: j.row };
    state.scExtras = j.extras || null;
    _renderScPanel();
  } catch (e) {
    toast('加载小班信息失败：' + e.message, 2500);
  }
}

// 从小班列表页快速打卡（不打开面板）
async function quickCheckin(scId) {
  if (!scId) return;
  if (!navigator.geolocation) { toast('设备不支持定位'); return; }
  toast('正在获取定位…', 1500);
  navigator.geolocation.getCurrentPosition(async pos => {
    const lng = pos.coords.longitude.toFixed(6);
    const lat = pos.coords.latitude.toFixed(6);
    try {
      await fetchJSON(`api/subcompartments/rows/${scId}/checkin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lng, lat }),
      });
      toast('✓ 打卡成功');
      const row = qs(`.sc-list-row[data-scid="${scId}"]`);
      if (row) {
        let badge = qs('.sc-checkin-badge', row);
        if (!badge) {
          const main = qs('.sc-row-main', row);
          if (main) {
            const span = document.createElement('span');
            span.className = 'sc-checkin-badge';
            span.textContent = '✓ 已打卡';
            main.appendChild(span);
          }
        } else {
          badge.textContent = '✓ 已打卡';
        }
      }
    } catch (e) {
      toast('打卡失败：' + e.message, 2500);
    }
  }, err => {
    handleGeoError(err);
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
}

function _renderScPanel() {
  const root = qs('#modalRoot');
  if (!state.subcompartment) { closeModal(); return; }
  const sc = state.subcompartment;
  const extras = state.scExtras || {};
  const label = sc.subcompartment_label || '';
  const checkinAt = extras.checkin_at || '';
  const checkinLng = extras.checkin_lng || '';
  const checkinLat = extras.checkin_lat || '';
  const checkinHtml = checkinAt
    ? `<div class="sc-extras-status ok">✓ 已打卡：${escapeHtml(checkinAt.replace('T', ' '))}<br>坐标：${escapeHtml(checkinLng)}, ${escapeHtml(checkinLat)}</div>`
    : `<div class="sc-extras-status empty">未打卡</div>`;
  const track = extras.track || [];
  const tracking = !!state._scTracking;
  const trackBtnLabel = tracking ? '停止记录' : (track.length ? '继续记录' : '开始记录轨迹');
  const trackHtml = track.length
    ? `<div class="sc-extras-status ok">✓ 轨迹 ${track.length} 个点</div>`
    : `<div class="sc-extras-status empty">无轨迹</div>`;
  const photos = extras.photos || [];
  const photosHtml = photos.length
    ? photos.map((p, i) => `<div class="sc-photo-item">
        <span class="sc-photo-name">📷 ${escapeHtml(p.name || '照片' + (i + 1))}</span>
        ${p.lng ? `<span class="sc-photo-gps">${escapeHtml(p.lng)}, ${escapeHtml(p.lat)}</span>` : ''}
        <button class="btn-sc-photo-del" data-action="sc-photo-remove" data-idx="${i}">✕</button>
      </div>`).join('')
    : `<div class="sc-extras-status empty">无照片</div>`;

  root.innerHTML = `
    <div class="modal-mask" data-action="close-modal-mask">
      <div class="modal sc-panel-modal" id="scPanelModal">
        <div class="sc-panel-header">
          <h3>小班扩展数据</h3>
          <button class="admin-close" data-action="close-modal">✕</button>
        </div>
        <div class="sc-panel-body">
          <div class="sc-panel-info">
            <span class="sc-panel-label">当前小班：</span>
            <b>${escapeHtml(label)}</b>
          </div>
          <div class="sc-extras-section">
            <h4>📍 打卡</h4>
            ${checkinHtml}
            <button class="btn-sc-action" data-action="sc-checkin">立即打卡</button>
          </div>
          <div class="sc-extras-section">
            <h4>🛤 轨迹</h4>
            ${trackHtml}
            <div class="sc-extras-actions">
              <button class="btn-sc-action ${tracking ? 'danger' : ''}" data-action="sc-track-toggle">${trackBtnLabel}</button>
              ${track.length ? '<button class="btn-sc-action" data-action="sc-track-view">查看轨迹图</button>' : ''}
              ${track.length ? '<button class="btn-sc-action warn" data-action="sc-track-clear">清空</button>' : ''}
            </div>
            <label class="btn-sc-action ghost">上传 GPX 文件
              <input type="file" id="scTrackFile" accept=".gpx,.json" hidden>
            </label>
          </div>
          <div class="sc-extras-section">
            <h4>📷 现场照片</h4>
            ${photosHtml}
            <label class="btn-sc-action">拍摄/选择照片
              <input type="file" id="scPhotoFile" accept="image/*" capture="environment" hidden>
            </label>
          </div>
        </div>
      </div>
    </div>
  `;
}

async function scCheckin() {
  if (!state.subcompartment) return;
  const scId = state.subcompartment.id;
  if (!navigator.geolocation) { toast('设备不支持定位'); return; }
  toast('正在获取定位…', 1500);
  navigator.geolocation.getCurrentPosition(async pos => {
    const lng = pos.coords.longitude.toFixed(6);
    const lat = pos.coords.latitude.toFixed(6);
    try {
      const r = await fetchJSON(`api/subcompartments/rows/${scId}/checkin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lng, lat }),
      });
      if (state.scExtras) {
        state.scExtras.checkin_at = r.checkin_at;
        state.scExtras.checkin_lng = r.checkin_lng;
        state.scExtras.checkin_lat = r.checkin_lat;
      }
      _renderScPanel();
      toast('打卡成功');
    } catch (e) {
      toast('打卡失败：' + e.message, 2500);
    }
  }, err => {
    handleGeoError(err);
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
}

let _scWatchId = null;
let _trackMap = null;     // Leaflet 地图实例
let _trackLayer = null;   // 轨迹折线图层
let _trackMarker = null;  // 当前位置标记

function openTrackMap() {
  if (!state.scExtras || !state.scExtras.track || !state.scExtras.track.length) {
    toast('无轨迹点');
    return;
  }
  let modal = qs('#trackMapModal');
  if (modal) modal.remove();
  modal = document.createElement('div');
  modal.id = 'trackMapModal';
  modal.className = 'track-map-modal';
  const tracking = !!state._scTracking;
  const track = state.scExtras.track || [];
  modal.innerHTML = `
    <div class="track-map-box">
      <div class="track-map-header">
        <span class="track-map-title">轨迹图（${track.length} 点）</span>
        <button class="track-map-close" data-action="track-map-close">×</button>
      </div>
      <div id="trackMapContainer" class="track-map-container"></div>
      <div class="track-map-actions">
        <span class="track-map-status">${tracking ? '正在记录…' : '已停止'}</span>
        <button class="btn-grid-action ${tracking ? 'danger' : ''}" data-action="sc-track-toggle">${tracking ? '停止记录' : '开始记录'}</button>
        <button class="btn-grid-action" data-action="track-map-close">关闭</button>
      </div>
    </div>
  `;
  app.appendChild(modal);
  modal.style.display = 'flex';

  // 初始化 Leaflet 地图
  const container = qs('#trackMapContainer');
  _trackMap = L.map(container, { zoomControl: true, attributionControl: false }).setView([0, 0], 13);
  // 高德瓦片（国内访问快，无偏移问题用 WGS84 经纬度，会有轻微偏移但可接受）
  L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', {
    subdomains: ['1', '2', '3', '4'],
    maxZoom: 18,
  }).addTo(_trackMap);

  _drawTrackLine();

  // 定时刷新轨迹（记录中实时更新）
  modal._refreshTimer = setInterval(() => {
    if (state._scTracking && state.scExtras && state.scExtras.track) {
      _drawTrackLine();
      const status = modal.querySelector('.track-map-status');
      if (status) status.textContent = `正在记录…（${state.scExtras.track.length} 点）`;
      const title = modal.querySelector('.track-map-title');
      if (title) title.textContent = `轨迹图（${state.scExtras.track.length} 点）`;
    }
  }, 3000);
}

function _drawTrackLine() {
  if (!_trackMap || !state.scExtras || !state.scExtras.track) return;
  const track = state.scExtras.track;
  const latlngs = track.filter(p => p.lat && p.lng).map(p => [parseFloat(p.lat), parseFloat(p.lng)]);
  if (!latlngs.length) return;

  if (_trackLayer) {
    _trackMap.removeLayer(_trackLayer);
  }
  _trackLayer = L.polyline(latlngs, { color: '#2e7d32', weight: 4, opacity: 0.8 }).addTo(_trackMap);

  // 起点绿点，终点红点
  if (_trackMarker) _trackMap.removeLayer(_trackMarker);
  L.circleMarker(latlngs[0], { radius: 6, color: '#2e7d32', fillColor: '#2e7d32', fillOpacity: 1 }).addTo(_trackMap);
  if (latlngs.length > 1) {
    L.circleMarker(latlngs[latlngs.length - 1], { radius: 6, color: '#c62828', fillColor: '#c62828', fillOpacity: 1 }).addTo(_trackMap);
  }

  // 自动适配视野
  _trackMap.fitBounds(_trackLayer.getBounds(), { padding: [30, 30] });
}

function closeTrackMap() {
  const modal = qs('#trackMapModal');
  if (!modal) return;
  if (modal._refreshTimer) clearInterval(modal._refreshTimer);
  if (_trackMap) {
    _trackMap.remove();
    _trackMap = null;
    _trackLayer = null;
    _trackMarker = null;
  }
  modal.remove();
  // 关闭弹窗不影响后台轨迹记录（_scWatchId 保持）
}

async function scTrackToggle() {
  if (!state.subcompartment) return;
  const scId = state.subcompartment.id;
  if (_scWatchId !== null) {
    navigator.geolocation.clearWatch(_scWatchId);
    _scWatchId = null;
    state._scTracking = false;
    await _scSaveTrack();
    _renderScPanel();
    _updateTrackMapState();
    return;
  }
  if (!navigator.geolocation) { toast('设备不支持定位'); return; }
  if (!state.scExtras) state.scExtras = { track: [], photos: [] };
  if (!state.scExtras.track) state.scExtras.track = [];
  state._scTracking = true;
  toast('开始记录轨迹，请保持页面打开…', 2500);
  _scWatchId = navigator.geolocation.watchPosition(pos => {
    const pt = {
      lng: pos.coords.longitude.toFixed(6),
      lat: pos.coords.latitude.toFixed(6),
      t: new Date().toISOString(),
    };
    state.scExtras.track.push(pt);
  }, err => {
    handleGeoError(err);
    navigator.geolocation.clearWatch(_scWatchId);
    _scWatchId = null;
    state._scTracking = false;
    _renderScPanel();
    _updateTrackMapState();
  }, { enableHighAccuracy: true, maximumAge: 1000, timeout: 30000 });
  _renderScPanel();
  _updateTrackMapState();
}

function _updateTrackMapState() {
  // 同步更新轨迹地图弹窗的按钮和状态（如果弹窗开着）
  const modal = qs('#trackMapModal');
  if (!modal) return;
  const tracking = !!state._scTracking;
  const track = (state.scExtras && state.scExtras.track) || [];
  const btn = modal.querySelector('[data-action="sc-track-toggle"]');
  if (btn) {
    btn.textContent = tracking ? '停止记录' : '开始记录';
    btn.classList.toggle('danger', tracking);
  }
  const status = modal.querySelector('.track-map-status');
  if (status) status.textContent = tracking ? `正在记录…（${track.length} 点）` : '已停止';
}

// 从轨迹面板点"查看轨迹图"
function scTrackView() {
  openTrackMap();
}

async function _scSaveTrack() {
  if (!state.subcompartment || !state.scExtras) return;
  const scId = state.subcompartment.id;
  const points = state.scExtras.track || [];
  try {
    await fetchJSON(`api/subcompartments/rows/${scId}/track`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ points }),
    });
    toast(`轨迹已保存（${points.length} 点）`);
  } catch (e) {
    toast('轨迹保存失败：' + e.message, 2500);
  }
}

function scTrackClear() {
  if (!state.scExtras) return;
  if (!confirm('确认清空轨迹？')) return;
  state.scExtras.track = [];
  _scSaveTrack();
  _renderScPanel();
}

async function scTrackFileUpload(input) {
  if (!state.subcompartment || !input.files || !input.files[0]) return;
  const file = input.files[0];
  const text = await file.text();
  let points = [];
  if (file.name.toLowerCase().endsWith('.gpx')) {
    const re = /<trkpt\s+[^>]*lat=["']([^"']+)["'][^>]*lon=["']([^"']+)["']/g;
    let m;
    while ((m = re.exec(text)) !== null) {
      points.push({ lat: m[1], lng: m[2], t: '' });
    }
  } else {
    try {
      const arr = JSON.parse(text);
      if (Array.isArray(arr)) {
        points = arr.map(p => ({ lng: String(p.lng || p.lon || ''), lat: String(p.lat || ''), t: p.t || '' }));
      }
    } catch (e) { toast('JSON 解析失败'); return; }
  }
  if (!points.length) { toast('未提取到轨迹点'); return; }
  if (!state.scExtras) state.scExtras = { track: [], photos: [] };
  state.scExtras.track = points;
  await _scSaveTrack();
  _renderScPanel();
}

// 照片名前缀：分类_乡镇_村_小班_拍照时间
function buildPhotoName(sc, origName) {
  const clean = (v) => (v == null ? '' : String(v).trim()).replace(/[\\/:*?"<>|\s]+/g, '_');
  const parts = [
    clean(sc.category),
    clean(sc.township),
    clean(sc.village),
    clean(sc.subcompartment_label || sc.subcompartment),
  ];
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  parts.push(stamp);
  const name = parts.filter(Boolean).join('_');
  const dot = origName ? origName.lastIndexOf('.') : -1;
  const ext = (dot >= 0) ? origName.slice(dot) : '';
  return name + ext;
}

// 将拍照文件以「分类_乡镇_村_小班_时间」前缀名下载保存到安卓相册
function savePhotoToAlbum(file, name) {
  try {
    const url = URL.createObjectURL(file);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 3000);
  } catch (e) { /* 下载失败不影响元数据保存 */ }
}

async function scPhotoFileChange(input) {
  if (!state.subcompartment || !input.files || !input.files[0]) return;
  const file = input.files[0];
  let lng = '', lat = '';
  if (navigator.geolocation) {
    try {
      const pos = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 });
      });
      lng = pos.coords.longitude.toFixed(6);
      lat = pos.coords.latitude.toFixed(6);
    } catch (e) { /* 定位失败仍可保存照片 */ }
  }
  if (!state.scExtras) state.scExtras = { track: [], photos: [] };
  if (!state.scExtras.photos) state.scExtras.photos = [];
  const name = buildPhotoName(state.subcompartment, file.name);
  savePhotoToAlbum(file, name);
  state.scExtras.photos.push({
    name,
    lng, lat,
    t: new Date().toISOString(),
    url: '',
  });
  await _scSavePhotos();
  _renderScPanel();
}

// 网格调查页「照片」按钮：直接调用安卓相机拍照（不弹面板），拍完按前缀命名保存
async function gridScPhotoChange(input) {
  if (!state.gridSubcompartment || !input.files || !input.files[0]) { input.remove(); return; }
  const sc = state.gridSubcompartment;
  if (!state.subcompartment) state.subcompartment = sc;
  const file = input.files[0];
  let lng = '', lat = '';
  if (navigator.geolocation) {
    try {
      const pos = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 });
      });
      lng = pos.coords.longitude.toFixed(6);
      lat = pos.coords.latitude.toFixed(6);
    } catch (e) { /* 定位失败仍可保存照片 */ }
  }
  if (!state.scExtras) state.scExtras = { track: [], photos: [] };
  if (!state.scExtras.photos) state.scExtras.photos = [];
  const name = buildPhotoName(sc, file.name);
  savePhotoToAlbum(file, name);
  state.scExtras.photos.push({ name, lng, lat, t: new Date().toISOString(), url: '' });
  await _scSavePhotos();
  toast('照片已保存：' + name);
  input.remove();
}

function scPhotoRemove(idx) {
  if (!state.scExtras || !state.scExtras.photos) return;
  if (!confirm('确认删除该照片记录？')) return;
  state.scExtras.photos.splice(idx, 1);
  _scSavePhotos();
  _renderScPanel();
}

async function _scSavePhotos() {
  if (!state.subcompartment || !state.scExtras) return;
  const scId = state.subcompartment.id;
  const photos = state.scExtras.photos || [];
  try {
    await fetchJSON(`api/subcompartments/rows/${scId}/photos`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ photos }),
    });
    toast('照片记录已保存');
  } catch (e) {
    toast('照片保存失败：' + e.message, 2500);
  }
}

// ── 事件委托 ──
app.addEventListener('click', async (e) => {
  const t = e.target.closest('[data-action]');
  if (!t) return;
  const action = t.dataset.action;
  switch (action) {
    case 'switch-table':
      await switchTable(t.dataset.table);
      break;
    case 'toggle-group': {
      const key = t.dataset.group;
      const g = t.closest('.group');
      if (g) {
        g.classList.toggle('collapsed');
        state.collapsed[key] = g.classList.contains('collapsed');
      }
      break;
    }
    case 'collapse-all': {
      const groups = qsa('.group');
      const allCollapsed = groups.every(g => g.classList.contains('collapsed'));
      groups.forEach(g => {
        const key = g.dataset.groupKey;
        if (allCollapsed) { g.classList.remove('collapsed'); delete state.collapsed[key]; }
        else { g.classList.add('collapsed'); state.collapsed[key] = true; }
      });
      break;
    }
    case 'enum-select': {
      const field = t.dataset.field;
      const val = t.dataset.value;
      state.formData[field] = val;
      qsa('.enum-btn', t.parentElement).forEach(b => b.classList.toggle('active', b.dataset.value === val));
      break;
    }
    case 'toggle-checkbox': {
      const field = t.dataset.field;
      const on = !state.formData[field];
      state.formData[field] = on;
      t.classList.toggle('on', on);
      const lbl = t.parentElement.querySelector('.toggle-label');
      if (lbl) { lbl.textContent = on ? '有' : '无'; lbl.className = 'toggle-label ' + (on ? 'yes' : 'no'); }
      break;
    }
    case 'get-gps':
      getGPS();
      break;
    case 'get-gps-sample':
      await getSampleGPS(t.dataset.sampleKey, Number(t.dataset.sampleIdx));
      break;
    case 'sample-add': {
      const sk = t.dataset.sampleKey;
      const def = activeDef();
      const fdef = def && (def.input_columns || []).find(x => x.key === sk);
      if (!fdef) break;
      if (!Array.isArray(state.formData[sk])) state.formData[sk] = [];
      state.formData[sk].push(newSampleObject(fdef.sample_fields || []));
      renderContent();
      break;
    }
    case 'sample-del': {
      const sk = t.dataset.sampleKey;
      const idx = Number(t.dataset.sampleIdx);
      if (Array.isArray(state.formData[sk])) {
        state.formData[sk].splice(idx, 1);
        renderContent();
      }
      break;
    }
    case 'save-survey':
      await saveSurvey();
      break;
    case 'export':
      exportData();
      break;
    case 'open-help':
      openHelpModal();
      break;
    case 'close-modal':
    case 'close-modal-mask':
      if (action === 'close-modal-mask' && e.target !== t) break;
      closeModal();
      break;
    case 'perm-open-settings':
      permOpenSettings();
      break;
    // ── 三级导航 ──
    case 'go-projects':
      goToProjects();
      break;
    case 'go-sc-list':
      await goToScList();
      break;
    case 'enter-project':
      await enterProject(t.dataset.pid);
      break;
    case 'sc-toggle-view':
      if (state._map && t.dataset.mode !== 'map') { state._map.remove(); state._map = null; }
      state.scViewMode = t.dataset.mode;
      renderApp();
      break;
    case 'go-survey-grid':
      await goToSurveyGrid();
      break;
    // grid-filter-township 由 change 事件处理（避免下拉展开时误触重渲染）
    case 'sc-enter-survey':
      await goToSurvey(t.dataset.scid);
      break;
    case 'sc-edit':
      await openEditSubcompartmentModal(t.dataset.scid);
      break;
    case 'save-sc-edit':
      await saveSubcompartmentEdit(t.dataset.scId);
      break;
    case 'sc-row-photos':
      await openScPanelForRow(t.dataset.scid);
      break;
    case 'sc-row-track':
      await openScPanelForRow(t.dataset.scid);
      break;
    case 'sc-row-checkin':
      await quickCheckin(t.dataset.scid);
      break;
    // 网格调查页：选定小班的照片 → 直接调用安卓相机拍照（capture）
    case 'sc-photo': {
      if (!state.gridSubcompartment) { toast('请先选择小班'); break; }
      // App 内先校验相机权限，无权限则提示
      if (isApp()) {
        const ps = await permState('camera');
        if (!ps.granted) {
          const req = await permRequest('camera');
          if (!req.granted) {
            showPermDialog('camera', '未获得相机权限，无法拍照。请在系统设置中允许「相机」，然后重试。');
            break;
          }
        }
      }
      if (!state.subcompartment) state.subcompartment = state.gridSubcompartment;
      const inp = document.createElement('input');
      inp.type = 'file';
      inp.accept = 'image/*';
      inp.setAttribute('capture', 'environment');
      inp.style.display = 'none';
      inp.addEventListener('change', async () => { await gridScPhotoChange(inp); }, { once: true });
      document.body.appendChild(inp);
      inp.click();
      break;
    }
    case 'sc-track':
      if (state.gridSubcompartment) await openScPanelForRow(state.gridSubcompartment.id);
      break;
    // 签字
    case 'sign-open': {
      const key = t.getAttribute('data-key');
      if (key) openSignModal(key);
      break;
    }
    case 'sign-close':
      closeSignModal();
      break;
    case 'sign-clear':
      clearSignCanvas();
      break;
    case 'sign-save':
      await saveSign();
      break;
    // 网格调查页：样方模式切换
    case 'sc-checkin':
      // 网格视图：用选定小班快速打卡；其它视图（详情面板）：用 state.subcompartment 打卡
      if (state.view === 'survey_grid' && state.gridSubcompartment) {
        await quickCheckin(state.gridSubcompartment.id);
      } else {
        await scCheckin();
      }
      break;
    case 'sc-track-toggle':
      await scTrackToggle();
      break;
    case 'sc-track-view':
      scTrackView();
      break;
    case 'sc-track-clear':
      scTrackClear();
      break;
    case 'track-map-close':
      closeTrackMap();
      break;
    case 'sc-photo-remove':
      scPhotoRemove(Number(t.dataset.idx));
      break;
    case 'logout':
      window.location.href = '/forest/logout';
      break;
  }
});

// input 事件
app.addEventListener('input', (e) => {
  const t = e.target;
  if (t.id === 'scSearch') {
    onScSearchInput(t.value);
    return;
  }
  // 样方字段输入：data-sample-field 定位
  if (t.dataset && t.dataset.sampleField) {
    const sk = t.dataset.sampleKey;
    const idx = Number(t.dataset.sampleIdx);
    const fk = t.dataset.sampleField;
    const arr = state.formData[sk];
    if (Array.isArray(arr) && arr[idx]) {
      arr[idx][fk] = t.value;
    }
    return;
  }
  if (t.dataset && t.dataset.field) {
    let v = t.value;
    state.formData[t.dataset.field] = v;
  }
});

// keydown — 小班搜索框回车立即搜索
app.addEventListener('keydown', (e) => {
  if (e.target.id === 'scSearch' && e.key === 'Enter') {
    e.preventDefault();
    if (_scSearchTimer) clearTimeout(_scSearchTimer);
    onScSearchInput(e.target.value);
  }
});

// 网格内切换项目：重置筛选与缓存，重新载入该项目的网格
async function switchGridProject(pid) {
  const p = (state.projects || []).find(x => x.id === pid);
  if (!p) return;
  state.project = p;
  state.gridCategory = '';
  state.gridTable = 'table1';
  state.gridTownship = '';
  state.gridVillage = '';
  state.gridSubcompartment = null;
  state.scAllRows = [];
  state.scFilteredRows = [];
  state.gridScRows = [];
  state.subcompartmentPrefilledMap = {};
  state._gridSurveyMap = {};
  await goToSurveyGrid();
}

// change 事件
app.addEventListener('change', async (e) => {
  const t = e.target;
  if (t.classList && t.classList.contains('f-photo')) {
    const field = t.dataset.field;
    const file = t.files && t.files[0];
    if (file) {
      state.formData[field] = file.name;
      const nameEl = qs(`[data-photo-name="${field}"]`);
      if (nameEl) nameEl.textContent = file.name;
    }
    return;
  }
  if (t.id === 'scPhotoFile') {
    scPhotoFileChange(t);
    return;
  }
  if (t.id === 'scTrackFile') {
    scTrackFileUpload(t);
    return;
  }
  if (t.id === 'gridProjectFilter') {
    switchGridProject(t.value);
    return;
  }
  if (t.id === 'gridCategoryFilter') {
    state.gridCategory = t.value;
    state.gridTable = CATEGORY_TO_TABLE[t.value] || 'table1';
    state.gridTownship = '';
    state.gridVillage = '';
    state.gridSubcompartment = null;
    await loadGridScRows();
    refreshGridToolbar();
    await renderSurveyForm();
    return;
  }
  if (t.id === 'gridSubcompartmentFilter') {
    const scId = t.value;
    state.gridSubcompartment = (state.gridScRows || []).find(r => r.id === scId) || null;
    state.subcompartment = state.gridSubcompartment;  // 复用照片/打卡等扩展数据流程
    await loadGridSubcompartmentData();
    // 预载扩展数据（照片/轨迹/打卡），避免拍照保存时覆盖历史记录
    if (state.gridSubcompartment) {
      try {
        const j = await fetchJSON(`api/subcompartments/rows/${state.gridSubcompartment.id}`);
        state.scExtras = j.extras || { track: [], photos: [] };
      } catch (e) { state.scExtras = { track: [], photos: [] }; }
    } else {
      state.scExtras = null;
    }
    refreshGridToolbar();
    await renderSurveyForm();
    return;
  }
  if (t.id === 'gridTownshipFilter') {
    state.gridTownship = t.value;
    state.gridVillage = '';        // 级联：清空下级筛选
    state.gridSubcompartment = null;
    refreshGridToolbar();
    await renderSurveyForm();
    return;
  }
  if (t.id === 'gridVillageFilter') {
    state.gridVillage = t.value;
    state.gridSubcompartment = null;    // 级联：清空下级筛选
    refreshGridToolbar();
    await renderSurveyForm();
    return;
  }
});

// 键盘回车提交（在编辑框单行输入框内回车 → 保存）
app.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.classList && e.target.classList.contains('f-input') &&
      e.target.tagName !== 'TEXTAREA' && state.view === 'survey' && state.subcompartment) {
    e.preventDefault();
    saveSurvey();
  }
});

// 启动
init();
