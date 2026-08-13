# 设计文档：Android 打包 + Excel 网格填表 + 原生能力扩展

> 范围：在现有「林业野外调查即时录入系统」（Flask + 原生 JS SPA + SQLite）基础上，
> ① 把五表录入从「动态表单」改造为「Excel 网格」操作体验；
> ② 精简界面，只保留五表填写主流程；
> ③ 打包为 Android App，并集成相机、GPS 轨迹、地图打点、手写签名等原生能力。
>
> 关键决策：**Capacitor 套壳**（现有前端 0 改动）+ **jspreadsheet-ce**（MIT，jexcel CE 现行名）网格填表。
> 后端 Flask + SQLite + 导出逻辑零改动。

---

## 一、背景与目标

### 1.1 现状

| 项 | 现状 | 代码位置 |
|----|------|----------|
| 后端 | Flask 单文件，gateway 聚合部署，端口 8090 | `survey/web/app.py` |
| 前端 | 原生 JS 单页应用，schema 驱动动态表单，离线优先 | `survey/web/static/app.js` |
| Schema | 五张验收表唯一真相源，字段类型化 | `survey/schema.py` |
| 存储 | SQLite `survey.db`，含小班批次/行/扩展数据 | `survey/storage.py` |
| 导出 | 按 schema col 映射写 xlsx | `survey/exporter.py` |
| 导航 | 三级：上传列表 → 小班列表 → 调查填写 | `app.js` view 状态机 |
| 扩展 | 打卡/轨迹/照片（subcompartment_extras） | `app.py:724-766` |

### 1.2 目标

1. **Excel 网格填表**：五表录入改为类 Excel 的网格直接编辑，支持键盘导航、复制粘贴、下拉/日期/数字单元格。
2. **界面精简**：App 端只保留「选小班 → 填五表」主流程，隐藏管理面板等非外业功能。
3. **Android 打包**：复用现有 Web 代码出 APK，B/S 架构（App 访问服务器 API）。
4. **原生能力**：相机拍照、后台 GPS 轨迹、地图打点显示、手写签名。

### 1.3 非目标

- 不重写后端，不改数据库表结构（仅可能新增字段）。
- 不做 iOS（先 Android）。
- 不做微信小程序（远期再评估 uni-app 重构）。

---

## 二、需求清单

### 2.1 原生能力需求（用户新增）

| # | 能力 | 说明 | 现有基础 |
|---|------|------|----------|
| R1 | 相机拍照 | 现场拍照，带经纬度水印 | 已有 `photo` 字段，Web 用 `getUserMedia` |
| R2 | 后台 GPS 轨迹 | 锁屏/后台持续记录轨迹点 | 已有 `track` 字段，Web 用 `navigator.geolocation`（仅前台） |
| R3 | 地图打点显示 | 地图上显示打卡点/轨迹/小班位置 | 无，需新增 |
| R4 | 手写签名 | 在表格内手写签名并存档 | 无，需新增 |
| R5 | 界面精简 | App 只显示五表填写 | 现有三级导航保留，App 入口直跳填写页 |

### 2.2 Excel 网格填表需求

| # | 需求 | 说明 |
|---|------|------|
| E1 | 网格直接编辑 | 点单元格即编辑，Tab/Enter/方向键导航 |
| E2 | 类型化单元格 | enum→下拉、number/percent→数字、date→日期、text→文本 |
| E3 | 特殊单元格 | gps/photo/checkin/track/签名→按钮单元格，点击弹窗 |
| E4 | 字段分组表头 | 按 `field_groups` 做列分组表头 |
| E5 | 多行录入 | `data_rows` 行网格，可增删行 |
| E6 | 预填列只读 | `prefilled_columns`（黄色列）只读，由小班信息映射 |
| E7 | 复制粘贴 | 支持 Excel ↔ 网格互粘 |
| E8 | 离线暂存 | 沿用现有离线优先，断网可继续编辑 |

