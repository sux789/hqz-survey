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
  gridCheckin: '',        // 网格打卡状态筛选（''=全部 done=已打卡 undone=未打卡）
  _grid: null,            // jspreadsheet 实例
  _gridSurveyMap: {},     // {subcompartment_id: data} 已有调查数据
  _gridVerMap: {},        // {subcompartment_id: version} 读取时版本号（乐观锁）
  _gridBaseMap: {},       // {subcompartment_id: data} 读取时基线快照（算"我改了哪些字段"）
  subcompartmentPrefilledMap: {}, // {sc_id: prefilled_dict} 网格黄色列取值
  // 级联筛选：项目 → 分类 → 县/乡/村/林班 → 小班
  gridCategory: '',                // 当前分类（''=全部分类）
  gridSubcompartment: null,        // 当前选中的小班对象
  gridCategories: [],              // 项目可选分类列表
  gridScRows: null,                // 按分类过滤后的小班集合（null=用 scAllRows）
  _gridRowFields: [],              // 当前两列表格每行的字段信息（用于行号→字段映射）
};
window.__hqzState = state;  // TODO(debug): 临时调试钩子，验证后删除

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
  // 清掉旧 toast，避免多条叠屏不消失
  document.querySelectorAll('.toast').forEach(el => el.remove());
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  // 定时移除 + 兜底二次清理（remove 幂等）
  setTimeout(() => t.remove(), ms);
  setTimeout(() => { if (t.parentNode) t.parentNode.removeChild(t); }, ms + 800);
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