---

## 三、技术选型评估

### 3.1 打包方案能力矩阵

| 能力 | Capacitor 套壳 | uni-app 重构 | HBuilderX 5+ | 评估依据 |
|------|---------------|--------------|--------------|----------|
| 现有代码复用 | ★★★★★ 0 改动 | ★☆☆☆☆ 重写为 Vue | ★★★★☆ 近 0 改动 | 前端为原生 JS SPA |
| 相机 (R1) | ★★★★★ @capacitor/camera | ★★★★★ uni.chooseImage | ★★★★☆ plus.camera | 三者都成熟 |
| **后台 GPS 轨迹 (R2)** | ★★★★☆ **开源插件** | ★★☆☆☆ **付费插件+高德授权费** | ★★☆☆☆ 仅前台 | 见下 |
| 地图打点 (R3) | ★★★★☆ 高德 JS SDK in WebView | ★★★★★ 原生 `<map>` 组件 | ★★★☆☆ WebView JS SDK | uni-app 略顺 |
| 手写签名 (R4) | ★★★★★ signature_pad 纯前端 | ★★★★★ signature_pad 纯前端 | ★★★★★ signature_pad 纯前端 | 与打包无关 |
| 多端覆盖 | App + H5 | App+H5+小程序 | App + H5 | — |
| 维护成本 | 低 | 中（Vue 学习+重写） | 低 | — |

**后台 GPS 轨迹（最硬的骨头）对比**：

- **Capacitor**：开源插件 `@gachlab/capacitor-background-geolocation`（Capacitor 8+，2026 年活跃），原生前台服务、`FOREGROUND_SERVICE_LOCATION`（Android 14+ 必需）、两步权限申请、OEM 电池杀手检测、离线缓冲、心跳。MIT/Apache，无额外授权费。
- **uni-app**：`uni.getLocation` 后台会被暂停；必须用 DCloud 插件市场**付费原生插件**（AMap-LocTrack 399 元 / Sly-UTSAMapLocSearch 99 元）+ 高德商用授权费（高德近年频繁向开发者收授权费）。成本与合规风险高。

### 3.2 选型结论

**打包：Capacitor 套壳**。

- 原因：现有前端 0 改动即可出 APK；后台轨迹有开源方案，避开了 uni-app 的付费插件+高德授权费；地图用高德 JS SDK 在 WebView 可行（林业外业打点场景性能足够）。
- 代价：地图体验略逊 uni-app 原生组件，但可接受；若后续必须做小程序，再评估 uni-app 重构。

**填表：jspreadsheet-ce**（jexcel v4 的现行 MIT 继任者）。

- 原因：`jexcel` npm 包已 deprecated，现名 `jspreadsheet-ce`，MIT 协议，CE 版功能（column types、自定义编辑器、复制粘贴、行列增删）满足本场景；30KB 级轻量，与原生 JS 架构契合。
- 避免：`jspreadsheet` Pro v5+（商业收费）、Handsontable（商用收费）、Luckysheet/Univer（功能全但体积几 MB、封装适配 schema 成本高）。

> 选型代码原则：**渲染层可替换，schema 与后端契约不变**。jspreadsheet-ce 只替换 `app.js` 的表单渲染部分，`schema.py` / API / 导出零改动。

---

## 四、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  Android App（Capacitor 套壳）                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  WebView（现有 Web 前端 0 改动）                        │  │
│  │   ├─ 三级导航（上传列表→小班列表→调查填写）              │  │
│  │   ├─ 五表录入：jspreadsheet-ce 网格（替换动态表单）      │  │
│  │   ├─ 地图打点：高德 JS SDK（嵌入式）                    │  │
│  │   └─ 手写签名：signature_pad（jspreadsheet 自定义单元格）│  │
│  ├───────────────────────────────────────────────────────┤  │
│  │  Capacitor 原生桥接层（新增）                           │  │
│  │   ├─ @capacitor/camera      → 相机拍照                 │  │
│  │   ├─ background-geolocation → 后台 GPS 轨迹            │  │
│  │   └─ @capacitor/preferences → 离线缓存补充             │  │
│  └───────────────────────────────────────────────────────┘  │
│                          │ HTTP (B/S)                          │
└──────────────────────────┼──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  服务器（现有，零改动）                                        │
│   Flask + SQLite + 导出  ← /api/...  （端口 8090 / gateway）  │
└─────────────────────────────────────────────────────────────┘
```

关键视角：**App 只是带原生能力的浏览器壳**，业务逻辑仍在服务器。离线时 App 用 localStorage 暂存（沿用现有 `state.formData` 机制），联网后回写。

---

## 五、详细设计

### 5.1 Excel 网格填表（jspreadsheet-ce 集成）

#### 5.1.1 原理

现有 `app.js` 按 `schema.input_columns` 渲染 `<input>` 表单（分组折叠面板）。
改造为：按同一份 schema 生成 jspreadsheet-ce 的 `columns` 配置，把网格挂载到填写页容器。schema 仍是唯一真相源。

#### 5.1.2 字段类型映射

| schema type | jspreadsheet-ce column.type | 备注 |
|-------------|----------------------------|------|
| `text` | `text` | 单行 |
| `textarea` | `text` + 自定义编辑器（多行弹窗） | 长文本 |
| `number` | `numeric` | `unit` 显示在表头 |
| `percent` | `numeric` + `mask:'0%'` | 0-100 |
| `date` | `calendar` | 日期选择器 |
| `enum` | `dropdown` + `source:options` | 单选下拉 |
| `checkbox` | `checkbox` | 有/无 |
| `gps` | **自定义按钮单元格** | 点击取 GPS |
| `photo` | **自定义按钮单元格** | 点击调相机 |
| `checkin` | **自定义按钮单元格** | 点击打卡 |
| `track` | **自定义按钮单元格** | 点击启停轨迹 |
| 签名（新增） | **自定义按钮单元格** | 点击弹签名板 |

#### 5.1.3 网格结构（以表1为例）

```
列布局：[预填只读列(黄)] [输入列按 group 分组] [ inspector | inspect_time | remark ]
        ├─ A~M: city/county/.../reported_area  ← prefilled_columns，readOnly
        ├─ 成活率组: survival_pass/replant/fail
        ├─ 核实面积组: verified_total/pass/replant/fail/loss
        ├─ 原因组: ...
        ├─ 管护抚育组: 4 对 面积+率
        └─ 收尾: inspector/inspect_time/remark
行：data_rows=5 预留行 + 可「+」增行
```

- 预填列：从 `subcompartment_data.prefilled` 自动回填，`readOnly:true`，背景色 `#FFF7CC`（黄）。
- 分组表头：jspreadsheet-ce 支持嵌套表头 `nestedHeaders`，按 `field_groups` 生成。
- 行与记录：每行 = 一条 record，`row_index` 对应网格行号；编辑 `onchange` 时调 `POST /api/projects/<pid>/records`（沿用现有 API）。

#### 5.1.4 自定义按钮单元格

jspreadsheet-ce 自定义列通过 `render`/自定义编辑器实现。按钮单元格渲染为小按钮，点击触发对应弹层：

```js
// 伪代码：自定义「photo」列
function photoCellRenderer(instance, cell, col, row, value) {
  cell.innerHTML = value
    ? `<button class="grid-btn has-data">📷</button>`
    : `<button class="grid-btn">📷</button>`;
  cell.onclick = () => openPhotoModal(row); // 复用现有 photo 弹窗逻辑
}
```

`gps/photo/checkin/track/签名` 共用此模式，弹层逻辑复用现有 `scExtras` 相关函数。

#### 5.1.5 离线与回写

沿用现有离线优先：`onchange` 先写 `state.formData` + localStorage，联网时批量 `POST /api/.../records`。jspreadsheet-ce 的 `onchange`/`oninsertrow`/`ondeleterow` 钩子对接现有 `saveRecord` 流程。