// 统一 fetch 封装：401 自动跳转登录页；禁缓存防止换账号后读到旧用户数据
async function apiFetch(url, options) {
  const res = await fetch(url, Object.assign({ cache: 'no-store' }, options));
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
  // Android 物理返回键：样地页返回小班页，网格调查页返回项目列表（默认 goBack 无 SPA 历史会直接退出 App）
  try {
    if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.App) {
      window.Capacitor.Plugins.App.addListener('backbutton', async () => {
        if (state.view === 'samples') { await smLeaveToGrid(); return; }
        if (state.view === 'survey_grid') { goToProjects(); return; }
        // 其余视图走浏览器历史（无历史时退出 App）
        if (window.history.length > 1) window.history.back();
        else if (navigator.app && navigator.app.exitApp) navigator.app.exitApp();
      });
    }
  } catch (e) { /* 非原生环境忽略 */ }
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
  } else if (state.view === 'samples') {
    body.innerHTML = renderSamplesPage();
    bindSamplesPage();
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
    : (state.view === 'samples'
      ? '<button class="btn-back" data-action="go-grid" title="返回小班">‹</button>'
      : ((state.view === 'survey' || state.view === 'survey_grid')
        ? '<button class="btn-back" data-action="go-projects" title="返回项目列表">‹</button>'
        : ''));
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
  } else if (state.view === 'samples' && state.gridSubcompartment) {
    viewTitle = `<span class="topbar-title">🌱 样地调查 · ${escapeHtml(state.gridSubcompartment.subcompartment_label || '')}</span>`;
  }
  tb.innerHTML = `
    <div class="topbar-left">
      ${backBtn}
      ${viewTitle}
    </div>
    <div class="topbar-right">
      <button class="btn-icon help-btn" data-action="open-help" title="使用说明">?</button>
      ${(state.view === 'survey_grid' || state.view === 'samples') ? `<button class="btn-export" data-action="export-base" title="导出基本信息 Excel（当前项目${currentBaseExportCategory() ? '，仅「' + escapeHtml(currentBaseExportCategory()) + '」分类' : '，全部分类'}）">导出</button>` : ''}
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
  // 预载当前表全部调查数据（打卡状态筛选用：样地坐标x/y 非空 = 已打卡）
  await loadGridSubcompartmentData();
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

  // 小班下拉（级联到最后；已选小班被筛出时仍保留其选项）
  // value 用小班行主键 id：多林班/跨村会出现同号小班，号码不唯一，id 才能精确定位
  const scRows = getGridFilteredRows();
  const scOpts = scRows.map(r =>
    `<option value="${escapeHtml(r.id)}" ${state.gridSubcompartment && r.id === state.gridSubcompartment.id ? 'selected' : ''}>${escapeHtml(r.subcompartment_label || '')}</option>`
  ).join('');
  if (state.gridSubcompartment && !scRows.some(r => r.id === state.gridSubcompartment.id)) {
    scOpts += `<option value="${escapeHtml(state.gridSubcompartment.id)}" selected>${escapeHtml(state.gridSubcompartment.subcompartment_label || '')}</option>`;
  }
  // 调查小班号搜索：正整数输入，命中后自动选中对应小班（放在小班 select 前）
  const scSearchHtml = `<input id="gridScSearch" class="grid-sc-search" type="number" min="1" step="1" inputmode="numeric" placeholder="调查小班号" title="输入调查小班号（正整数），匹配后自动选中该小班">`;
  const scHtml = `<select id="gridSubcompartmentFilter" data-action="grid-filter-subcompartment" title="小班">
    <option value="">选择小班</option>
    ${scOpts}
  </select>`;

  // 打卡状态筛选（样地坐标x/y 均非空 = 已打卡）
  const ckHtml = `<select id="gridCheckinFilter" data-action="grid-filter-checkin" title="打卡状态">
    <option value="">打卡状态</option>
    <option value="done" ${state.gridCheckin === 'done' ? 'selected' : ''}>已打卡</option>
    <option value="undone" ${state.gridCheckin === 'undone' ? 'selected' : ''}>未打卡</option>
  </select>`;

  // 操作按钮（选定小班后显示）
  const scBtns = state.gridSubcompartment ? `
    <button class="btn-grid-action" data-action="sc-photo">照片</button>
    <button class="btn-grid-action" data-action="sc-track">轨迹</button>
    <button class="btn-grid-action" data-action="sc-checkin">打卡</button>
    <button class="btn-grid-action" data-action="sc-samples">样地</button>
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
    ${ckHtml}
    ${scSearchHtml}
    ${scHtml}
    ${scBtns}
    <span class="grid-hint">${hint}</span>
  </div>`;
}

// 按分类+县+乡+村+打卡状态过滤后的小班列表
function getGridFilteredRows() {
  let rows = state.gridScRows || state.scAllRows || [];
  if (state.gridTownship) rows = rows.filter(r => r.township === state.gridTownship);
  if (state.gridVillage) rows = rows.filter(r => r.village === state.gridVillage);
  if (state.gridCheckin) rows = rows.filter(r => isScCheckedIn(r) === (state.gridCheckin === 'done'));
  return rows;
}

// 打卡状态判定：样地坐标x/y 都不为空 = 已打卡
function isScCheckedIn(r) {
  const d = (state._gridSurveyMap || {})[r.id] || {};
  return d.sample_coord_x != null && d.sample_coord_x !== '' &&
         d.sample_coord_y != null && d.sample_coord_y !== '';
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

// 加载当前表全部调查数据（选定小班编辑 + 打卡状态筛选共用）
async function loadGridSubcompartmentData() {
  if (!state.project || !state.gridTable) { state._gridSurveyMap = {}; state._gridVerMap = {}; state._gridBaseMap = {}; return; }
  try {
    const url = `api/projects/${state.project.id}/survey/${state.gridTable}/rows`;
    const j = await fetchJSON(url);
    applyLoadedSurveyRows(j.rows || []);
  } catch (e) { state._gridSurveyMap = {}; state._gridVerMap = {}; state._gridBaseMap = {}; }
}

// 全量应用服务端返回的调查行：数据 + 乐观锁版本号 + 基线快照（深拷贝，
// 供冲突时 diff「我改了哪些字段」——样地页对 map 值是就地修改，浅拷贝不够）
function applyLoadedSurveyRows(rows) {
  const map = {}, vm = {}, bm = {};
  (rows || []).forEach(r => {
    const d = r.data || {};
    map[r.subcompartment_id] = d;
    vm[r.subcompartment_id] = (r.version != null) ? r.version : 1;
    bm[r.subcompartment_id] = JSON.parse(JSON.stringify(d));
  });
  state._gridSurveyMap = map;
  state._gridVerMap = vm;
  state._gridBaseMap = bm;
}

// 网格页保存成功后同步版本号 + 基线快照（仅当前 gridTable 的 map）
function markGridRowSaved(scId, rec, savedData) {
  if (!scId) return;
  if (rec && rec.version != null) state._gridVerMap[scId] = rec.version;
  state._gridBaseMap[scId] = JSON.parse(JSON.stringify(savedData || (rec && rec.data) || {}));
}

// 读取当前小班的乐观锁版本号（无记录 = 0，表示「首次创建须不存在」）
function gridRowVersion(scId) {
  return (scId && state._gridVerMap[scId] != null) ? state._gridVerMap[scId] : 0;
}

// 统一 PUT 调查行（乐观锁）：409 冲突返回 {ok:false, conflict}，其余异常抛出
async function putSurveyRow(tid, scId, data, inspector, baseVersion) {
  const body = { subcompartment_id: scId, data, inspector };
  if (baseVersion != null) body.base_version = baseVersion;
  const res = await apiFetch(`api/projects/${state.project.id}/survey/${tid}/rows`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.status === 409) {
    const j = await res.json().catch(() => ({}));
    return { ok: false, conflict: (j && j.conflict) || null };
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return { ok: true, rec: await res.json() };
}

// ── 保存冲突（乐观锁 409）：弹窗展示双方改动，提供 合并/覆盖/加载最新 ──

// 字段 key → 展示 label（冲突弹窗用）：表定义字段 + 样地页/汇总区固定项
function fieldLabelMap(tid) {
  const m = {};
  const def = getGridTableDef(tid) || getTableDef(tid);
  ((def && def.prefilled_columns) || []).concat((def && def.input_columns) || []).forEach(f => {
    if (f.key) m[f.key] = f.label || f.key;
  });
  Object.assign(m, {
    samples: '样地列表',
    sm_total_count: '总样地个数', sm_grid_area: '单个网格面积', sm_grid_count: '种植网格数量',
    sm_pole: '撑杆情况', sm_film: '覆膜情况', sm_inspector: '验收人',
    sm_inspect_date: '验收日期', sm_remark: '备注',
  });
  return m;
}

// 表内 computed 字段 key 集合（diff 展示时过滤——computed 随输入联动，保存前会重算）
function computedKeySet(tid) {
  const def = getGridTableDef(tid) || getTableDef(tid);
  const s = {};
  ((def && def.input_columns) || []).forEach(f => { if (f.type === 'computed') s[f.key] = 1; });
  return s;
}

// diff：base → cur 间值有变化（含增删）的顶层 key（过滤 inspector/computed 噪音）
function diffChangedKeys(base, cur, computedKeys) {
  const b = base || {}, c = cur || {};
  const out = [];
  new Set([...Object.keys(b), ...Object.keys(c)]).forEach(k => {
    if (k === 'inspector') return;
    if (computedKeys && computedKeys[k]) return;
    if (JSON.stringify(b[k]) !== JSON.stringify(c[k])) out.push(k);
  });
  return out;
}

// 合并：以对方最新数据为底，叠加我改过的字段（调用方保存前重算 computed）
function mergeSurveyData(theirData, myData, myChangedKeys) {
  const merged = JSON.parse(JSON.stringify(theirData || {}));
  (myChangedKeys || []).forEach(k => { merged[k] = (myData || {})[k]; });
  return merged;
}

// 保存前重算 computed 字段（就地修改并返回；冲突弹窗合并/覆盖的数据也要重算，
// 防止对方的旧统计值随合并数据混入）
function recomputeComputedFields(d, tid) {
  const def = getGridTableDef(tid) || getTableDef(tid);
  ((def && def.input_columns) || []).forEach(f => {
    if (f.type === 'computed') d[f.key] = computeFieldValue(f.formula, d);
  });
  return d;
}

// 冲突弹窗。ctx: {tid, scId, myData, myBase, conflict,
//   saveFn(data, baseVersion) -> putSurveyRow 结果,
//   onSaved(rec, savedData), onLoaded(data, version)}
// 按钮布局：主行 = 合并保存（推荐，仅双方无重叠改动时）或 用我的覆盖；
// 次行 = 加载最新（丢弃我的修改）/ 取消（点遮罩同取消，保留我的修改稍后处理）。
function showRowConflictDialog(ctx) {
  const { tid, myData, conflict, saveFn, onSaved, onLoaded } = ctx;
  const their = (conflict && conflict.data) || {};
  const ck = computedKeySet(tid);
  const labels = fieldLabelMap(tid);
  const myBase = ctx.myBase != null ? ctx.myBase : (state._gridBaseMap[ctx.scId] || {});
  const myKeys = diffChangedKeys(myBase, myData, ck);
  const theirKeys = diffChangedKeys(myBase, their, ck);
  const overlap = myKeys.filter(k => theirKeys.includes(k));
  const canMerge = overlap.length === 0;
  const fmt = ks => {
    if (!ks.length) return '（无）';
    const ls = ks.map(k => labels[k] || k);
    return ls.length > 6 ? ls.slice(0, 6).join('、') + ` 等 ${ls.length} 项` : ls.join('、');
  };
  const who = (conflict && conflict.inspector) || '其他用户';
  const when = (conflict && conflict.updated_at) || '';
  const root = qs('#modalRoot');
  root.innerHTML = `
    <div class="modal-mask">
      <div class="modal">
        <h3>数据已被他人修改</h3>
        <div class="sm-confirm-tip">该小班数据在你编辑期间被 <b>${escapeHtml(who)}</b>${when ? `（${escapeHtml(when)}）` : ''} 保存过新版本。</div>
        <div class="cf-diff">
          <div><span class="cf-side">对方修改了</span>${escapeHtml(fmt(theirKeys))}</div>
          <div><span class="cf-side">你修改了</span>${escapeHtml(fmt(myKeys))}</div>
          ${canMerge ? '' : `<div class="cf-overlap">双方都改了：${escapeHtml(fmt(overlap))}，无法自动合并，需选择以谁为准</div>`}
        </div>
        <div class="modal-actions">
          ${canMerge
            ? '<button class="btn-confirm" data-cf="merge">合并保存（推荐）</button>'
            : '<button class="btn-confirm" data-cf="overwrite">用我的覆盖</button>'}
        </div>
        <div class="modal-actions">
          <button class="btn-cancel" data-cf="cancel">取消</button>
          <button class="btn-cancel" data-cf="reload">加载最新（丢弃我的修改）</button>
        </div>
      </div>
    </div>`;
  const maskEl = root.querySelector('.modal-mask');
  const close = () => { root.innerHTML = ''; };
  // 保存动作（合并/覆盖）：CAS 用冲突返回的最新 version；再冲突则用新数据刷新弹窗
  const doSave = async (data, label) => {
    root.querySelectorAll('button').forEach(b => { b.disabled = true; });
    try {
      const r = await saveFn(data, (conflict && conflict.version) || 0);
      if (r && r.ok) {
        close();
        if (onSaved) onSaved(r.rec, data);
        toast(label + '成功', 1800);
      } else if (r && r.conflict) {
        showRowConflictDialog(Object.assign({}, ctx, { conflict: r.conflict }));
        toast('对方又保存了新版本，请重新选择', 2500);
      } else {
        throw new Error('保存失败');
      }
    } catch (e) {
      toast('保存失败：' + (e.message || e), 2500);
      root.querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  };
  root.querySelectorAll('[data-cf]').forEach(b => b.addEventListener('click', ev => {
    ev.stopPropagation();
    const act = b.dataset.cf;
    if (act === 'cancel') { close(); return; }
    if (act === 'reload') {
      close();
      if (onLoaded) onLoaded(their, (conflict && conflict.version) || 1);
      toast('已加载最新数据', 1800);
      return;
    }
    if (act === 'merge') {
      doSave(mergeSurveyData(their, myData, myKeys), '合并保存');
      return;
    }
    if (act === 'overwrite') doSave(myData, '覆盖保存');
  }));
  // 点遮罩 = 取消（保留我的修改，稍后处理）
  maskEl.addEventListener('click', ev => {
    if (ev.target === ev.currentTarget) close();
  });
}

// 当前小班的预填数据（GDB 黄色列）：网格页取行自带 prefilled，详情页回退 subcompartmentData
function currentPrefilled() {
  const sc = state.gridSubcompartment;
  if (sc) return sc.prefilled || state.subcompartmentPrefilledMap[sc.id] || {};
  if (state.subcompartmentData && state.subcompartmentData.prefilled) return state.subcompartmentData.prefilled;
  return {};
}

// 公式计算：根据 formula 名和数据计算 computed 字段值
// 样地统计（与导出端 _sample_stats / 样地页 smSummaryComputed 同口径）：
//   查数株数 = 调查总株数（样地模板 B34 同口径）
//            = round(Σ种植 ÷ 个数 ÷ 150 × 网格面积 × 网格数量)，
//            个数 = 手写 sm_total_count（>0 生效）回退实际样地数
//   合格率 = Σ成活÷Σ种植（比率 0-1 如 0.9524，保留4位，不 ×100——
//            与 Excel 模板 0.00% 百分比格式/分派公式 0.9/0.4 阈值同语义）；
//   合格株树 = round(查数株数×合格率)
function computeFieldValue(formula, data) {
  const samples = data && Array.isArray(data.samples) ? data.samples : [];
  let planted = 0, alive = 0, realN = 0;
  samples.forEach(s => {
    if (!s) return;
    realN++;
    planted += Number(s.planted) || 0;
    alive += Number(s.alive) || 0;
  });
  const handN = Number(data && data.sm_total_count);
  const n = (handN > 0) ? handN : realN;
  const gArea = Number(data && data.sm_grid_area) || 0;
  const gCount = Number(data && data.sm_grid_count) || 0;
  // 查数株数 = 调查总株数（B34 守卫：个数或Σ种植为 0 → 空；网格未填时结果为 0，同模板）
  const total = (n > 0 && planted > 0) ? Math.round(planted / n / 150 * gArea * gCount) : null;
  // 合格率（比率 0-1；无样地 → 空）
  const rate = planted ? (alive / planted) : null;
  const rate4 = rate != null ? Math.round(rate * 10000) / 10000 : null;
  switch (formula) {
    case 's_planted_total':
      return total || '';
    case 's_qualified_rate':
      return rate4 != null ? rate4 : '';
    case 's_qualified_count': {
      if (!total || !planted) return '';
      return Math.round(total * alive / planted) || '';
    }
    // ── 成活率等级三列（互斥分派，与模板 =IF(AP>=0.9,AP,"") 等公式同口径）──
    case 's_survival_pass':      // 合格：合格率 ≥ 0.9 → 显示合格率，否则空白
      return (rate != null && rate >= 0.9) ? rate4 : '';
    case 's_survival_replant':   // 待补植：0.4 < 合格率 < 0.9 → 显示合格率，否则空白
      return (rate != null && rate < 0.9 && rate > 0.4) ? rate4 : '';
    case 's_survival_fail':      // 失败：合格率 ≤ 0.4 → 显示合格率，否则空白
      return (rate != null && rate <= 0.4) ? rate4 : '';
    // ── 面积分派列：合格率 ≥0.9 → 上报面积原样填入；<0.9 → 留空（模板 =IF(AP>=0.9,N,"")）──
    // 上报面积是小班预填（GDB）字段，不在调查记录 data 里：优先 data 带入值，
    // 否则从当前小班预填取；GDB 字符串数值（"11000.0"）转数字显示
    case 's_qualified_area': {
      if (rate == null || rate < 0.9) return '';
      let area = data && data.reported_area;
      if (area == null || area === '') area = currentPrefilled().reported_area;
      if (area == null || area === '') return '';
      const n = Number(area);
      return isNaN(n) ? String(area) : n;
    }
    default: return '';
  }
}

// ── 率类字段（percent）显示/存储换算 ──
// 存储统一为比率 0-1（不 ×100，与 Excel 模板百分比格式/分派公式阈值同语义）；
// 输入框 UX 不变：用户仍输入 95（见 95%），保存时 ÷100，回显时 ×100。
function pctToDisplay(v) {
  if (v === '' || v == null) return '';
  const n = Number(v);
  if (isNaN(n)) return String(v);
  return String(Math.round(n * 10000) / 100);
}
function pctToStore(v) {
  if (v === '' || v == null) return '';
  const n = Number(v);
  if (isNaN(n)) return v;
  return Math.round(n * 100) / 10000;  // 95 → 0.95；95.24 → 0.9524（保留4位）
}
// computed 率类字段展示：0.9524 → "95.24%"（合格率/成活率等级三列）；计数/面积类原样输出
function fmtComputedDisplay(formula, cv) {
  if (cv === '' || cv == null) return '';
  if (formula === 's_qualified_rate' || formula === 's_survival_pass'
    || formula === 's_survival_replant' || formula === 's_survival_fail') {
    const n = Number(cv);
    return isNaN(n) ? String(cv) : (Math.round(n * 10000) / 100).toFixed(2) + '%';
  }
  return String(cv);
}

// 刷新详情页全部 computed 字段显示（样地数据变化后调用）
function refreshComputedDisplays() {
  (activeInputColumns() || []).forEach(f => {
    if (f.type !== 'computed') return;
    const cv = computeFieldValue(f.formula, state.formData);
    state.formData[f.key] = cv;
    const el = qs(`[data-computed-val="${f.key}"]`);
    if (el) el.textContent = cv === '' ? '自动计算' : fmtComputedDisplay(f.formula, cv);
  });
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
      data.push([f.label, fmtComputedDisplay(f.formula, cv)]);
      return;
    }
    // readOnly 输入字段（从密点文件读取等）
    rowFields.push({ kind: 'input', key: f.key, label: f.label, type: f.type, options: f.options || [], default: f.default, readOnly: !!f.readOnly });
    let v = sv[f.key];
    if (v == null) v = '';
    // 率类存比率（0.95），网格回显 ×100（95）与输入 UX 一致
    if (f.type === 'percent' && v !== '') v = pctToDisplay(v);
    // 只读字段空值时回填小班预填值（每亩面积/每亩设计株树 等）
    if (f.readOnly && v === '' && pf[f.key] != null) v = String(pf[f.key]);
    // enum：旧 checkbox 布尔数据归一 + 空值显示默认值（有/无、是/否）
    if (f.type === 'enum' && f.options && f.options.length) {
      if (v === true || v === 'true' || v === 1 || v === '1') v = f.options[0];
      else if (v === false || v === 'false' || v === 0 || v === '0') v = (f.default && f.options.includes(f.default)) ? f.default : f.options[f.options.length - 1];
      else if (v === '' && f.default && f.options.includes(f.default)) v = f.default;
    }
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
  // 表格高度：行高28px × 行数 + 表头32 + 边距，让所有行完整显示，外层容器滚动
  const rowH = 28;
  const headerH = 32;
  const tableH = data.length * rowH + headerH + 4;
  state._grid = jspreadsheet(qs('#gridEl'), {
    data: data,
    columns: cols,
    contextMenu: false,
    allowInsertRow: false,
    allowManualInsertRow: false,
    allowDeleteRow: false,
    tableOverflow: false,
    tableWidth: '100%',
    onchange: (instance, cell, x, y, value) => onGridCellChange(x, y, value),
  });
  // 黄色预填列/computed/只读字段：官方 setReadOnly API 锁定（阻断双击、键入、粘贴）
  rowFields.forEach((f, i) => {
    if (f.kind === 'prefilled' || f.kind === 'computed' || (f.kind === 'input' && f.readOnly)) {
      try { state._grid.setReadOnly(`B${i + 1}`, true); } catch (e) { /* 忽略单格失败 */ }
    }
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
      // enum 字段注入 select 下拉框（有默认值时不提供空选项）
      if (f.kind === 'input' && f.type === 'enum' && f.options && f.options.length && !f.readOnly) {
        const curVal = data[i][1] || '';
        const hasDefault = !!(f.default && f.options.includes(f.default));
        const select = document.createElement('select');
        select.className = 'cell-enum-select';
        select.style.cssText = 'width:100%;height:100%;border:none;background:transparent;font-size:13px;cursor:pointer;';
        select.innerHTML = (hasDefault ? '' : '<option value=""></option>') + f.options.map(o =>
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
    const r = await putSurveyRow(state.gridTable, sc.id, existing, state.user || '', gridRowVersion(sc.id));
    if (!r.ok) {
      showRowConflictDialog({
        tid: state.gridTable, scId: sc.id, myData: existing, conflict: r.conflict,
        saveFn: (data, bv) => putSurveyRow(state.gridTable, sc.id, recomputeComputedFields(data, state.gridTable), state.user || '', bv),
        onSaved: (rec, saved) => {
          state._gridSurveyMap[sc.id] = saved;
          markGridRowSaved(sc.id, rec, saved);
          renderSurveyForm();
          toast('签字已保存', 1200);
        },
        onLoaded: (data, ver) => {
          state._gridSurveyMap[sc.id] = data;
          state._gridVerMap[sc.id] = ver;
          state._gridBaseMap[sc.id] = JSON.parse(JSON.stringify(data));
          renderSurveyForm();
          renderSignCards(sc, data);
        },
      });
      return;
    }
    markGridRowSaved(sc.id, r.rec, existing);
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
  if (f.type === 'checkbox') v = (value === true || value === 'true' || value === 1 || value === '1' || value === '有') ? '是' : '否';
  // 率类：网格里输入 95（见 95%），落库存比率 0.95（不 ×100）
  if (f.type === 'percent') v = value === '' ? '' : pctToStore(value);
  if (f.type === 'number') v = value === '' ? '' : Number(value);
  existing[f.key] = v;
  existing['inspector'] = state.user || '';
  // 重算 computed 字段并更新网格
  rowFields.forEach((cf, idx) => {
    if (cf.kind !== 'computed') return;
    const cv = computeFieldValue(cf.formula, existing);
    existing[cf.key] = cv;
    if (state._grid && cv !== '') state._grid.setValueFromCoords(1, idx, fmtComputedDisplay(cf.formula, cv));
  });
  state._gridSurveyMap[sc.id] = existing;
  try {
    const r = await putSurveyRow(state.gridTable, sc.id, existing, state.user || '', gridRowVersion(sc.id));
    if (!r.ok) {
      // 乐观锁冲突：弹窗选择 合并/覆盖/加载最新
      showRowConflictDialog({
        tid: state.gridTable, scId: sc.id, myData: existing, conflict: r.conflict,
        saveFn: (data, bv) => putSurveyRow(state.gridTable, sc.id, recomputeComputedFields(data, state.gridTable), state.user || '', bv),
        onSaved: (rec, saved) => {
          state._gridSurveyMap[sc.id] = saved;
          markGridRowSaved(sc.id, rec, saved);
          renderSurveyForm();
        },
        onLoaded: (data, ver) => {
          state._gridSurveyMap[sc.id] = data;
          state._gridVerMap[sc.id] = ver;
          state._gridBaseMap[sc.id] = JSON.parse(JSON.stringify(data));
          renderSurveyForm();
        },
      });
      return;
    }
    markGridRowSaved(sc.id, r.rec, existing);
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
    // 正在记录该小班轨迹：用记录中的数组（含未落库的点）替换刚拉取的，避免显示/保存脱钩
    if (_scWatchId !== null && _scTrackScId === scId && _scTrackRef) {
      if (!state.scExtras) state.scExtras = { track: [], photos: [] };
      state.scExtras.track = _scTrackRef;
    }
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
    case 'enum': {
      // 空值显示默认值；旧 checkbox 布尔数据归一（管理情况 有/无 等）
      const opts = f.options || [];
      let cur = v;
      if (cur === true || cur === 'true') cur = opts[0];
      else if (cur === false || cur === 'false') cur = (f.default && opts.includes(f.default)) ? f.default : opts[opts.length - 1];
      else if (cur == null || cur === '') cur = f.default || '';
      return `<div class="enum-btns">${opts.map(opt =>
        `<button class="enum-btn ${cur === opt ? 'active' : ''}" data-action="enum-select" data-field="${f.key}" data-value="${escapeHtml(opt)}">${escapeHtml(opt)}</button>`
      ).join('')}</div>`;
    }
    case 'number': {
      const attrs = numAttrs(f);
      return `<input type="number" class="f-input" data-field="${f.key}" value="${escapeHtml(v)}" ${attrs}>`;
    }
    case 'percent':
      // 存比率（0.95），输入框显示 95（step 0.01 支持 95.24 等两位小数）
      return `<div class="input-with-suffix"><input type="number" class="f-input" data-field="${f.key}" value="${escapeHtml(pctToDisplay(v))}" min="0" max="100" step="0.01"><span class="suffix">%</span></div>`;
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
    case 'computed': {
      const cv = computeFieldValue(f.formula, state.formData);
      return `<div class="computed-val" data-computed-val="${f.key}">${escapeHtml(cv === '' ? '自动计算' : fmtComputedDisplay(f.formula, cv))}</div>`;
    }
    case 'sample_array':
      return renderSamplePanel(f);
    default:
      return `<input type="text" class="f-input" data-field="${f.key}" value="${escapeHtml(v)}">`;
  }
}

// ── 样地子数组编辑面板（samples，三表共用）──
function renderSamplePanel(f) {
  const arr = state.formData[f.key];
  const samples = Array.isArray(arr) ? arr : [];
  let html = `<div class="sample-panel">
    <div class="sample-panel-head">
      <span class="sample-count">共 ${samples.length} 个样地</span>
      <button class="btn-sample-add" data-action="sample-add" data-sample-key="${f.key}">+ 添加样地</button>
    </div>`;
  if (!samples.length) {
    html += `<div class="sample-empty">暂无样地，点击「添加样地」开始录入</div>`;
  } else {
    html += `<div class="sample-list">`;
    samples.forEach((s, i) => { html += renderSampleCard(f, s, i); });
    html += `</div>`;
  }
  html += `</div>`;
  return html;
}

// 新建样地对象：字段按 sample_fields 初始化，样圆号自动递增
function newSampleObject(sampleFields) {
  const obj = {};
  (sampleFields || []).forEach(sf => { obj[sf.key] = ''; });
  obj.photos = [];
  return obj;
}

function renderSampleCard(f, sample, idx) {
  const fieldsHtml = (f.sample_fields || []).map(sf => renderSampleField(sf, sample, idx, f.key)).join('');
  const photos = Array.isArray(sample.photos) ? sample.photos.filter(Boolean) : [];
  const photosTxt = photos.length
    ? `<div class="sample-photo-names" title="${escapeHtml(photos.join('；'))}">📷 ${escapeHtml(photos.join('；'))}</div>`
    : '';
  return `<div class="sample-card" data-sample-idx="${idx}">
    <div class="sample-card-head">
      <span class="sample-card-title">样地 ${sample.no || idx + 1}</span>
      <div class="sample-card-actions">
        <button class="btn-gps" data-action="get-gps-sample" data-sample-key="${f.key}" data-sample-idx="${idx}" title="一键获取该样地坐标">📍坐标</button>
        <label class="btn-photo" title="拍照（水印含样地号）">📷拍照
          <input type="file" accept="image/*" capture="environment" class="f-sample-photo" data-sample-key="${f.key}" data-sample-idx="${idx}" hidden>
        </label>
        <button class="btn-sample-del" data-action="sample-del" data-sample-key="${f.key}" data-sample-idx="${idx}" title="删除样地">✕</button>
      </div>
    </div>
    <div class="sample-card-body">${fieldsHtml}</div>
    ${photosTxt}
  </div>`;
}

function renderSampleField(sf, sample, idx, sampleKey) {
  // auto 字段（样地号）自动编号，不渲染输入框
  if (sf.auto) return '';
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
  // 样地统计（computed）随保存写入 data_json，与网格页行为一致
  (def.input_columns || []).forEach(f => {
    if (f.type === 'computed') state.formData[f.key] = computeFieldValue(f.formula, state.formData);
  });
  const data = Object.assign({}, state.formData);
  const inspector = data.inspector || data.surveyor || state.user || '';
  const scId = state.subcompartment.id;
  const isUpdate = !!state.editRecordId;
  // 乐观锁基线：records 里尚未被本次保存覆盖的旧记录（含 version），无记录 = 0（新建）
  const baseRec = (state.records[tid] || []).find(r => r.subcompartment_id === scId);
  const baseVersion = baseRec ? (baseRec.version != null ? baseRec.version : 1) : 0;
  const baseData = baseRec ? (baseRec.data || {}) : {};
  // records 条目落库/更新（保存成功后同步 version）
  const applySavedRec = (savedRec) => {
    if (!state.records[tid]) state.records[tid] = [];
    const idx = state.records[tid].findIndex(r => r.id === savedRec.id);
    if (idx >= 0) state.records[tid][idx] = Object.assign({}, state.records[tid][idx], savedRec);
    else state.records[tid].push(savedRec);
    state.editRecordId = savedRec.id;
  };

  try {
    // 一对一模型：统一走 PUT survey/<tid>/rows upsert（带乐观锁版本）
    const r = await putSurveyRow(tid, scId, data, inspector, baseVersion);
    if (!r.ok) {
      state.busy = false;
      showRowConflictDialog({
        tid, scId, myData: data, myBase: baseData, conflict: r.conflict,
        saveFn: (d, bv) => putSurveyRow(tid, scId, recomputeComputedFields(d, tid), inspector, bv),
        onSaved: (rec, saved) => {
          applySavedRec(rec);
          state.formData = Object.assign({}, saved);
          state.busy = false;
          renderProgress();
          renderContent();
        },
        onLoaded: (loaded, ver) => {
          if (baseRec) {
            baseRec.data = loaded;
            baseRec.version = ver;
          }
          state.formData = Object.assign({}, loaded);
          state.busy = false;
          renderProgress();
          renderContent();
        },
      });
      return;
    }
    applySavedRec(r.rec);
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

// ── 导出（基本信息 Excel / 轨迹 GPX）──

// 当前基本信息导出的分类：样地页取当前小班分类，网格页取分类筛选（空=全部分类）
function currentBaseExportCategory() {
  if (state.view === 'samples' && state.gridSubcompartment) return state.gridSubcompartment.category || '';
  return state.gridCategory || '';
}

// 导出文件落盘：App 内走原生 MediaStore 写入 Download/验收导出/，toast 提示真实
// 绝对路径便于手机查找；浏览器回退 <a download>。返回 true = 原生已保存（已提示路径）。
async function downloadExportFile(blob, filename) {
  if (isApp()) {
    const plugin = permPlugin();
    if (plugin && plugin.saveFile) {
      try {
        const base64 = await fileToBase64(blob);
        const r = await plugin.saveFile({ base64, name: filename });
        const p = r && r.path;
        if (p) {
          const dir = p.slice(0, p.lastIndexOf('/') + 1);
          if (dir) state._exportSaveDir = dir;
          toast(`已保存到：${p}`, 5000);
          return true;
        }
      } catch (e) { /* 原生失败回退下载 */ }
    }
  }
  try {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 3000);
  } catch (e) { /* 下载失败不影响主流程 */ }
  return false;
}

// 导出基本信息 Excel（当前项目 + 当前分类；分类为空 = 全部分类 3 个 sheet）
async function exportBase() {
  if (!state.project) { toast('请先选择项目'); return; }
  const cat = currentBaseExportCategory();
  toast('正在导出基本信息…', 1500);
  try {
    const q = cat ? `?cat=${encodeURIComponent(cat)}` : '';
    const res = await fetch(`api/projects/${state.project.id}/export_base${q}`);
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.error || `导出失败 (${res.status})`);
    }
    const blob = await res.blob();
    const filename = `${state.project.name}${cat ? '_' + cat : ''}_基本信息.xlsx`;
    const nativeSaved = await downloadExportFile(blob, filename);
    if (!nativeSaved) toast(cat ? `「${cat}」基本信息已导出` : '全部分类基本信息已导出');
  } catch (e) {
    toast('基本信息导出失败：' + e.message, 2500);
  }
}

// ── 坐标基准转换（GCJ-02 火星坐标 ↔ WGS-84）──
// 国内网络定位（基站/WiFi，无 GMS 的华为/鸿蒙设备林下 GPS 失效时常见）返回 GCJ-02，
// GPS 返回 WGS-84。库内与导出统一 WGS-84：精度差(≥50m)的定位点视为网络源，
// 反算纠偏并打标记，全部保留不丢弃。
const _GCJ_PI = 3.14159265358979324;
const _GCJ_A = 6378245.0;
const _GCJ_EE = 0.00669342162296594323;
const NET_ACC_THRESHOLD = 50;  // accuracy ≥ 50m 视为网络定位（GCJ-02）

function _tLat(x, y) {
  let r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  r += (20 * Math.sin(6 * x * _GCJ_PI) + 20 * Math.sin(2 * x * _GCJ_PI)) * 2 / 3;
  r += (20 * Math.sin(y * _GCJ_PI) + 40 * Math.sin(y / 3 * _GCJ_PI)) * 2 / 3;
  r += (160 * Math.sin(y / 12 * _GCJ_PI) + 320 * Math.sin(y * _GCJ_PI / 30)) * 2 / 3;
  return r;
}
function _tLng(x, y) {
  let r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  r += (20 * Math.sin(6 * x * _GCJ_PI) + 20 * Math.sin(2 * x * _GCJ_PI)) * 2 / 3;
  r += (20 * Math.sin(x * _GCJ_PI) + 40 * Math.sin(x / 3 * _GCJ_PI)) * 2 / 3;
  r += (150 * Math.sin(x / 12 * _GCJ_PI) + 300 * Math.sin(x / 30 * _GCJ_PI)) * 2 / 3;
  return r;
}
function _outOfChina(lng, lat) {
  return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271;
}
// WGS-84 → GCJ-02（仅显示用：高德瓦片是 GCJ-02 基准）
function wgs2gcj(lng, lat) {
  if (_outOfChina(lng, lat)) return [lng, lat];
  let dLat = _tLat(lng - 105, lat - 35);
  let dLng = _tLng(lng - 105, lat - 35);
  const radLat = lat / 180 * _GCJ_PI;
  let magic = Math.sin(radLat);
  magic = 1 - _GCJ_EE * magic * magic;
  const sqrtMagic = Math.sqrt(magic);
  dLat = (dLat * 180) / ((_GCJ_A * (1 - _GCJ_EE)) / (magic * sqrtMagic) * _GCJ_PI);
  dLng = (dLng * 180) / (_GCJ_A / sqrtMagic * Math.cos(radLat) * _GCJ_PI);
  return [lng + dLng, lat + dLat];
}
// GCJ-02 → WGS-84（一次反算，误差约 1~2m，远小于 300~700m 偏移）
function gcj2wgs(lng, lat) {
  if (_outOfChina(lng, lat)) return [lng, lat];
  const g = wgs2gcj(lng, lat);
  return [lng * 2 - g[0], lat * 2 - g[1]];
}
// 定位结果统一入口：网络源(GCJ-02)反算为 WGS-84，acc 精度存档、adj 打标，不丢弃任何点。
// 网络源判定优先级：原生插件点携带的 provider（network=GCJ-02 必纠 / gps=WGS-84 必不纠）
// > WebView 点无 provider，回退 accuracy≥50m 启发式
function _gpsFix(coords) {
  let lng = coords.longitude, lat = coords.latitude;
  const acc = (typeof coords.accuracy === 'number' && isFinite(coords.accuracy)) ? Math.round(coords.accuracy) : null;
  let isNet = null;
  if (coords.provider === 'network') isNet = true;
  else if (coords.provider === 'gps') isNet = false;
  if (isNet === null) isNet = (acc !== null && acc >= NET_ACC_THRESHOLD);
  let adj = 0;
  if (isNet) {
    const w = gcj2wgs(lng, lat);
    lng = w[0]; lat = w[1]; adj = 1;
  }
  return { lng: lng.toFixed(6), lat: lat.toFixed(6), acc, adj };
}

// ── GPS ──
function getGPS() {
  if (!navigator.geolocation) { toast('设备不支持定位'); return; }
  toast('正在获取定位…', 1500);
  navigator.geolocation.getCurrentPosition(pos => {
    const fix = _gpsFix(pos.coords);
    const lng = fix.lng;
    const lat = fix.lat;
    state.formData.longitude = lng;
    state.formData.latitude = lat;
    const lngEl = qs('[data-gps-val="longitude"]');
    const latEl = qs('[data-gps-val="latitude"]');
    if (lngEl) lngEl.textContent = lng;
    if (latEl) latEl.textContent = lat;
    toast(fix.adj ? '定位成功（网络定位已纠偏）' : '定位成功');
  }, err => {
    handleGeoError(err);
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
}

// 样地 GPS：一键同时填入坐标x(经度)/坐标y(纬度)到指定样地条目
function getSampleGPS(sampleKey, idx) {
  if (!navigator.geolocation) { toast('设备不支持定位'); return; }
  toast('正在获取定位…', 1500);
  navigator.geolocation.getCurrentPosition(pos => {
    const fix = _gpsFix(pos.coords);
    const lng = fix.lng;
    const lat = fix.lat;
    const arr = state.formData[sampleKey];
    if (!Array.isArray(arr) || !arr[idx]) { toast('定位成功但样地已失效，请重试'); return; }
    arr[idx].x = lng;
    arr[idx].y = lat;
    // 更新输入框显示值
    const xEl = qs(`[data-sample-field="x"][data-sample-key="${sampleKey}"][data-sample-idx="${idx}"]`);
    const yEl = qs(`[data-sample-field="y"][data-sample-key="${sampleKey}"][data-sample-idx="${idx}"]`);
    if (xEl) xEl.value = lng;
    if (yEl) yEl.value = lat;
    toast(fix.adj ? '定位成功（网络定位已纠偏）' : '定位成功');
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
              <li><b>选择表格</b>：点击顶部标签切换表1~表3（人工造林/封山育林/退化林修复）</li>
              <li><b>录入数据</b>：每个小班每张表仅一条记录，按分组填写字段后点击"保存"</li>
              <li><b>网格直填</b>：也可在小班列表点击"📊 网格调查"，在 Excel 式表格中直接编辑单元格</li>
              <li><b>样地调查</b>：选中小班后点工具栏"样地"按钮进入样地管理（"返回小班"切回），点"+ 添加样地"新增（样地号自动递增，无坐标时自动GPS定位），填写面积/种植/成活株数；已有坐标时点"📍坐标"需确认后才覆盖（默认否），点"📷拍照"拍样地照片（按钮显示已拍张数，水印含样地号，照片不参与统计）</li>
              <li><b>自动统计</b>：样地填好后，小班查数株数/合格株树/合格率自动计算</li>
              <li><b>打卡/轨迹/照片</b>：在小班列表点击对应按钮</li>
              <li><b>数据导出</b>：Excel 导出（基本信息/样地）请在管理后台操作；调查页右上角"轨迹"可导出本项目轨迹 GPX</li>
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
              <li><b>样地增删</b>：样地页倒序排列（新样地在最上面），点击"+ 添加样地"新增（前一样地需填全面积/种植/成活，样地号自动递增无需手填），点 ✕ 删除单个样地</li>
              <li><b>样地保存</b>：光标离开输入框自动保存，输入停顿1秒后也会自动保存，顶部实时显示保存状态（●未保存/⏳保存中/✓已保存/✗失败点保存重试）；保存失败多为网络不稳，会自动重试</li>
              <li><b>样地坐标</b>：经纬度不可手填，点"📍坐标"按钮GPS自动获取后显示在卡片头</li>
              <li><b>样地照片</b>：每个样地卡片有独立拍照按钮，可拍多张，仅保存到相册并记录文件名</li>
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
  // 正在记录轨迹时切换小班：先停止并保存到原小班，防止轨迹丢失/错挂
  if (_scWatchId !== null && _scTrackScId && _scTrackScId !== scId) {
    await stopTrackRecording();
    toast('已自动保存并停止上一小班轨迹', 2200);
  }
  try {
    const j = await fetchJSON(`api/subcompartments/rows/${scId}`);
    state.subcompartment = j.row;
    state.subcompartmentData = { prefilled: j.prefilled || {}, row: j.row };
    state.scExtras = j.extras || null;
    // 正在记录该小班轨迹：用记录中的数组（含未落库的点）替换刚拉取的，避免显示/保存脱钩
    if (_scWatchId !== null && _scTrackScId === scId && _scTrackRef) {
      if (!state.scExtras) state.scExtras = { track: [], photos: [] };
      state.scExtras.track = _scTrackRef;
    }
    _renderScPanel();
  } catch (e) {
    toast('加载小班信息失败：' + e.message, 2500);
  }
}

// 打卡成功后自动填样地坐标：按小班分类定位所属表，样地坐标x/y 为空才填（不覆盖手工值）
async function fillSampleCoords(scId, lng, lat) {
  if (!state.project || !scId) return;
  try {
    const row = (state.scAllRows || []).find(r => r.id === scId) || state.gridSubcompartment;
    const cat = row ? (row.category || '') : '';
    const tid = CATEGORY_TO_TABLE[cat] || state.gridTable || 'table1';
    const def = getGridTableDef(tid);
    if (!def) return;
    const fields = def.input_columns || [];
    const hasCoord = fields.some(f => f.key === 'sample_coord_x');
    const hasDate = fields.some(f => f.key === 'inspect_time');
    if (!hasCoord && !hasDate) return;
    // 拉取该表全部记录，合并保存（避免覆盖其它字段）；
    // 携记录 version 走乐观锁，409 冲突时静默重拉重试一次，仍冲突放弃（后台补填，不打扰）
    const doFill = async () => {
      const j = await fetchJSON(`api/projects/${state.project.id}/survey/${tid}/rows`);
      const rec = (j.rows || []).find(r => r.subcompartment_id === scId);
      const existing = Object.assign({}, (rec && rec.data) || {});
      if (tid === state.gridTable) {
        // 顺带全量刷新缓存（数据+版本+基线快照），打卡状态筛选立即准确
        applyLoadedSurveyRows(j.rows || []);
      }
      let changed = false;
      let coordFilled = false;
      // 样地坐标：为空才填（不覆盖手工精测值）
      if (hasCoord) {
        if (existing.sample_coord_x == null || existing.sample_coord_x === '') {
          existing.sample_coord_x = Number(lng); changed = true; coordFilled = true;
        }
        if (existing.sample_coord_y == null || existing.sample_coord_y === '') {
          existing.sample_coord_y = Number(lat); changed = true; coordFilled = true;
        }
      }
      // 验收时间：为空则填打卡当天（坐标已有值也照填，不受上面坐标提前返回影响）
      if (hasDate && (existing.inspect_time == null || existing.inspect_time === '')) {
        const d = new Date();
        const pad = n => String(n).padStart(2, '0');
        existing.inspect_time = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        changed = true;
      }
      if (!changed) return { skipped: true };
      if (!existing.inspector) existing.inspector = state.user || '';
      const bv = rec ? (rec.version != null ? rec.version : 1) : 0;
      const r = await putSurveyRow(tid, scId, existing, state.user || '', bv);
      return { r, existing, coordFilled };
    };
    let res = await doFill();
    if (res.skipped) return;
    if (res.r && !res.r.ok) {
      // 版本冲突：他人在我读取后先保存 → 重拉最新再试一次（只填空值，天然安全）
      res = await doFill();
      if (res.skipped || !res.r || !res.r.ok) return;
    }
    const { existing, coordFilled } = res;
    // 同步缓存 + 刷新网格显示（当前表匹配且正显示该小班时）
    if (tid === state.gridTable) {
      state._gridSurveyMap[scId] = existing;
      markGridRowSaved(scId, res.r.rec, existing);
      (state._gridRowFields || []).forEach((f, idx) => {
        if ((f.key === 'sample_coord_x' || f.key === 'sample_coord_y' || f.key === 'inspect_time') && state._grid) {
          state._grid.setValueFromCoords(1, idx, String(existing[f.key]));
        }
      });
      refreshGridToolbar();  // 打卡状态筛选下拉选项随坐标更新
    }
    toast(coordFilled ? '样地坐标、验收时间已填入' : '验收时间已填入', 1500);
  } catch (e) {
    // 填写失败不影响打卡本身
  }
}

// 从小班列表页快速打卡（不打开面板）
async function quickCheckin(scId) {
  if (!scId) return;
  if (!navigator.geolocation) { toast('设备不支持定位'); return; }
  toast('正在获取定位…', 1500);
  navigator.geolocation.getCurrentPosition(async pos => {
    const fix = _gpsFix(pos.coords);
    const lng = fix.lng;
    const lat = fix.lat;
    try {
      await fetchJSON(`api/subcompartments/rows/${scId}/checkin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lng, lat }),
      });
      toast('✓ 打卡成功' + (fix.adj ? '（网络定位已纠偏）' : ''));
      await fillSampleCoords(scId, lng, lat);
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
            ${photos.length
              ? `<div class="sc-photo-save-path">📁 已保存到目录：${escapeHtml(photoSaveDirShown(state.subcompartment))}</div>`
              : `<div class="sc-photo-save-path empty">📁 拍摄后自动保存到：${escapeHtml(photoSaveDirHint(state.subcompartment))}（文件名：分类_乡镇_村_小班_时间.jpg）</div>`}
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
    const fix = _gpsFix(pos.coords);
    const lng = fix.lng;
    const lat = fix.lat;
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
      toast('打卡成功' + (fix.adj ? '（网络定位已纠偏）' : ''));
      await fillSampleCoords(scId, lng, lat);
    } catch (e) {
      toast('打卡失败：' + e.message, 2500);
    }
  }, err => {
    handleGeoError(err);
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
}

let _scWatchId = null;     // 记录中哨兵：'bg'=原生插件模式，数字=watchPosition id
let _scTrackScId = null;   // 正在记录轨迹的小班 id
let _scTrackRef = null;    // 轨迹点数组引用（scExtras 被替换后仍有效）
let _scTrackTimer = null;  // 自动保存定时器
let _scBgGeo = null;       // 后台定位插件句柄（原生壳内非空）

// 后台定位：自研原生插件 BgLocation（平台 LocationManager + location 型前台服务，
// 通用方案——不依赖 Google Play Services，GMS 安卓与 HMS 鸿蒙均可用；社区插件
// 依赖 gms fused 在无 GMS 设备上静默零回调，已弃用）。浏览器/旧 APK 无此插件，
// 回退 navigator.geolocation（仅前台有效）。
// 注意：RETURN_CALLBACK 方法经 nativeCallback 同步返回 callbackId（非 Promise），
// 不可对其 .then/.catch；错误经回调第二参数 (null, err) 送回。
function bgGeoPlugin() {
  try {
    if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.BgLocation) {
      return window.Capacitor.Plugins.BgLocation;
    }
  } catch (e) { /* 忽略 */ }
  return null;
}

// 停原生 watcher（停前台服务/通知）；容错：已停止或未就绪时静默
function _bgStopWatcher() {
  const plugin = _scBgGeo;
  _scBgGeo = null;
  if (plugin) {
    try { plugin.stopWatcher().catch(() => {}); } catch (e) { /* 忽略 */ }
  }
}
let _trackMap = null;     // Leaflet 地图实例
let _trackLayer = null;   // 轨迹折线图层
let _trackMarker = null;  // 当前位置标记