### 5.2 原生能力集成（Capacitor）

#### 5.2.1 相机拍照（R1）

- 插件：`@capacitor/camera`
- 流程：photo 单元格点击 → `Camera.getPhoto()` → 拿到 base64/path → 带经纬度水印 → `POST /api/.../photos`
- 抽象层：在 `app.js` 新增 `native.capturePhoto()`，内部判断 `window.Capacitor` 是否存在，存在则用原生，否则回退 `getUserMedia`。**Web 端不受影响**。

#### 5.2.2 后台 GPS 轨迹（R2）

- 插件：`@gachlab/capacitor-background-geolocation`（或 `@bglocation/capacitor`）
- 流程：
  1. 首次进入填写页，两步申请权限：前台 `location` → 后台 `backgroundLocation`（Android 10+ 必须）。
  2. track 单元格点击「开始」→ `bg.tracking.start({distanceFilter:25})` → 监听 `bg.locations.on` 累积点。
  3. 锁屏/后台由原生前台服务持续记录（带通知栏）。
  4. 「停止」→ `session.stop()` → 点集 `POST /api/.../track`（沿用现有 track API）。
- 权限清单（AndroidManifest，插件自动合并）：`ACCESS_FINE/COARSE/BACKGROUND_LOCATION`、`FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_LOCATION`（Android 14+）、`POST_NOTIFICATIONS`（Android 13+）。
- 抽象层：`native.startTrack()/stopTrack()`，Web 回退 `navigator.geolocation.watchPosition`（仅前台）。

#### 5.2.3 地图打点显示（R3）

- 方案：高德地图 JS API 2.0（WebView 内），`AMap.Map` + `Marker`（打卡点）+ `Polyline`（轨迹）。
- 嵌入位置：小班列表页右侧抽屉 / 调查填写页顶部地图区。
- 数据源：`GET /api/subcompartments/rows/<id>` 返回的 `extras`（checkin lng/lat、track points、photos lng/lat）。
- 抽象层：`native.showMap(container, points)`，Web/App 同一套 JS SDK，无差异。
- 备选：若 WebView 地图性能不足，再上 `@capacitor/google-maps` 或高德原生插件。

#### 5.2.4 手写签名（R4）

- 库：`signature_pad`（MIT，纯前端 canvas，~10KB）
- 集成：作为 jspreadsheet-ce 的「签名」自定义按钮单元格，点击弹出签名板（canvas），确认后存为 base64/PNG。
- 存储：新增 `records` 字段 `signature`（base64）或单独 `signatures` 表（见 5.4）。
- 导出：xlsx 导出时，签名以图片插入对应单元格（openpyxl 支持图片单元格）。

### 5.3 界面精简（只填五表）

App 端入口配置：Capacitor 启动 URL 指向填写页，并带 `?app=1` 参数。`app.js` 检测该参数：

- 隐藏：管理面板、用户管理、上传小班（管理员功能）。
- 保留：上传列表页（只读浏览）→ 小班列表页 → 调查填写页。
- 默认进入：当前项目的批次列表。

Web 端（PC）仍保留全部功能。通过 URL 参数区分，**不维护两套代码**。

### 5.4 Android 打包工程（Capacitor）

```
hqz-survey/
├─ survey/web/          ← 现有 Web 代码（0 改动）
├─ android-app/         ← 新增 Capacitor 工程
│   ├─ capacitor.config.json   ← webDir 指向 survey/web/static 或服务器 URL
│   ├─ package.json            ← @capacitor/core, camera, preferences, background-geolocation
│   ├─ android/                ← npx cap add android 生成
│   └─ src/                    ← JS 桥接封装 native.* API
└─ ...
```

两种部署模式（`capacitor.config.json` 配置）：