function openTrackMap() {
  const noTrack = !state.scExtras || !state.scExtras.track || !state.scExtras.track.length;
  if (noTrack && !state._scTracking) {
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
  // 高德瓦片（GCJ-02 基准）：库内轨迹为 WGS-84，画图时经 wgs2gcj 转换对齐瓦片
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
  // 库内 WGS-84 → 高德瓦片 GCJ-02（仅显示转换，不改库内数据）
  const latlngs = track.filter(p => p.lat && p.lng).map(p => {
    const g = wgs2gcj(parseFloat(p.lng), parseFloat(p.lat));
    return [g[1], g[0]];
  });
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
  // 别的小班轨迹仍在记录：先自动停止并保存（单设备 GPS 同时只能记一条）
  if (_scWatchId !== null && _scTrackScId && _scTrackScId !== scId) {
    await stopTrackRecording();
    toast('已自动保存并停止上一小班轨迹', 2200);
  }
  if (_scWatchId !== null) {
    await stopTrackRecording();
    _renderScPanel();
    _updateTrackMapState();
    return;
  }
  if (!navigator.geolocation) { toast('设备不支持定位'); return; }
  if (!state.scExtras) state.scExtras = { track: [], photos: [] };
  if (!state.scExtras.track) state.scExtras.track = [];
  state._scTracking = true;
  toast('开始记录轨迹（每15秒自动保存）…', 2500);
  _scTrackScId = scId;                       // 记录轨迹归属的小班（防切换后丢失）
  _scTrackRef = state.scExtras.track;        // 引用住数组，即使 scExtras 被替换
  let savedLen = 0;
  _scTrackTimer = setInterval(async () => {
    // 定时静默自动保存：中途杀 APP/刷新/断网也能保住已记录点
    if (!_scTrackRef || _scTrackRef.length === savedLen) return;
    const ok = await _postTrack(_scTrackScId, _scTrackRef, true);
    if (ok) savedLen = _scTrackRef.length;
  }, 15000);
  // 收点入轨：纠偏存档 → 连续重复去重 → 入 _scTrackRef 并实时同步 UI
  const trackPush = (fix) => {
    if (!_scTrackRef) return;
    const pt = { lng: fix.lng, lat: fix.lat, t: new Date().toISOString() };
    if (fix.acc !== null && fix.acc !== undefined) pt.acc = fix.acc;  // 精度存档，事后可甄别网络点
    if (fix.adj) pt.adj = 1;                                          // 已纠偏标记（GCJ-02→WGS-84）
    const last = _scTrackRef[_scTrackRef.length - 1];
    if (last && last.lng === pt.lng && last.lat === pt.lat) return;
    _scTrackRef.push(pt);
    // 同步挂钩：scExtras 可能被重新拉取替换，确保 UI/轨迹图实时显示记录中的点
    if (state.scExtras) state.scExtras.track = _scTrackRef;
  };
  // 异常中止：停采集与定时器，已记录的点仍保存（避免整段丢失）
  const trackAbort = (err) => {
    _bgStopWatcher();
    if (_scWatchId !== null && _scWatchId !== 'bg') {
      navigator.geolocation.clearWatch(_scWatchId);
    }
    _scWatchId = null;
    state._scTracking = false;
    if (_scTrackTimer) { clearInterval(_scTrackTimer); _scTrackTimer = null; }
    if (_scTrackScId && _scTrackRef && _scTrackRef.length) _postTrack(_scTrackScId, _scTrackRef, false);
    _scTrackScId = null;
    _scTrackRef = null;
    _renderScPanel();
    _updateTrackMapState();
    if (err) handleGeoError(err);
  };
  // 原生壳优先自研 BgLocation 插件（前台服务，灭屏/后台持续记录）；
  // 插件缺失/启动异常/浏览器 → 回退 watchPosition（仅前台有效）
  const bgGeo = bgGeoPlugin();
  if (bgGeo) {
    _scWatchId = 'bg';   // 哨兵：原生插件模式记录中
    _scBgGeo = bgGeo;
    try {
      // nativeCallback 同步返回 callbackId；错误（含授权被拒）经回调 (null, err) 送回
      bgGeo.startWatcher({
        title: '验收轨迹记录中',
        message: '正在后台记录调查轨迹，请保持定位开启',
      }, (loc, err) => {
        if (_scWatchId === null || !_scTrackRef) return;  // 已停止则丢弃
        if (err) { trackAbort(err); return; }
        trackPush(_gpsFix({
          longitude: loc.longitude,
          latitude: loc.latitude,
          accuracy: loc.accuracy,
          provider: loc.provider,   // 'gps'=WGS-84 / 'network'=GCJ-02，精准纠偏
        }));
      });
      toast('已启用后台持续定位（灭屏/后台均可记录）', 2200);
    } catch (e) {
      // 插件异常：回退 watchPosition（前台仍可记录）
      _bgStopWatcher();
      _scWatchId = null;
    }
  }
  if (_scWatchId === null) {
    _scWatchId = navigator.geolocation.watchPosition(pos => {
      if (!_scTrackRef) return;
      trackPush(_gpsFix(pos.coords));
    }, err => {
      trackAbort(err);
    }, { enableHighAccuracy: true, maximumAge: 1000, timeout: 30000 });
  }
  // 立即取首点仅 watchPosition 回退模式需要（GPS 冷启动时 watch 回调可能数十秒不来）。
  // 原生插件模式跳过：WebView 秒回的点常为网络定位且无 provider 标记（accuracy<50m
  // 不触发启发式纠偏，GCJ-02 原样入库），必成偏移几百米的首点；首点交给原生层
  // 30 秒 GPS 优先宽限策略出。
  if (_scWatchId !== 'bg') {
    navigator.geolocation.getCurrentPosition(pos => {
      if (_scWatchId === null || !_scTrackRef) return;  // 已停止则丢弃
      if (!_scTrackRef.length) {
        const fix = _gpsFix(pos.coords);
        const pt = { lng: fix.lng, lat: fix.lat, t: new Date().toISOString() };
        if (fix.acc !== null) pt.acc = fix.acc;
        if (fix.adj) pt.adj = 1;
        _scTrackRef.push(pt);
        if (state.scExtras) state.scExtras.track = _scTrackRef;
        _updateTrackMapState();
      }
    }, () => {}, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
  }
  _renderScPanel();
  _updateTrackMapState();
}

// 停止记录并保存（点「停止」或切换小班/退后台时调用）
async function stopTrackRecording() {
  // 插件模式：先移除 watcher 停前台服务（未 resolve 的 addWatcher 由 then 竞态分支自清）
  _bgStopWatcher();
  if (_scWatchId !== null && _scWatchId !== 'bg') {
    navigator.geolocation.clearWatch(_scWatchId);
  }
  _scWatchId = null;
  if (_scTrackTimer) { clearInterval(_scTrackTimer); _scTrackTimer = null; }
  state._scTracking = false;
  if (_scTrackScId && _scTrackRef) {
    if (_scTrackRef.length) {
      await _postTrack(_scTrackScId, _scTrackRef, false);
    } else {
      toast('未记录到轨迹点（GPS 信号弱或未移动），原轨迹已保留', 2600);
    }
  }
  _scTrackScId = null;
  _scTrackRef = null;
}

// 提交轨迹到指定小班（quiet=true 静默自动保存，不弹提示）
async function _postTrack(scId, points, quiet) {
  try {
    await fetchJSON(`api/subcompartments/rows/${scId}/track`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ points }),
    });
    if (!quiet) toast(`轨迹已保存（${points.length} 点）`);
    return true;
  } catch (e) {
    if (!quiet) toast('轨迹保存失败：' + e.message, 2500);
    return false;
  }
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
// 照片保存目录：Pictures/{年度}年度/{分类}/{调查小班号}号调查小班（App 内原生
// MediaStore 创建多级子目录；原生保存成功后以返回的真实路径为准）
const PHOTO_SAVE_DIR = '/storage/emulated/0/Pictures/';

// 目录名安全化：去文件系统非法字符
function _dirSeg(v) {
  return String(v == null ? '' : v).trim().replace(/[\\/:*?"<>|]+/g, '_');
}

// 照片年度：小班计划年度（GDB，可能带 .0）→ 项目名「(2023 年度)」正则 → 当前年
function _photoYear(sc) {
  const pf = sc.prefilled || (state.subcompartmentData && state.subcompartmentData.prefilled) || {};
  const py = pf.plan_year;
  if (py != null && String(py).trim() !== '') {
    const n = parseFloat(py);
    if (!isNaN(n) && n > 0) return String(Math.round(n));
  }
  const m = /(\d{4})\s*年度/.exec((state.project && state.project.name) || '');
  if (m) return m[1];
  return String(new Date().getFullYear());
}

// 照片保存子目录：{年度}年度/{分类}/{调查小班号}号调查小班
function photoSaveSubdir(sc) {
  if (!sc) return '验收照片';
  const no = sc.subcompartment != null && String(sc.subcompartment).trim() !== ''
    ? sc.subcompartment : (sc.subcompartment_label || '');
  const segs = [
    `${_photoYear(sc)}年度`,
    sc.category || '',
    no !== '' ? `${no}号调查小班` : '',
  ].map(_dirSeg).filter(Boolean);
  return segs.length ? segs.join('/') : '验收照片';
}

// 预计保存目录（拍照前提示用；拍照后以原生返回的真实路径为准）
function photoSaveDirHint(sc) {
  return PHOTO_SAVE_DIR + photoSaveSubdir(sc) + '/';
}

// 已保存目录显示：最近一次原生返回的真实路径（仅当属于当前小班子目录时），
// 否则回退预计目录（跨小班残留 _photoSaveDir 防串显）
function photoSaveDirShown(sc) {
  const subdir = photoSaveSubdir(sc);
  if (state._photoSaveDir && state._photoSaveDirSubdir === subdir) return state._photoSaveDir;
  return PHOTO_SAVE_DIR + subdir + '/';
}

// 导出文件（Excel/轨迹ZIP）保存目录（默认值；App 内原生保存成功后以返回的真实路径为准）
const EXPORT_SAVE_DIR = '/storage/emulated/0/Download/验收导出/';

function buildPhotoName(sc, origName, sampleNo) {
  const clean = (v) => (v == null ? '' : String(v).trim()).replace(/[\\/:*?"<>|\s]+/g, '_');
  const parts = [
    clean(sc.category),
    clean(sc.township),
    clean(sc.village),
    clean(sc.subcompartment_label || sc.subcompartment),
  ];
  // 样地级照片：文件名带样地号（如 …_1-5小班_样地2_时间.jpg）
  if (sampleNo != null && sampleNo !== '') parts.push(`样地${sampleNo}`);
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  parts.push(stamp);
  const name = parts.filter(Boolean).join('_');
  const dot = origName ? origName.lastIndexOf('.') : -1;
  const ext = (dot >= 0) ? origName.slice(dot) : '';
  return name + ext;
}

// ── 照片水印：左下角 经纬度 + 备注（黑字白边，参考经典水印相机格式）──

// 照片备注：分类 州市县乡村小班
function buildPhotoRemark(sc) {
  if (!sc) return '';
  // 网格页小班行自带 prefilled；详情页 state.subcompartment 需回退 subcompartmentData.prefilled
  const pf = sc.prefilled || (state.subcompartmentData && state.subcompartmentData.prefilled) || {};
  const addr = [
    pf.city != null ? String(pf.city) : '',
    pf.county != null ? String(pf.county) : '',
    sc.township != null ? String(sc.township) : (pf.township != null ? String(pf.township) : ''),
    sc.village != null ? String(sc.village) : (pf.village != null ? String(pf.village) : ''),
  ].join('');
  const label = sc.subcompartment_label || sc.subcompartment || '';
  return [sc.category, addr, label !== '' ? `${label}小班` : ''].filter(Boolean).join(' ').trim();
}

// 从 JPEG EXIF 读 GPS（相机实拍坐标）；无 EXIF / 解析失败 → null
function readExifGps(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const dv = new DataView(reader.result);
        if (dv.byteLength < 8 || dv.getUint16(0) !== 0xFFD8) return resolve(null);
        let off = 2;
        while (off + 4 < dv.byteLength) {
          if (dv.getUint8(off) !== 0xFF) break;
          const marker = dv.getUint8(off + 1);
          if (marker === 0xD8 || marker === 0x01 || (marker >= 0xD0 && marker <= 0xD7)) { off += 2; continue; }
          const size = dv.getUint16(off + 2);
          // APP1 段且以 "Exif\0\0" 开头
          if (marker === 0xE1 && off + 10 < dv.byteLength
              && dv.getUint32(off + 4) === 0x45786966 && dv.getUint16(off + 8) === 0x0000) {
            return resolve(_parseTiffGps(dv, off + 10));
          }
          if (marker === 0xDA) break; // SOS：图像数据开始，后面无 EXIF
          off += 2 + size;
        }
      } catch (e) { /* 解析失败按无 GPS 处理 */ }
      resolve(null);
    };
    reader.onerror = () => resolve(null);
    reader.readAsArrayBuffer(file.slice(0, 256 * 1024)); // EXIF 位于文件头部
  });
}

// 解析 TIFF 头 EXIF GPS IFD（返回 {lng, lat} 十进制度字符串）
function _parseTiffGps(dv, base) {
  const le = dv.getUint16(base) === 0x4949; // "II" 小端
  const u16 = (o) => dv.getUint16(o, le);
  const u32 = (o) => dv.getUint32(o, le);
  const ifd0 = base + u32(base + 4);
  let gpsOff = 0;
  const n0 = u16(ifd0);
  for (let i = 0; i < n0; i++) {
    const e = ifd0 + 2 + i * 12;
    if (u16(e) === 0x8825) { gpsOff = base + u32(e + 8); break; } // GPS IFD 指针
  }
  if (!gpsOff) return null;
  const rat = (o) => { const d = u32(o + 4); return d ? u32(o) / d : 0; };
  const valOff = (e, bytes) => (bytes <= 4 ? e + 8 : base + u32(e + 8)); // ≤4字节内联存储
  let latRef = '', lat = null, lngRef = '', lng = null;
  const ng = u16(gpsOff);
  for (let i = 0; i < ng; i++) {
    const e = gpsOff + 2 + i * 12;
    const tag = u16(e);
    if (tag === 1) latRef = String.fromCharCode(dv.getUint8(valOff(e, 2)));
    else if (tag === 2) { const o = valOff(e, 24); lat = rat(o) + rat(o + 8) / 60 + rat(o + 16) / 3600; }
    else if (tag === 3) lngRef = String.fromCharCode(dv.getUint8(valOff(e, 2)));
    else if (tag === 4) { const o = valOff(e, 24); lng = rat(o) + rat(o + 8) / 60 + rat(o + 16) / 3600; }
  }
  if (lat == null || lng == null) return null;
  if (latRef === 'S' && lat > 0) lat = -lat;
  if (lngRef === 'W' && lng > 0) lng = -lng;
  return { lng: lng.toFixed(6), lat: lat.toFixed(6) };
}

// 在照片左下角绘制水印（时间 + 经纬度 + 备注 + 样地号，黑字白边，左对齐），返回 JPEG Blob
async function stampPhotoMeta(file, remark, lng, lat, sampleNo) {
  let bmp;
  try { bmp = await createImageBitmap(file, { imageOrientation: 'from-image' }); }
  catch (e) { bmp = await createImageBitmap(file); }
  const canvas = document.createElement('canvas');
  canvas.width = bmp.width;
  canvas.height = bmp.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bmp, 0, 0);
  // 水印行（label 前缀，逐行换行）：时间 / 坐标 / 备注 / 样地号（样地级照片才有）
  const d = new Date();
  const p2 = n => String(n).padStart(2, '0');
  const lines = [
    `时间：${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`,
    lng !== '' && lat !== '' ? `坐标：N ${lat}  E ${lng}` : '坐标：',
    remark ? `备注：${remark}` : '备注：',
  ];
  if (sampleNo != null && sampleNo !== '') lines.push(`样地号：${sampleNo}`);
  if (lines.length) {
    const scale = Math.max(1, bmp.width / 1200);
    const fs = Math.max(18, Math.round(26 * scale));
    const pad = Math.round(16 * scale);
    const lineH = Math.round(fs * 1.4);
    ctx.font = `bold ${fs}px sans-serif`;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    ctx.lineJoin = 'round';
    let y = canvas.height - pad - (lines.length - 1) * lineH;
    lines.forEach(t => {
      ctx.lineWidth = Math.max(2, Math.round(fs / 5));
      ctx.strokeStyle = 'rgba(255,255,255,0.95)'; // 白边
      ctx.strokeText(t, pad, y);
      ctx.fillStyle = 'rgba(20,20,20,0.9)';
      ctx.fillText(t, pad, y);
      y += lineH;
    });
  }
  return await new Promise((res, rej) =>
    canvas.toBlob(b => (b ? res(b) : rej(new Error('照片水印编码失败'))), 'image/jpeg', 0.92));
}

// 拍照统一处理：EXIF GPS 优先（回退当前定位）→ 绘水印（可含样地号）→ 存相册，返回 {name, lng, lat}
async function stampAndSavePhoto(sc, file, lng, lat, sampleNo) {
  const exif = await readExifGps(file);
  if (exif && exif.lng && exif.lat) { lng = exif.lng; lat = exif.lat; }
  let name = buildPhotoName(sc, file.name, sampleNo);
  let out = file;
  try {
    out = await stampPhotoMeta(file, buildPhotoRemark(sc), lng, lat, sampleNo);
    name = name.replace(/\.[^.]+$/, '') + '.jpg'; // 重编码为 JPEG
  } catch (e) { /* 水印失败：按原图原扩展名保存 */ }
  await savePhotoToAlbum(out, name, photoSaveSubdir(sc));
  return { name, lng, lat };
}