1. **远程模式（推荐，B/S）**：`server.url = https://your-server/survey/`，App 启动直连服务器，Web 代码改了 App 0 更新。原生能力通过 Capacitor 插件注入 WebView。
2. **本地内嵌模式（离线）**：`webDir = survey/web/static`，Web 资源打包进 APK，需 SQLite 本地化（复杂，二期再评估）。

一期用远程模式。

---

## 六、代码改造点（精确位置）

### 6.1 新增

| 文件 | 作用 |
|------|------|
| `survey/web/static/vendor/jspreadsheet-ce.js` (+ css) | 网格库本地化（离线可用） |
| `survey/web/static/vendor/signature_pad.js` | 签名库 |
| `survey/web/static/native.js` | 原生能力抽象层（camera/track/map/signature），判断 `window.Capacitor` |
| `survey/web/static/grid.js` | jspreadsheet-ce 集成：schema→columns、自定义单元格、回写 |
| `android-app/` | Capacitor 工程 |

### 6.2 修改

| 文件 | 改动 | 说明 |
|------|------|------|
| `survey/web/static/app.js` | 调查填写页渲染（约 `app.js:487`）由动态表单改为调用 `grid.js` | 渲染层替换，状态机不变 |
| `survey/web/static/app.js` | photo/checkin/track 弹层调用改走 `native.*` 抽象 | 兼容 Web/App |
| `survey/web/static/app.js` | 入口检测 `?app=1` 隐藏管理面板 | 界面精简 |
| `survey/web/templates/base.html` | 引入 jspreadsheet-ce / signature_pad / native.js / grid.js | 资源加载 |
| `survey/web/static/style.css` | 网格、签名板、地图区样式 | UI |

### 6.3 不动（契约保持）

- `survey/schema.py` — 唯一真相源不变（可能新增 `signature` 字段）
- `survey/storage.py` — 记录/扩展数据存取不变
- `survey/exporter.py` — 导出逻辑不变（签名导出另加）
- `survey/web/app.py` — API 不变（可能新增签名字段透传）
- 所有 `/api/...` 路由

---

## 七、数据库 / API 变更

### 7.1 数据库（最小改动）

`records` 表新增字段（向后兼容，nullable）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `signature` | TEXT | 手写签名 base64（PNG） |

或独立 `record_signatures(rid, signature_png, signed_at)` 表——若担心 base64 膨胀 records 行。**推荐独立表**，避免主表臃肿。

其余表结构不变。

### 7.2 API 变更

| API | 变更 |
|-----|------|
| `POST /api/projects/<pid>/records` | 透传 `signature`（若用独立表，则 records 不变，新增 `POST /api/records/<rid>/signature`） |
| `GET /api/subcompartments/rows/<id>` | 已返回 extras（checkin/track/photos 坐标），地图打点直接用，不改 |

原生能力（camera/track）产生的数据仍走现有 `photos`/`track` API，不改。

---

## 八、风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| 后台轨迹被 OEM 电池优化杀死（小米/华为） | 高 | 用支持「忽略电池优化」检测的插件；引导用户加白名单；`dontkillmyapp` 提示 |
| Android 10+ 后台定位权限两步申请易失败 | 中 | 严格按「前台→后台」顺序；拒绝后引导设置页开启 |
| Play Store 上架需位置声明表单 | 中 | 一期不上架 Play，企业内分发；上架前补声明 |
| jspreadsheet-ce 单元格自定义编辑器学习成本 | 中 | 先用内置 type 跑通主流程，特殊字段按钮单元格逐步加 |
| WebView 地图性能 | 低 | 林业打点场景点数少，JS SDK 足够；不足再上原生地图插件 |
| 远程模式依赖网络 | 中 | 沿用离线优先 localStorage 暂存；签名/轨迹本地缓存，联网回写 |
| 高德 JS SDK 商用 key | 低 | 申请企业 key；或用 Leaflet+OSM（免费但无国内偏移纠正） |
| 签名 base64 体积 | 低 | 独立表存；PNG 压缩；控制 canvas 尺寸 |