// 将拍照文件以「分类_乡镇_村_小班_时间」前缀名保存到安卓相册
// Pictures/{年度}年度/{分类}/{调查小班号}号调查小班/。
// App 内优先走原生 MediaStore（subdir 多级子目录，返回真实路径）；浏览器回退 <a download>。
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1] || '');
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

async function savePhotoToAlbum(file, name, subdir) {
  if (isApp()) {
    const plugin = permPlugin();
    if (plugin && plugin.savePhoto) {
      try {
        const base64 = await fileToBase64(file);
        const r = await plugin.savePhoto({ base64, name, subdir: subdir || '验收照片' });
        const p = r && r.path;
        if (p) {
          const dir = p.slice(0, p.lastIndexOf('/') + 1);
          if (dir) {
            state._photoSaveDir = dir;
            state._photoSaveDirSubdir = subdir || '';
          }
          return;
        }
      } catch (e) { /* 原生失败回退下载 */ }
    }
  }
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
      const fix = _gpsFix(pos.coords);
      lng = fix.lng;
      lat = fix.lat;
    } catch (e) { /* 定位失败仍可保存照片 */ }
  }
  if (!state.scExtras) state.scExtras = { track: [], photos: [] };
  if (!state.scExtras.photos) state.scExtras.photos = [];
  const { name, lng: flng, lat: flat } = await stampAndSavePhoto(state.subcompartment, file, lng, lat);
  state.scExtras.photos.push({
    name,
    lng: flng, lat: flat,
    t: new Date().toISOString(),
    url: '',
  });
  await _scSavePhotos();
  toast('已保存到 ' + (state._photoSaveDir || photoSaveDirHint(state.subcompartment)), 2600);
  _renderScPanel();
}

// 网格调查页「照片」按钮：直接调用安卓相机拍照（不弹面板），拍完加水印按前缀命名保存
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
      const fix = _gpsFix(pos.coords);
      lng = fix.lng;
      lat = fix.lat;
    } catch (e) { /* 定位失败仍可保存照片 */ }
  }
  if (!state.scExtras) state.scExtras = { track: [], photos: [] };
  if (!state.scExtras.photos) state.scExtras.photos = [];
  const { name, lng: flng, lat: flat } = await stampAndSavePhoto(sc, file, lng, lat);
  state.scExtras.photos.push({ name, lng: flng, lat: flat, t: new Date().toISOString(), url: '' });
  await _scSavePhotos();
  toast('已保存到 ' + (state._photoSaveDir || photoSaveDirHint(sc)), 2600);
  input.remove();
}

function scPhotoRemove(idx) {
  if (!state.scExtras || !state.scExtras.photos) return;
  if (!confirm('确认删除该照片记录？')) return;
  state.scExtras.photos.splice(idx, 1);
  _scSavePhotos();
  _renderScPanel();
}

// 样地级拍照：水印含样地号，文件名记入 sample.photos（仅相册文件名，不上传）
async function samplePhotoFileChange(input) {
  const sk = input.dataset.sampleKey;
  const idx = Number(input.dataset.sampleIdx);
  const file = input.files && input.files[0];
  const arr = sk ? state.formData[sk] : null;
  if (!file || !Array.isArray(arr) || !arr[idx] || !state.subcompartment) { input.value = ''; return; }
  let lng = '', lat = '';
  if (navigator.geolocation) {
    try {
      const pos = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 });
      });
      const fix = _gpsFix(pos.coords);
      lng = fix.lng;
      lat = fix.lat;
    } catch (e) { /* 定位失败仍可保存照片 */ }
  }
  const sampleNo = arr[idx].no || (idx + 1);
  const { name } = await stampAndSavePhoto(state.subcompartment, file, lng, lat, sampleNo);
  if (!Array.isArray(arr[idx].photos)) arr[idx].photos = [];
  arr[idx].photos.push(name);
  toast(`样地${sampleNo}照片已保存到 ` + (state._photoSaveDir || photoSaveDirHint(state.subcompartment)), 2600);
  input.value = '';
  renderContent();
}

// ══ 样地管理弹窗（网格工具栏「样地」按钮；小班 ↔ 样地 双向切换）══
// 数据存 _gridSurveyMap[sc.id].samples（与网格保存同轨 PUT upsert）。
// 样地号自动递增不手填；照片仅记相册文件名（水印含样地号），不参与统计运算。
function smFieldDefs() {
  const tdef = getGridTableDef(state.gridTable);
  const f = tdef && (tdef.input_columns || []).find(x => x.type === 'sample_array');
  return (f && f.sample_fields) || [];
}

function smSamples() {
  const sc = state.gridSubcompartment;
  if (!sc) return null;
  const d = state._gridSurveyMap[sc.id] || (state._gridSurveyMap[sc.id] = {});
  if (!Array.isArray(d.samples)) d.samples = [];
  return d.samples;
}

// 进入样地管理页（独立视图，非弹窗）：工具栏「样地」按钮入口
async function openSamplesPage() {
  const sc = state.gridSubcompartment;
  if (!sc) { toast('请先选择小班'); return; }
  await loadGridSubcompartmentData();  // 确保最新数据
  if (!smSamples()) return;
  _smDirty = false;
  state.view = 'samples';
  renderApp();
  // 无坐标样地自动 GPS 补齐（有坐标的不动）
  setTimeout(smAutoFillCoords, 300);
}

// 样地管理页（页面级布局，卡片不压缩）
// 布局顺序：信息条 → 统计行（自动计算项转文本，紧凑不占空间）→
// 滚动区（样地列表 + 汇总表单在列表最下面）→「+添加样地」按钮（底部）。
// 可输入控件不占列表上方空间，避免样地卡片被遮挡。
function renderSamplesPage() {
  const sc = state.gridSubcompartment;
  if (!sc) {
    state.view = 'survey_grid';
    return renderSurveyGridPage();
  }
  return `<div class="page-samples">
    <div class="samples-bar" id="samplesBar">${renderSamplesBarInner()}</div>
    <div class="sm-stats-line" id="smStatsLine">${renderSamplesStatsInner()}</div>
    <div class="samples-scroll">
      <div class="samples-list" id="samplesList">${renderSamplesListInner()}</div>
      <div class="sm-summary" id="smSummary">${renderSamplesSummaryInner()}</div>
    </div>
    <button class="btn-sample-add sm-add-btn" data-action="sm-add">+ 添加样地</button>
  </div>`;
}

function bindSamplesPage() { /* 事件全部走全局委托 */ }

// 顶部信息条：小班信息 + 保存状态/按钮（统计已移至下方统计行）
function renderSamplesBarInner() {
  const sc = state.gridSubcompartment;
  const loc = [sc.township, sc.village].filter(Boolean).join(' ');
  return `
    <div class="samples-bar-info">
      <b>小班 ${escapeHtml(sc.subcompartment_label || '')}</b>
      ${loc ? `<small>${escapeHtml(loc)}</small>` : ''}
    </div>
    <div class="samples-bar-actions">
      <span class="sm-save-state ${_smDirty ? 'sm-unsaved' : 'sm-saved'}" id="smSaveState">${_smDirty ? '● 未保存' : '✓ 已保存'}</span>
      <button class="btn-grid-action btn-primary" data-action="sm-save">保存</button>
      <button class="btn-grid-action" data-action="sm-export" title="导出当前小班样地数据（Excel）">导出</button>
    </div>`;
}

function updateSamplesStat() {
  const bar = qs('#samplesBar');
  if (bar && state.view === 'samples') bar.innerHTML = renderSamplesBarInner();
  updateSmSummaryComputed();
}

// ── 样地页汇总区（对应样地模板 R27-R39 的 13 项）──
// 自动计算（不落库，导出由模板公式/代码计算）：
//   总样地面积 / 样地总株数 / 样地成活株数 / 样地成活率 / 调查总株数
// 手写录入（data_json.sm_* 键）：总样地个数 / 单个网格面积 / 种植网格数量 /
//   撑杆情况 / 覆膜情况 / 验收人 / 验收日期 / 备注
// 总样地个数默认自动填充实际样地数（仅空值/未手改时跟随，手写后以用户值为准）；
// 验收人默认当前用户、验收日期默认当天（仅填空值不覆盖已有数据，同打卡口径）
function smSummaryDefaults() {
  const sc = state.gridSubcompartment;
  const d = sc && state._gridSurveyMap[sc.id];
  if (!d) return;
  let touched = false;
  if (!d.sm_inspector) { d.sm_inspector = state.user || ''; touched = true; }
  if (!d.sm_inspect_date) {
    const t = new Date();
    const p2 = n => String(n).padStart(2, '0');
    d.sm_inspect_date = `${t.getFullYear()}-${p2(t.getMonth() + 1)}-${p2(t.getDate())}`;
    touched = true;
  }
  if (d.sm_total_count == null || d.sm_total_count === '') {
    d.sm_total_count = String((Array.isArray(d.samples) ? d.samples : []).length);
    touched = true;
  }
  if (touched && state.view === 'samples') smScheduleSave();
}

// 样地增删后同步「总样地个数」：用户未手改（空或等于旧自动值）时跟随实际个数
function smSyncTotalCount(oldLen) {
  const sc = state.gridSubcompartment;
  const d = sc && state._gridSurveyMap[sc.id];
  if (!d) return;
  const cur = d.sm_total_count;
  const newLen = (Array.isArray(d.samples) ? d.samples : []).length;
  if (cur == null || cur === '' || Number(cur) === oldLen) {
    d.sm_total_count = String(newLen);
    const el = qs('[data-sm-sum="sm_total_count"]');
    if (el) el.value = d.sm_total_count;
  }
}

// 汇总计算（除 0 守卫：成活率=成活/总株数，调查总株数=ROUND(总株数/个数/150*网格面积*网格数量)）
// 个数 n 用手写 sm_total_count（与模板 B27/B34 公式同口径），无手写值回退实际样地数
function smSummaryComputed() {
  const sc = state.gridSubcompartment;
  const d = (sc && state._gridSurveyMap[sc.id]) || {};
  const samples = Array.isArray(d.samples) ? d.samples : [];
  let realN = 0, area = 0, planted = 0, alive = 0;
  samples.forEach(s => {
    if (!s) return;
    realN++;
    area += Number(s.area) || 0;
    planted += Number(s.planted) || 0;
    alive += Number(s.alive) || 0;
  });
  const handN = Number(d.sm_total_count);
  const n = (handN > 0) ? handN : realN;
  const rate = planted > 0 ? alive / planted : null;
  const gArea = Number(d.sm_grid_area) || 0;
  const gCount = Number(d.sm_grid_count) || 0;
  const total = (n > 0 && planted > 0) ? Math.round(planted / n / 150 * gArea * gCount) : null;
  return { n, realN, area, planted, alive, rate, total };
}

// 统计行：自动计算项转纯文本（不可输入，紧凑展示在信息条下方，
// 与可输入的汇总表单分离，避免输入控件挤占样地列表空间）
function renderSamplesStatsInner() {
  const c = smSummaryComputed();
  const st = (label, val, key, suffix = '') =>
    `<span class="sm-stat-item">${label} <b data-sm-sum-val="${key}">${escapeHtml(String(val))}</b>${suffix}</span>`;
  return `
    <span class="sm-stat-item">共 <b data-sm-sum-val="realN">${c.realN}</b> 个样地</span>
    ${st('总样地面积', c.area, 'area', '㎡')}
    ${st('样地总株数', c.planted, 'planted')}
    ${st('样地成活株数', c.alive, 'alive')}
    ${st('样地成活率', c.rate == null ? '--' : (c.rate * 100).toFixed(2) + '%', 'rate')}
    ${st('调查总株数', c.total == null ? '--' : c.total, 'total')}`;
}

// 汇总表单（可输入项，放在样地列表最下面、随列表滚动）
function renderSamplesSummaryInner() {
  const sc = state.gridSubcompartment;
  if (!sc) return '';
  smSummaryDefaults();
  const d = state._gridSurveyMap[sc.id] || {};
  const inp = (label, key, type, attrs = '') =>
    `<div class="sm-sum-field"><label>${label}</label><input type="${type}" class="f-input" data-sm-sum="${key}" value="${escapeHtml(d[key] != null ? d[key] : '')}" ${attrs}></div>`;
  return `
    <div class="sm-sum-title">汇总信息</div>
    <div class="sm-sum-form">
      ${inp('总样地个数', 'sm_total_count', 'number', 'min="0" step="1" inputmode="numeric"')}
      ${inp('单个网格面积(㎡)', 'sm_grid_area', 'number', 'min="0" step="any" inputmode="decimal"')}
      ${inp('种植网格数量', 'sm_grid_count', 'number', 'min="0" step="1" inputmode="numeric"')}
      ${inp('撑杆情况', 'sm_pole', 'text')}
      ${inp('覆膜情况', 'sm_film', 'text')}
      ${inp('验收人', 'sm_inspector', 'text')}
      ${inp('验收日期', 'sm_inspect_date', 'date')}
      ${inp('备注', 'sm_remark', 'text')}
    </div>`;
}

// 汇总表单输入：写回 data_json.sm_* 并刷新联动计算（调查总株数依赖网格面积/数量）
function onSmSummaryInput(t) {
  const sc = state.gridSubcompartment;
  const d = sc && state._gridSurveyMap[sc.id];
  if (!d) return;
  let v = t.value;
  // 总样地个数/网格面积/数量不能为负数（同样地字段口径，即时去掉负号）
  if ((t.dataset.smSum === 'sm_total_count' || t.dataset.smSum === 'sm_grid_area'
       || t.dataset.smSum === 'sm_grid_count')
      && v !== '' && Number(v) < 0) {
    v = String(v).replace(/-/g, '');
    t.value = v;
    toast('填写数据不能为负数', 1800);
  }
  d[t.dataset.smSum] = v;
  updateSmSummaryComputed();
  smScheduleSave();
}

// 只刷新统计行 6 个计算值（不重渲染表单，避免输入焦点丢失）
function updateSmSummaryComputed() {
  if (!qs('#smStatsLine') || !state.gridSubcompartment) return;
  const c = smSummaryComputed();
  const set = (k, v) => { const el = qs(`[data-sm-sum-val="${k}"]`); if (el) el.textContent = v; };
  set('realN', c.realN);
  set('area', c.area);
  set('planted', c.planted);
  set('alive', c.alive);
  set('rate', c.rate == null ? '--' : (c.rate * 100).toFixed(2) + '%');
  set('total', c.total == null ? '--' : c.total);
}

// 死亡株数提示（种植−成活，只读 label，导出由模板 E 列公式计算）
function smUpdateDeathLabel(idx) {
  const s = (smSamples() || [])[idx];
  const el = qs(`[data-sm-death="${idx}"]`);
  if (!el || !s) return;
  const p = s.planted !== '' && s.planted != null ? Number(s.planted) : null;
  const a = s.alive !== '' && s.alive != null ? Number(s.alive) : null;
  el.textContent = (p != null && a != null) ? (p - a) : '--';
}

// 样地卡片渲染（倒序：最新样地显示在最上面；data-sm-idx 始终用原数组索引）
// 经纬度（x/y）不可编辑：不渲染输入框，GPS 按钮获取后在卡片头只读显示
// 删除按钮仅最后一个样地显示（防中间删除错位 + 防误触）
function renderSamplesListInner() {
  const samples = smSamples() || [];
  const fieldDefs = smFieldDefs().filter(sf => !sf.auto && sf.key !== 'x' && sf.key !== 'y');
  if (!samples.length) return '<div class="sample-empty">暂无样地，点击上方「+ 添加样地」开始录入</div>';
  return samples.map((s, i) => ({ s, i })).reverse().map(({ s, i }) => {
    const photos = Array.isArray(s.photos) ? s.photos.filter(Boolean) : [];
    const coordTxt = (s.x && s.y) ? `${s.x}, ${s.y}` : '未定位';
    const isLast = i === samples.length - 1;  // 数组最后一个（倒序显示在最底部）
    const fieldsHtml = fieldDefs.map(sf => {
      const v = s[sf.key] != null ? s[sf.key] : '';
      // 备注（text 类型）渲染文本框，其余数值框；顺序上备注在成活株数后、死亡株数提示前
      const isText = sf.type === 'text';
      const numAttrs = isText ? '' : ' step="any" inputmode="decimal" min="0"';
      return `<div class="field field-sm">
        <label>${escapeHtml(sf.label)}</label>
        <input type="${isText ? 'text' : 'number'}"${numAttrs} class="f-input" data-sm-idx="${i}" data-sm-field="${sf.key}" value="${escapeHtml(v)}">
      </div>`;
    }).join('');
    // 死亡株数提示（种植−成活，只读；导出由模板 E 列公式计算）
    const p = s.planted !== '' && s.planted != null ? Number(s.planted) : null;
    const a = s.alive !== '' && s.alive != null ? Number(s.alive) : null;
    const deathTxt = (p != null && a != null) ? (p - a) : '--';
    return `<div class="sample-card">
      <div class="sample-card-head">
        <span class="sample-card-title">样地 ${s.no || i + 1}</span>
        <span class="sample-coord" data-sm-coord="${i}">📍 ${escapeHtml(coordTxt)}</span>
        <div class="sample-card-actions">
          <button class="btn-gps" data-action="sm-gps" data-sm-idx="${i}" title="一键获取该样地坐标">📍坐标</button>
          <label class="btn-photo" title="拍照（水印含样地号，照片不参与运算）">📷拍照${photos.length ? `(${photos.length})` : ''}
            <input type="file" accept="image/*" capture="environment" class="f-sm-photo" data-sm-idx="${i}" hidden>
          </label>
          ${isLast ? `<button class="btn-sample-del" data-action="sm-del" data-sm-idx="${i}" title="删除最后一个样地（需先清空面积或种植株数）">✕</button>` : ''}
        </div>
      </div>
      <div class="sample-card-body">${fieldsHtml}
        <div class="sample-death">死亡株数：<b data-sm-death="${i}">${deathTxt}</b><small>（种植−成活，自动）</small></div>
      </div>
      ${photos.length ? `<div class="sample-photo-names" title="${escapeHtml(photos.join('；'))}">📷 ${escapeHtml(photos.join('；'))}</div>` : ''}
    </div>`;
  }).join('');
}

// 新建样地前校验：已有样地必须填全 面积/种植/成活/坐标（GPS 获取），且种植 ≥ 成活
function smValidateBeforeAdd() {
  const samples = smSamples() || [];
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i] || {};
    const miss = [];
    if (s.area == null || s.area === '') miss.push('样地面积');
    if (s.planted == null || s.planted === '') miss.push('种植株数');
    if (s.alive == null || s.alive === '') miss.push('成活株数');
    if (s.x == null || s.x === '' || s.y == null || s.y === '') miss.push('坐标（点📍获取）');
    if (miss.length) {
      toast(`样地${s.no || i + 1} 未填写：${miss.join('、')}，请补全后再添加新样地`, 3000);
      // 聚焦第一个缺失的输入字段（坐标无输入框，跳过聚焦）
      const focusMap = { '样地面积': 'area', '种植株数': 'planted', '成活株数': 'alive' };
      const fk = focusMap[miss[0]];
      if (fk) {
        const el = qs(`[data-sm-field="${fk}"][data-sm-idx="${i}"]`);
        if (el) { el.focus(); el.classList.add('input-error'); setTimeout(() => el.classList.remove('input-error'), 2000); }
      }
      return false;
    }
    // 种植株数必须 ≥ 成活株数
    if (!smRowRuleOk(s)) {
      toast(`样地${s.no || i + 1}：种植株数不能小于成活株数，请修正后再添加新样地`, 3000);
      ['planted', 'alive'].forEach(k => {
        const el = qs(`[data-sm-field="${k}"][data-sm-idx="${i}"]`);
        if (el) el.classList.add('input-error');
      });
      const pe = qs(`[data-sm-field="planted"][data-sm-idx="${i}"]`);
      if (pe) pe.focus();
      return false;
    }
  }
  return true;
}

// 保存时机：① 输入后 1.2s 自动落库 ② 光标离开输入框（blur）立即保存 ③ 手动保存按钮
// 状态指示：● 未保存 / ⏳ 保存中… / ✓ 已保存 / ✗ 保存失败（自动重试）
let _smDirty = false;
let _smSaveTimer = null;
let _smSaving = false;

function smSetState(txt, cls) {
  const st = qs('#smSaveState');
  if (st) { st.textContent = txt; st.className = 'sm-save-state' + (cls ? ' ' + cls : ''); }
}

function smScheduleSave() {
  _smDirty = true;
  smSetState('● 未保存', 'sm-unsaved');
  if (_smSaveTimer) clearTimeout(_smSaveTimer);
  _smSaveTimer = setTimeout(() => {
    _smSaveTimer = null;
    if (_smDirty) smSave(false);
  }, 1200);
}

// 光标离开输入框立即保存（切换到下一字段时即落库）
function smSaveOnBlur() {
  if (!_smDirty) return;
  if (_smSaveTimer) { clearTimeout(_smSaveTimer); _smSaveTimer = null; }
  smSave(false);
}

async function smSave(manual) {
  if (_smSaving) return;  // 防并发重复提交
  // 手动保存：强校验种植 ≥ 成活（自动保存不拦，避免输入中间态误报失败）
  if (manual) {
    const bad = smFirstRuleBad();
    if (bad >= 0) {
      const samples = smSamples() || [];
      const s = samples[bad] || {};
      toast(`样地${s.no || bad + 1}：种植株数不能小于成活株数，请修正后再保存`, 3000);
      smMarkRowErrors();
      const pe = qs(`[data-sm-field="planted"][data-sm-idx="${bad}"]`);
      if (pe) pe.focus();
      return;
    }
  }
  _smSaving = true;
  smSetState('⏳ 保存中…', 'sm-saving');
  const ok = await saveSamplesNow(true, 2);  // 网络抖动自动重试 2 次
  _smSaving = false;
  if (ok) {
    _smDirty = false;
    smSetState('✓ 已保存', 'sm-saved');
    toast(manual ? '样地数据已保存' : '样地数据已自动保存');
  } else {
    smSetState('✗ 保存失败 点击保存重试', 'sm-failed');
  }
}

async function saveSamplesNow(silent, retries) {
  const sc = state.gridSubcompartment;
  if (!sc || !state.project) return false;
  const d = state._gridSurveyMap[sc.id] || {};
  // 重算 computed 统计字段（与网格保存同口径）
  recomputeComputedFields(d, state.gridTable);
  d.inspector = d.inspector || state.user || '';
  const tid = state.gridTable;
  // 走统一封装（401 跳登录）；Failed to fetch（弱网/断网）自动重试；
  // 409 乐观锁冲突不重试——弹窗由用户决策（合并/覆盖/加载最新）
  const tries = (retries || 0) + 1;
  let lastErr = null;
  for (let i = 0; i < tries; i++) {
    try {
      const r = await putSurveyRow(tid, sc.id, d, d.inspector, gridRowVersion(sc.id));
      if (r.ok) {
        markGridRowSaved(sc.id, r.rec, d);
        return true;
      }
      // 冲突：弹窗（保存状态由弹窗回调接管——成功置 ✓，取消保持 ✗ 可重试）
      showRowConflictDialog({
        tid, scId: sc.id, myData: d, conflict: r.conflict,
        saveFn: (data, bv) => putSurveyRow(tid, sc.id, recomputeComputedFields(data, tid), d.inspector, bv),
        onSaved: (rec, saved) => {
          state._gridSurveyMap[sc.id] = saved;
          markGridRowSaved(sc.id, rec, saved);
          _smDirty = false;
          smSetState('✓ 已保存', 'sm-saved');
          renderApp();
        },
        onLoaded: (data, ver) => {
          state._gridSurveyMap[sc.id] = data;
          state._gridVerMap[sc.id] = ver;
          state._gridBaseMap[sc.id] = JSON.parse(JSON.stringify(data));
          _smDirty = false;
          renderApp();
        },
      });
      return false;
    } catch (e) {
      lastErr = e;
      if (String(e.message || '').includes('未登录')) return false;  // 跳登录场景不再重试
      if (i < tries - 1) await new Promise(res => setTimeout(res, 1200));
    }
  }
  if (!silent) toast('保存失败：' + (lastErr ? lastErr.message : '未知错误') + '（请检查网络后重试）', 3000);
  return false;
}

// 返回小班前兜底保存未落库的输入
async function smLeaveToGrid() {
  if (_smSaveTimer) { clearTimeout(_smSaveTimer); _smSaveTimer = null; }
  if (_smDirty) await smSave(false);
  state.view = 'survey_grid';
  renderApp();
  await renderSurveyForm();  // 刷新网格统计显示
}

// ── 样地行规则校验：种植株数 ≥ 成活株数（两者都有值时才判） ──
function smRowRuleOk(s) {
  if (!s) return true;
  const p = (s.planted == null || s.planted === '') ? null : Number(s.planted);
  const a = (s.alive == null || s.alive === '') ? null : Number(s.alive);
  if (p != null && !isNaN(p) && a != null && !isNaN(a) && p < a) return false;
  return true;
}

// 找到第一个违反「种植 ≥ 成活」的样地（返回索引，无则 -1）
function smFirstRuleBad() {
  const samples = smSamples() || [];
  for (let i = 0; i < samples.length; i++) {
    if (!smRowRuleOk(samples[i])) return i;
  }
  return -1;
}

// 红框标记所有违反规则的样地输入框（输入时实时刷新）
function smMarkRowErrors() {
  const samples = smSamples() || [];
  samples.forEach((s, i) => {
    const bad = !smRowRuleOk(s);
    ['planted', 'alive'].forEach(k => {
      const el = qs(`[data-sm-field="${k}"][data-sm-idx="${i}"]`);
      if (el) el.classList.toggle('input-error', bad);
    });
  });
}

// 样地页导出：仅导出当前小班（当前分类，sheet 名「分类-调查小班号」）
async function exportSamples() {
  if (!state.project) { toast('请先选择项目'); return; }
  const sc = state.gridSubcompartment;
  if (!sc) { toast('请先选择小班'); return; }
  toast('正在导出样地数据…', 1500);
  try {
    const res = await fetch(`api/projects/${state.project.id}/export_samples?sc=${sc.id}`);
    if (!res.ok) {
      const e = await res.json().catch(() => ({}));
      throw new Error(e.error || `导出失败 (${res.status})`);
    }
    const blob = await res.blob();
    const filename = `${state.project.name}_${sc.category || ''}_${sc.subcompartment || sc.subcompartment_label || ''}_样地.xlsx`;
    const nativeSaved = await downloadExportFile(blob, filename);
    if (!nativeSaved) toast('当前小班样地数据已导出');
  } catch (e) {
    toast('样地导出失败：' + e.message, 2500);
  }
}