---

## 九、分阶段实施计划

### 阶段一：Excel 网格填表（Web 端先受益，不依赖 Android）

1. 引入 jspreadsheet-ce 本地化资源。
2. `grid.js`：schema → columns 映射，预填只读列、分组表头、内置类型单元格。
3. 改造调查填写页渲染，对接现有 saveRecord 离线回写。
4. 特殊字段（gps/photo/checkin/track）按钮单元格 + 弹窗（复用现有逻辑）。
5. Web 端冒烟测试：五表录入/导出一致性。

### 阶段二：手写签名 + 地图打点（Web/App 共用）

1. signature_pad 集成，签名按钮单元格 + 签名表/字段 + 导出图片。
2. 高德 JS SDK 嵌入，小班详情地图打点（打卡/轨迹/照片坐标）。

### 阶段三：Android 打包（Capacitor）

1. 建 `android-app/` Capacitor 工程，远程模式指向服务器。
2. 集成 `@capacitor/camera`、`background-geolocation`、`preferences`。
3. `native.js` 抽象层：photo/track 走原生，Web 回退。
4. 界面精简：`?app=1` 隐藏管理面板。
5. 真机测试：相机、后台轨迹（锁屏/灭屏）、地图打点、签名。
6. 出 APK，企业内分发。

### 阶段四（远期，可选）

- 本地内嵌模式（离线 SQLite 本地化）。
- iOS 支持。
- 微信小程序需求出现时，评估 uni-app 重构。

---

## 十、更优实践与优化方向

1. **渲染层与 schema 解耦**：网格/表单/未来 Vue 组件都消费同一 schema，换渲染层不碰后端。本次改造坚守此原则。
2. **原生能力抽象层 `native.js`**：所有原生调用经抽象，Web/App 同一套业务代码。避免 `if (isApp)` 散落业务逻辑。
3. **远程模式优先**：B/S 架构让 Web 端修复即时生效，App 免发版。仅原生插件升级才需重新打包 APK。
4. **签名独立表**：避免 base64 污染主记录表，导出时按 rid 关联。
5. **轨迹点抽样与压缩**：后台轨迹开启 `distanceFilter`（如 25m）减少点数；回写前做 Douglas-Peucker 抽稀。
6. **权限引导 UX**：后台定位/电池优化权限易被拒，首次启动做引导卡片 + 拒绝后设置页深链。
7. **jspreadsheet-ce 锁版本**：CE 版偶有 breaking change，`package.json` 锁定具体版本，避免自动升级踩坑。
8. **地图国内坐标纠偏**：GPS 原始 WGS84 在高德地图需转 GCJ02，封装 `wgs84togcj02` 工具（插件多自带）。
9. **APK 体积**：Capacitor 远程模式 APK 很小（仅 WebView 壳 + 插件）；避免把 Web 资源打进 APK。
10. **可观测性**：原生轨迹/相机失败要上报服务器日志（沿用 forest-data 日志体系），便于外业问题定位。

---

## 十一、决策摘要

| 决策 | 选择 | 理由 |
|------|------|------|
| 打包方案 | Capacitor 套壳（远程模式） | 现有代码 0 改动；后台轨迹有开源方案，避开 uni-app 付费插件+高德授权费 |
| 填表库 | jspreadsheet-ce (MIT) | jexcel 已 deprecated 的现行名；轻量、原生 JS 契合、自定义编辑器易做 |
| 地图 | 高德 JS SDK in WebView | 打点场景性能足够；免原生插件费用 |
| 签名 | signature_pad + 独立表 | 纯前端、轻量；独立表避免主表臃肿 |
| 界面精简 | `?app=1` URL 参数 | 不维护两套代码 |
| 部署 | B/S 远程模式 | Web 改动即时生效，App 免发版 |
| iOS / 小程序 | 远期再评估 | 当前需求仅 Android |