// 样地 GPS：手动点击时若该样地已有坐标，需确认后才覆盖（默认否，防误触）；
// auto=true 为自动补坐标模式（无坐标样地静默获取，无确认无成功提示）
function smGetGPS(idx, auto) {
  if (!navigator.geolocation) { if (!auto) toast('设备不支持定位'); return; }
  if (!auto) {
    const cur = (smSamples() || [])[idx];
    if (cur && cur.x != null && cur.x !== '' && cur.y != null && cur.y !== '') {
      smConfirmOverwriteCoords().then(ok => { if (ok) smGetGPS(idx, true); });
      return;
    }
    toast('正在获取定位…', 1500);
  }
  navigator.geolocation.getCurrentPosition(pos => {
    const samples = smSamples();
    if (!samples || !samples[idx]) { if (!auto) toast('定位成功但样地已失效，请重试'); return; }
    const fix = _gpsFix(pos.coords);
    samples[idx].x = fix.lng;
    samples[idx].y = fix.lat;
    // 只读坐标显示更新（无输入框）
    const el = qs(`[data-sm-coord="${idx}"]`);
    if (el) el.textContent = `📍 ${samples[idx].x}, ${samples[idx].y}`;
    if (!auto) toast(fix.adj ? '定位成功（网络定位已纠偏）' : '定位成功');
    smScheduleSave();
  }, err => {
    if (!auto) handleGeoError(err);
    else toast('自动定位失败，可点「📍坐标」手动获取', 2500);
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
}

// 覆盖坐标确认弹窗（默认否）：自定义弹窗而非原生 confirm——
// 原生 confirm 回车默认"确定"，会误触覆盖已录坐标；此处「否」为主按钮
function smConfirmOverwriteCoords() {
  return new Promise(resolve => {
    const root = qs('#modalRoot');
    root.innerHTML = `
      <div class="modal-mask" data-action="close-modal-mask">
        <div class="modal">
          <h3>覆盖样地坐标？</h3>
          <div class="sm-confirm-tip">该样地已有坐标，是否用当前定位覆盖？</div>
          <div class="modal-actions">
            <button class="btn-confirm" data-cf="no">否（保留原坐标）</button>
            <button class="btn-cancel" data-cf="yes">是（覆盖）</button>
          </div>
        </div>
      </div>`;
    const done = v => { root.innerHTML = ''; resolve(v); };
    root.querySelectorAll('[data-cf]').forEach(b => b.addEventListener('click', ev => {
      ev.stopPropagation();
      done(b.dataset.cf === 'yes');
    }));
    // 点遮罩 = 不覆盖（与默认一致）
    root.querySelector('.modal-mask').addEventListener('click', ev => {
      if (ev.target === ev.currentTarget) done(false);
    });
  });
}

// 自动补齐无坐标样地：进入样地页/新增样地时调用，静默 GPS 获取填入
// （有坐标的样地不动；未获取到则留待手动点「📍坐标」）
function smAutoFillCoords() {
  const samples = smSamples();
  if (!samples || !samples.length || !navigator.geolocation) return;
  const empties = [];
  samples.forEach((s, i) => {
    if (!s || s.x == null || s.x === '' || s.y == null || s.y === '') empties.push(i);
  });
  if (!empties.length) return;
  toast(`正在自动定位 ${empties.length} 个未定位样地…`, 2000);
  empties.forEach(i => smGetGPS(i, true));
}

// 样地页拍照：优先用该样地已有坐标，回退当前定位；拍完即时落库并显示累计张数
async function smPhotoFileChange(input) {
  const idx = Number(input.dataset.smIdx);
  const file = input.files && input.files[0];
  const samples = smSamples();
  const sc = state.gridSubcompartment;  // grid 流程：小班对象来自 gridSubcompartment
  if (!file || !samples || !samples[idx] || !sc) { input.value = ''; return; }
  let lng = samples[idx].x || '', lat = samples[idx].y || '';
  if ((!lng || !lat) && navigator.geolocation) {
    try {
      const pos = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 });
      });
      const fix = _gpsFix(pos.coords);
      lng = fix.lng;
      lat = fix.lat;
    } catch (e) { /* 定位失败仍可保存照片 */ }
  }
  const sampleNo = samples[idx].no || (idx + 1);
  const { name } = await stampAndSavePhoto(sc, file, lng, lat, sampleNo);
  if (!Array.isArray(samples[idx].photos)) samples[idx].photos = [];
  samples[idx].photos.push(name);
  await saveSamplesNow(true, 1);  // 照片记录即时落库，防丢失
  toast(`样地${sampleNo}已拍 ${samples[idx].photos.length} 张，保存在 ` + (state._photoSaveDir || photoSaveDirHint(sc)), 2600);
  input.value = '';
  updateSamplesStat();
  // 仅更新该卡片拍照按钮张数，避免重渲染打断其他输入
  const btn = input.closest('.btn-photo');
  if (btn) btn.firstChild.textContent = `📷拍照${samples[idx].photos.length ? `(${samples[idx].photos.length})` : ''}`;
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
      const obj = newSampleObject(fdef.sample_fields || []);
      obj.no = state.formData[sk].length + 1;  // 样地号自动递增
      state.formData[sk].push(obj);
      renderContent();
      break;
    }
    case 'sample-del': {
      const sk = t.dataset.sampleKey;
      const idx = Number(t.dataset.sampleIdx);
      if (Array.isArray(state.formData[sk])) {
        state.formData[sk].splice(idx, 1);
        // 重排序号，保持连续
        state.formData[sk].forEach((s, i) => { if (s && typeof s === 'object') s.no = i + 1; });
        renderContent();
      }
      break;
    }
    // ── 样地管理页 ──
    case 'sc-samples':
      await openSamplesPage();
      break;
    case 'go-grid':
      await smLeaveToGrid();
      break;
    case 'sm-add': {
      const samples = smSamples();
      if (!samples) break;
      // 新建前校验：前面样地数据必须完整（面积/种植/成活）
      if (!smValidateBeforeAdd()) break;
      // 兜底保存未落库的输入
      if (_smSaveTimer) { clearTimeout(_smSaveTimer); _smSaveTimer = null; }
      if (_smDirty) await saveSamplesNow(true, 1);
      const obj = newSampleObject(smFieldDefs());
      obj.no = samples.length + 1;  // 样地号自动递增，不手填
      const prevLen = samples.length;
      samples.push(obj);
      smSyncTotalCount(prevLen);
      // 仅重渲染列表与信息条（倒序：新样地在最顶部）
      const list = qs('#samplesList');
      if (list) list.innerHTML = renderSamplesListInner();
      updateSamplesStat();
      await saveSamplesNow(true, 1);
      // 新样地无坐标：自动 GPS 定位填入（前面样地已校验必完整，不受影响）
      setTimeout(smAutoFillCoords, 200);
      // 聚焦新样地（列表第一个卡片）的面积输入
      const firstCard = qs('.sample-card');
      if (firstCard) {
        const firstInput = firstCard.querySelector('[data-sm-field="area"]');
        if (firstInput) setTimeout(() => firstInput.focus(), 150);
      }
      break;
    }
    case 'sm-del': {
      const samples = smSamples();
      if (!samples) break;
      const idx = Number(t.dataset.smIdx);
      // 仅最后一个样地可删（防中间删除导致样地号/数据错位）
      if (idx !== samples.length - 1) { toast('只能删除最后一个样地', 2200); break; }
      // 防误触：最后一个样地数据完整（面积和种植都有值）时不可删，
      // 需先把面积或种植株数清空/置 0 才能删除
      const s = samples[idx] || {};
      const area = Number(s.area) || 0;
      const planted = Number(s.planted) || 0;
      if (area > 0 && planted > 0) {
        toast('为防误删：请先将该样地的面积或种植株数清空（或置 0），再点删除', 3000);
        break;
      }
      const prevLen = samples.length;
      samples.pop();
      smSyncTotalCount(prevLen);
      samples.forEach((x, i) => { if (x && typeof x === 'object') x.no = i + 1; });
      const list = qs('#samplesList');
      if (list) list.innerHTML = renderSamplesListInner();
      updateSamplesStat();
      await saveSamplesNow(true, 1);
      break;
    }
    case 'sm-gps':
      smGetGPS(Number(t.dataset.smIdx));
      break;
    case 'sm-save':
      if (_smSaveTimer) { clearTimeout(_smSaveTimer); _smSaveTimer = null; }
      await smSave(true);
      updateSamplesStat();
      break;
    case 'sm-export':
      // 导出前兜底保存未落库的输入，确保导出数据最新
      if (_smSaveTimer) { clearTimeout(_smSaveTimer); _smSaveTimer = null; }
      if (_smDirty) await smSave(false);
      await exportSamples();
      break;
    case 'save-survey':
      await saveSurvey();
      break;
    case 'export-base':
      await exportBase();
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
  // 网格工具栏调查小班号搜索：正整数匹配 → 自动选中该小班
  if (t.id === 'gridScSearch') {
    onGridScSearchInput(t.value);
    return;
  }
  // 样地页汇总表单输入：data-sm-sum 定位（网格面积/数量/撑杆/覆膜/验收人/日期/备注）
  if (t.dataset && t.dataset.smSum !== undefined) {
    onSmSummaryInput(t);
    return;
  }
  // 样地字段输入：data-sample-field 定位；同时刷新样地统计（computed）
  if (t.dataset && t.dataset.sampleField) {
    const sk = t.dataset.sampleKey;
    const idx = Number(t.dataset.sampleIdx);
    const fk = t.dataset.sampleField;
    const arr = state.formData[sk];
    if (Array.isArray(arr) && arr[idx]) {
      arr[idx][fk] = t.value;
      refreshComputedDisplays();
    }
    return;
  }
  // 样地页字段输入：data-sm-field 定位；负数硬拦截 + 规则红框 + 实时统计 + debounce 自动保存
  if (t.dataset && t.dataset.smField !== undefined && t.dataset.smIdx !== undefined) {
    const samples = smSamples();
    const idx = Number(t.dataset.smIdx);
    if (samples && samples[idx]) {
      let v = t.value;
      // 不能为负数：即时去掉负号（min=0 已挡步进器，这里挡手动键入）；
      // 仅数值输入框拦截——备注（type=text）等文本字段允许任意字符
      if (t.type === 'number' && v !== '' && Number(v) < 0) {
        v = String(v).replace(/-/g, '');
        t.value = v;
        toast('填写数据不能为负数', 1800);
      }
      samples[idx][t.dataset.smField] = v;
      // 种植 ≥ 成活：红框实时标记（强拦截在 添加样地/手动保存 时）
      smMarkRowErrors();
      smUpdateDeathLabel(idx);   // 死亡株数提示实时刷新
      updateSamplesStat();
      smScheduleSave();
    }
    return;
  }
  if (t.dataset && t.dataset.field) {
    let v = t.value;
    // 率类：输入 95（见 95%），落库存比率 0.95（不 ×100）
    const pf = (activeInputColumns() || []).find(x => x.key === t.dataset.field);
    if (pf && pf.type === 'percent') v = pctToStore(v);
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
  // 网格工具栏小班搜索回车立即匹配
  if (e.target.id === 'gridScSearch' && e.key === 'Enter') {
    e.preventDefault();
    if (_gridScSearchTimer) clearTimeout(_gridScSearchTimer);
    onGridScSearchInput(e.target.value);
  }
});

// 网格工具栏调查小班号搜索：输入正整数时在当前筛选结果内找该调查小班号，
// 命中则把小班 select 值设为该小班并派发 change（复用手动选中的完整流程：
// 停轨迹/载数据/预载扩展/刷工具栏/渲染网格）。无匹配则不动。
let _gridScSearchTimer = null;
function onGridScSearchInput(val) {
  if (_gridScSearchTimer) clearTimeout(_gridScSearchTimer);
  _gridScSearchTimer = setTimeout(() => {
    const q = (val || '').trim();
    if (!/^[1-9]\d*$/.test(q)) return;  // 仅正整数触发匹配
    const hit = getGridFilteredRows().find(r => String(r.subcompartment) === q);
    if (!hit) { toast(`当前筛选内无调查小班号 ${q}`, 1800); return; }
    const sel = qs('#gridSubcompartmentFilter');
    if (!sel || sel.value === hit.id) return;
    sel.value = hit.id;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }, 250);
}

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
// 光标离开样地输入框（切换到下一字段）：违反「种植 ≥ 成活」即时提示 + 立即保存
app.addEventListener('focusout', (e) => {
  const t = e.target;
  if (t && t.dataset && t.dataset.smField !== undefined && t.dataset.smIdx !== undefined) {
    const samples = smSamples();
    const idx = Number(t.dataset.smIdx);
    if (samples && samples[idx] && !smRowRuleOk(samples[idx])) {
      const s = samples[idx];
      toast(`样地${s.no || idx + 1}：种植株数不能小于成活株数`, 2500);
      t.classList.add('input-error');
    }
    smSaveOnBlur();
  }
});

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
  if (t.classList && t.classList.contains('f-sample-photo')) {
    samplePhotoFileChange(t);
    return;
  }
  if (t.classList && t.classList.contains('f-sm-photo')) {
    smPhotoFileChange(t);
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
    // value 为小班行主键 id（同号小班不唯一，必须按 id 定位）
    const scId = t.value;
    const nextSc = scId ? ((state.gridScRows || []).find(r => r.id === scId) || null) : null;
    // 正在记录轨迹时切换小班：先停止并保存到原小班
    if (_scWatchId !== null && _scTrackScId && nextSc && _scTrackScId !== nextSc.id) {
      await stopTrackRecording();
      toast('已自动保存并停止上一小班轨迹', 2200);
    }
    state.gridSubcompartment = nextSc;
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
  if (t.id === 'gridCheckinFilter') {
    state.gridCheckin = t.value;
    state.gridSubcompartment = null;    // 级联：清空已选小班
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
// 退后台/关闭页面前兜底保存轨迹（Android WebView 切后台即可能被回收）
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden' && _scWatchId !== null) {
    // 静默保存，不停止记录（回前台继续）
    if (_scTrackScId && _scTrackRef && _scTrackRef.length) _postTrack(_scTrackScId, _scTrackRef, true);
  }
});
window.addEventListener('pagehide', () => {
  if (_scWatchId !== null && _scTrackScId && _scTrackRef && _scTrackRef.length) {
    // pagehide 中 async fetch 可能被截断，用同步 sendBeacon 兜底
    try {
      const blob = new Blob([JSON.stringify({ points: _scTrackRef })], { type: 'application/json' });
      navigator.sendBeacon(`api/subcompartments/rows/${_scTrackScId}/track`, blob);
    } catch (e) { /* 忽略 */ }
  }
});

init();
