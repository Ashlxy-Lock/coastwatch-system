import json

_DASHBOARD_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>海岸安全预警监控</title>
  <style>
    :root { color-scheme: dark; --bg:#07131b; --panel:#102530; --line:#214452;
      --text:#e7f7fb; --muted:#87a8b3; --safe:#29d391; --warn:#ffb84d;
      --danger:#ff5b61; --fault:#c584ff; --accent:#4bd6ff; --ink:#08161d; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:"Microsoft YaHei",system-ui,sans-serif;
      background:radial-gradient(circle at top right,#123646 0,var(--bg) 42%);
      color:var(--text); min-height:100vh; }
    main { max-width:1240px; margin:auto; padding:28px 18px 48px; }
    header { display:flex; justify-content:space-between; gap:20px; align-items:end;
      border-bottom:1px solid var(--line); padding-bottom:18px; }
    h1 { margin:0; font-size:clamp(24px,4vw,38px); letter-spacing:.05em; }
    .sub,.muted { color:var(--muted); }
    .status { border:1px solid var(--line); border-radius:999px; padding:7px 13px; }
    .online { color:var(--safe); } .offline { color:var(--danger); }
    .grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px;
      margin:22px 0; }
    .card,.panel { background:linear-gradient(145deg,rgba(16,37,48,.96),rgba(11,29,38,.96));
      border:1px solid var(--line); border-radius:14px; box-shadow:0 14px 30px #0005; }
    .card { padding:18px; min-height:128px; }
    .label { color:var(--muted); font-size:13px; }
    .value { font-size:30px; font-weight:700; margin-top:14px; word-break:break-word; }
    .unit { color:var(--muted); font-size:14px; margin-left:4px; }
    .panel { padding:18px; margin-top:14px; overflow:auto; }
    .panel h2 { margin:0 0 14px; font-size:18px; }
    .alarm { color:var(--safe); } .alarm[data-level="2"] { color:var(--warn); }
    .alarm[data-level="3"] { color:var(--danger); }
    .alarm[data-level="4"] { color:var(--fault); }
    .chips { display:flex; flex-wrap:wrap; gap:8px; }
    .chip { background:#163542; color:var(--muted); border-radius:999px; padding:7px 10px; }
    .location-tools { display:grid; grid-template-columns:2fr 1.3fr auto; gap:10px;
      align-items:end; }
    .field { display:flex; flex-direction:column; gap:6px; color:var(--muted); font-size:13px; }
    input,select,button { min-height:42px; border-radius:9px; border:1px solid var(--line);
      background:#0b1d26; color:var(--text); padding:9px 11px; font:inherit; }
    button { cursor:pointer; background:#17617a; border-color:#2e91ad; font-weight:700; }
    button:hover { filter:brightness(1.12); }
    .search-row { display:flex; gap:10px; margin-top:12px; }
    .search-row input { flex:1; }
    .location-results { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
    .location-results button { background:#163542; font-weight:500; text-align:left; }
    .chip.ok { color:var(--safe); border:1px solid #29d39155; }
    .simulation-grid { display:grid; grid-template-columns:1.15fr .85fr; gap:14px; }
    .scenario-panel { border:1px solid #ffb84d66; border-radius:12px; padding:15px;
      margin-top:14px; background:linear-gradient(145deg,#2a2418,#101f27); }
    .scenario-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px;
      margin-top:13px; }
    .scenario-grid .span-2 { grid-column:span 2; }
    .scenario-grid input { width:100%; }
    .scenario-provenance { display:flex; flex-wrap:wrap; gap:8px; padding:11px 12px;
      border:1px solid var(--line); border-radius:10px; background:#091a22; margin-top:12px; }
    .scenario-import { display:grid; grid-template-columns:1fr auto; gap:10px;
      align-items:end; margin-top:12px; }
    .scenario-import textarea { width:100%; min-height:82px; resize:vertical; border-radius:9px;
      border:1px solid var(--line); background:#0b1d26; color:var(--text); padding:9px 11px;
      font:12px ui-monospace,SFMono-Regular,Consolas,monospace; }
    .feature-groups { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px;
      margin-top:11px; }
    .feature-group { border:1px solid var(--line); border-radius:10px; background:#091a22;
      padding:11px 12px; color:var(--muted); font-size:12px; line-height:1.65; }
    .feature-group strong { color:var(--text); display:block; margin-bottom:5px; }
    .tool-row { display:flex; flex-wrap:wrap; gap:10px; align-items:end; margin-top:12px; }
    .tool-row .field { min-width:130px; flex:1; }
    .tool-row .wide { min-width:240px; flex:2; }
    button.secondary { background:#163542; border-color:var(--line); }
    button.danger { background:#7c2d34; border-color:#c94850; }
    button:disabled { opacity:.45; cursor:not-allowed; filter:none; }
    .model-list { display:grid; gap:8px; }
    .model-card { border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
    .model-card.selected { border-color:var(--safe); background:#14352f; }
    .model-meta { color:var(--muted); font-size:12px; margin-top:5px; }
    .notice { border-left:3px solid var(--warn); padding:9px 12px; background:#2a2418;
      color:#f7d69a; margin:10px 0 14px; }
    .compact { font-size:13px; }
    tr.selected-sample { background:#163542; }
    .section-heading { display:flex; justify-content:space-between; gap:14px; align-items:start;
      flex-wrap:wrap; }
    .section-heading h2 { margin-bottom:4px; }
    .eyebrow { color:var(--accent); font-size:11px; font-weight:800; letter-spacing:.16em;
      text-transform:uppercase; }
    .badges { display:flex; flex-wrap:wrap; gap:7px; }
    .badge { border:1px solid var(--line); border-radius:999px; padding:5px 9px;
      color:var(--muted); font-size:11px; font-weight:800; letter-spacing:.05em; }
    .badge.safe,.badge.active { color:var(--safe); border-color:#29d39166; background:#12362f; }
    .badge.danger { color:var(--danger); border-color:#ff5b6166; background:#371c23; }
    .badge.warn { color:var(--warn); border-color:#ffb84d66; background:#332918; }
    .badge.research { color:var(--accent); border-color:#4bd6ff66; background:#11313c; }
    .summary-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px;
      margin:14px 0; }
    .summary-card { border:1px solid var(--line); border-radius:11px; padding:12px;
      background:#0b1d26; min-height:83px; }
    .summary-card strong { display:block; margin-top:8px; font-size:22px; }
    .summary-card small { color:var(--muted); }
    .session-list { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px;
      margin-top:12px; }
    .session-card { border:1px solid var(--line); border-radius:9px; background:#0b1d26;
      overflow:hidden; }
    .session-card.selected { border-color:var(--accent); box-shadow:inset 0 0 0 1px #4bd6ff44; }
    .session-card.training-selected { border-color:var(--safe); box-shadow:inset 0 0 0 1px #29d39144; }
    .session-button { width:100%; min-height:82px; text-align:left; background:transparent;
      border:0; border-radius:0; font-weight:500; padding:11px 12px; }
    .session-button .session-top { display:flex; justify-content:space-between; gap:8px;
      align-items:center; font-weight:750; }
    .session-button .session-meta { color:var(--muted); font-size:12px; margin-top:8px;
      line-height:1.45; }
    .session-training-toggle { display:flex; gap:8px; align-items:center; border-top:1px solid var(--line);
      padding:8px 12px; color:var(--muted); font-size:12px; cursor:pointer; }
    .session-training-toggle input { min-height:auto; width:17px; height:17px; margin:0; accent-color:var(--safe); }
    .session-actions { display:flex; gap:8px; align-items:center; border-top:1px solid var(--line);
      padding:8px 12px; }
    .session-actions button { width:100%; min-height:36px; padding:7px 10px; font-size:12px; }
    .session-actions button.confirm-delete { background:#b4232b; border-color:#ff757a;
      box-shadow:0 0 0 2px #ff5b6133; }
    .training-selection-toolbar { display:flex; justify-content:space-between; gap:12px;
      align-items:center; flex-wrap:wrap; margin-top:14px; padding:11px 12px;
      border:1px solid #29d39155; border-radius:10px; background:#0b2528; }
    .training-selection-toolbar .tool-row { margin-top:0; }
    .chart-shell { border:1px solid var(--line); border-radius:12px; background:#081a23;
      padding:12px; margin-top:14px; overflow:hidden; }
    .chart-toolbar { display:flex; justify-content:space-between; gap:12px; align-items:center;
      flex-wrap:wrap; margin-bottom:8px; }
    .chart-legend { display:flex; flex-wrap:wrap; gap:12px; color:var(--muted); font-size:12px; }
    .legend-key { display:inline-flex; gap:6px; align-items:center; }
    .legend-dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
    .chart-scroll { overflow:auto; border-radius:9px; }
    #simulationChart { display:block; width:100%; min-width:720px; height:auto;
      background:linear-gradient(180deg,#0a202a,#081820); cursor:crosshair; }
    .chart-help { color:var(--muted); font-size:12px; margin-top:8px; }
    .quality-grid { display:grid; grid-template-columns:1.25fr 1fr; gap:12px; margin-top:14px; }
    .quality-panel { border:1px solid var(--line); border-radius:11px; padding:13px;
      background:#0b1d26; }
    .quality-panel h3,.training-panel h3 { margin:0 0 10px; font-size:15px; }
    .quality-row { display:grid; grid-template-columns:140px 1fr auto; align-items:center;
      gap:9px; margin-top:9px; color:var(--muted); font-size:12px; }
    .bar { height:8px; background:#17333f; border-radius:999px; overflow:hidden; }
    .bar > span { display:block; height:100%; border-radius:inherit; background:var(--accent); }
    .coverage { display:flex; height:14px; border-radius:999px; overflow:hidden;
      background:#31404a; margin:11px 0 8px; }
    .coverage span { display:block; min-width:0; }
    .coverage .safe { background:var(--safe); }.coverage .danger { background:var(--danger); }
    .coverage .unknown { background:#536773; }
    .coverage-copy { display:flex; flex-wrap:wrap; gap:13px; color:var(--muted); font-size:12px; }
    .label-pill { display:inline-flex; align-items:center; border-radius:999px; padding:3px 7px;
      font-size:11px; font-weight:800; letter-spacing:.04em; }
    .label-pill.safe { color:var(--safe); background:#14352f; }
    .label-pill.danger { color:#ff979b; background:#3a1d24; }
    .label-pill.unknown { color:#b2c3ca; background:#253943; }
    .training-panel { border:1px solid #4bd6ff55; border-radius:12px; padding:14px;
      margin-top:14px; background:linear-gradient(145deg,#0b2530,#0b1c25); }
    .readiness { display:flex; gap:10px; align-items:start; padding:10px 12px;
      border-radius:9px; background:#0a1a22; color:var(--muted); margin-top:10px; }
    .readiness .compact { overflow-wrap:anywhere; }
    .readiness.ready { border-left:3px solid var(--safe); }
    .readiness.blocked { border-left:3px solid var(--warn); }
    .metrics-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:9px;
      margin-top:12px; }
    .metric-card { border:1px solid var(--line); border-radius:10px; padding:11px;
      background:#091a22; }
    .metric-card strong { display:block; font-size:21px; margin-top:7px; }
    .metric-card small { color:var(--muted); font-size:11px; line-height:1.35; }
    .evaluation-grid { display:grid; grid-template-columns:.8fr 1.2fr; gap:12px; margin-top:12px; }
    .confusion { display:grid; grid-template-columns:86px repeat(2,minmax(78px,1fr));
      gap:5px; align-items:stretch; }
    .matrix-head { color:var(--muted); font-size:11px; display:grid; place-items:center;
      min-height:32px; text-align:center; }
    .matrix-cell { min-height:64px; display:grid; place-items:center; border-radius:8px;
      background:#102f3a; font-size:22px; font-weight:800; }
    .matrix-cell.correct { background:#123b31; color:var(--safe); }
    .matrix-cell.error { background:#3b2027; color:#ff9ca0; margin:0; min-height:64px; }
    .split-details { border:1px solid var(--line); border-radius:10px; padding:12px;
      color:var(--muted); font-size:12px; line-height:1.65; background:#091a22; }
    .split-details strong { color:var(--text); }
    .table-note { color:var(--muted); font-size:12px; margin:9px 0 0; }
    .sticky-actions { display:flex; justify-content:space-between; gap:12px; align-items:center;
      flex-wrap:wrap; }
    table { width:100%; border-collapse:collapse; min-width:760px; }
    th,td { border-bottom:1px solid var(--line); text-align:left; padding:10px 8px; }
    th { color:var(--muted); font-size:12px; font-weight:500; }
    .error { color:var(--danger); min-height:1.5em; margin-top:12px; }
    .strategy-console { border-color:#4bd6ff66; position:relative; }
    .strategy-console.sensor-console { border-color:#29d39166; }
    .boundary-banner { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px;
      border:1px solid var(--line); border-radius:11px; overflow:hidden; margin:14px 0;
      background:var(--line); }
    .boundary-step { background:#091a22; padding:12px; min-height:86px; }
    .boundary-step strong { display:block; margin:5px 0; }
    .boundary-step small { color:var(--muted); line-height:1.5; }
    .console-grid { display:grid; grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr);
      gap:14px; align-items:start; }
    .console-pane { border:1px solid var(--line); border-radius:12px; padding:14px;
      background:#091a22; min-width:0; }
    .console-pane h3 { margin:0 0 10px; font-size:16px; }
    .form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
    .form-grid .span-2 { grid-column:span 2; }
    .form-grid input,.form-grid select { width:100%; }
    select[multiple] { min-height:118px; }
    .provenance-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px;
      margin-top:12px; }
    .provenance-item { border:1px solid var(--line); border-radius:9px; padding:9px 10px;
      min-height:64px; background:#0b1d26; overflow-wrap:anywhere; }
    .provenance-item strong { display:block; margin-top:5px; font-size:13px; }
    .provenance-grid .span-2 { grid-column:span 2; }
    .invariant { border:1px solid #29d39177; color:var(--safe); background:#102f29;
      border-radius:10px; padding:10px 12px; font-weight:850; letter-spacing:.04em; }
    .blocker-list { margin:8px 0 0; padding-left:20px; color:var(--muted); }
    .blocker-list li+li { margin-top:5px; }
    .run-history { display:grid; gap:8px; margin-top:10px; max-height:230px; overflow:auto; }
    .run-button { text-align:left; width:100%; background:#0b1d26; border-color:var(--line);
      font-weight:500; }
    .run-button[aria-current="true"] { border-color:var(--accent); background:#11313c; }
    .formula { background:#061219; border:1px solid var(--line); border-radius:9px; padding:11px;
      font:13px ui-monospace,SFMono-Regular,Consolas,monospace; color:#bfefff;
      overflow-wrap:anywhere; margin-top:10px; }
    .live-mapping { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px;
      margin-top:10px; }
    .live-mapping .summary-card { min-height:72px; }
    .choice-row { display:flex; flex-wrap:wrap; gap:9px; margin:10px 0; }
    .choice-row label { display:flex; align-items:center; gap:7px; border:1px solid var(--line);
      border-radius:9px; padding:9px 11px; cursor:pointer; color:var(--muted); }
    .choice-row input { min-height:auto; width:17px; height:17px; padding:0; }
    .retired-workspace { margin-top:14px; border:1px dashed #ffb84d77; border-radius:12px;
      padding:0 14px 14px; background:#151c20; }
    .retired-workspace > summary { cursor:pointer; padding:14px 0; font-weight:800;
      color:#f7d69a; }
    :focus-visible { outline:3px solid #4bd6ff; outline-offset:2px; }
    @media (max-width:1000px) { .summary-grid,.metrics-grid { grid-template-columns:repeat(3,1fr); }
      .session-list { grid-template-columns:repeat(2,1fr); } }
    @media (max-width:850px) { .grid { grid-template-columns:repeat(2,1fr); }
      .simulation-grid { grid-template-columns:1fr; }
      .scenario-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .feature-groups { grid-template-columns:1fr; }
      .quality-grid,.evaluation-grid,.console-grid { grid-template-columns:1fr; }
      .location-tools { grid-template-columns:1fr; } }
    @media (max-width:600px) { .summary-grid,.metrics-grid,.session-list { grid-template-columns:1fr 1fr; } }
    @media (max-width:500px) { .grid,.summary-grid,.metrics-grid,.session-list { grid-template-columns:1fr; }
      .scenario-grid { grid-template-columns:1fr; }.scenario-grid .span-2 { grid-column:span 1; }
      .scenario-import,.boundary-banner { grid-template-columns:1fr; }
      .form-grid,.provenance-grid,.live-mapping { grid-template-columns:1fr; }
      .form-grid .span-2,.provenance-grid .span-2 { grid-column:span 1; }
      header{align-items:start;flex-direction:column;} .quality-row{grid-template-columns:110px 1fr auto;} }
  </style>
</head>
<body><main>
  <header><div><h1>海岸安全预警监控</h1><div class="sub">设备 COAST_01 · 本地监控系统</div></div>
    <div><div id="online" class="status offline">等待遥测</div>
      <div id="adminControls" class="tool-row" style="display:none;margin-top:8px;justify-content:flex-end"><span id="adminIdentity" class="muted"></span><button id="logoutAdmin" class="secondary compact" type="button">退出登录</button></div>
    </div></header>
  <section class="panel">
    <h2>屏幕显示地区</h2>
    <div class="location-tools">
      <label class="field">常用沿海地区<select id="locationPreset"></select></label>
      <label class="field">液晶英文短名<input id="displayLocation" maxlength="32" pattern="[A-Za-z0-9 ._-]+" placeholder="例如 QINGDAO COAST"></label>
      <button id="saveLocation" type="button">保存到设备</button>
    </div>
    <div class="search-row"><input id="locationQuery" maxlength="80" placeholder="也可以搜索任意城市，例如：青岛">
      <button id="searchLocation" type="button">搜索地区</button></div>
    <div id="locationResults" class="location-results"></div>
    <div id="locationStatus" class="muted" style="margin-top:12px">正在读取地区配置…</div>
  </section>
  <section class="grid">
    <div class="card"><div class="label">报警状态</div><div id="alarm" class="value alarm">--</div></div>
    <div class="card"><div class="label">水面距离</div><div class="value"><span id="distance">--</span><span class="unit">mm</span></div></div>
    <div class="card"><div class="label">水位上升</div><div class="value"><span id="rise">--</span><span class="unit">mm</span></div></div>
    <div class="card"><div class="label">变化速度</div><div class="value"><span id="rate">--</span><span class="unit">mm/s</span></div></div>
    <div class="card"><div class="label">人员检测</div><div id="person" class="value">--</div></div>
    <div class="card"><div class="label">Wi-Fi 信号</div><div class="value"><span id="rssi">--</span><span class="unit">dBm</span></div></div>
    <div class="card"><div class="label">序号 / 运行时间</div><div id="sequence" class="value" style="font-size:20px">--</div></div>
    <div class="card"><div class="label">天气与海况</div><div id="environment" class="value" style="font-size:17px">--</div></div>
  </section>
  <section class="panel"><h2>设备健康位</h2><div id="health" class="chips"></div>
    <div id="updated" class="muted" style="margin-top:12px">尚无数据</div></section>

  <section class="panel strategy-console" id="officialTrainingConsole" aria-labelledby="officialTrainingHeading">
    <div class="section-heading">
      <div><div class="eyebrow">UK OFFICIAL DATA · MANUAL TRAINING</div><h2 id="officialTrainingHeading">英国官方海岸模型训练台</h2>
        <div class="muted compact">选择已注册的英国官方数据集、站点与时间切分，手动启动二分类逻辑回归训练。</div></div>
      <div class="badges"><span class="badge safe">OFFICIAL DATA ONLY</span><span class="badge research">LOGISTIC REGRESSION</span><span class="badge warn">SHADOW ONLY</span></div>
    </div>
    <div class="notice">模型仅学习经质量控制的英国官方海岸观测与明确的未来极端海平面条件标签。输出是 <strong>Extreme sea-level condition probability</strong>，不是海啸、洪水或自然灾害概率。</div>
    <div class="notice provenance-disclosure"><strong>Provenance 边界：</strong><code>operator_attested_raw_hash_verified</code> 表示服务器验证 raw 原始字节、SHA-256 与 manifest 结构；官方归属、许可和标签派生仍由操作者声明。<code>deterministic_importer_replay_verified=false</code> 表示本系统没有独立重放从原始档案到 harmonised 表和标签的转换。</div>
    <div class="notice"><strong>18-feature contract：</strong>1 个可由传感器线性映射替换的相对水位 + 17 个冻结官方上下文：预测潮位、有效波高、波周期、风速、阵风、气压、降雨、气温、相对湿度、水温、流速、小时 sin/cos、年周期 sin/cos、纬度、经度。不额外加入由相对水位减预测潮位得到的残差特征。</div>
    <div class="boundary-banner" aria-label="训练与外部测试边界">
      <div class="boundary-step"><span class="badge safe">1 · FIT</span><strong>官方训练时间段</strong><small>只在这一段拟合标准化器和逻辑回归参数。</small></div>
      <div class="boundary-step"><span class="badge research">2 · EVALUATE</span><strong>官方验证 / 冻结测试</strong><small>验证集选阈值；冻结测试集只用于最终指标。</small></div>
      <div class="boundary-step"><span class="badge warn">3 · EXTERNAL TEST</span><strong>ESP32 不参与训练</strong><small>超声波仅在模型冻结后经线性映射做硬件在环测试。</small></div>
    </div>
    <div class="invariant" id="officialLeakageInvariant" role="status">SENSOR ROWS USED FOR FIT = 0 · SCALER = 0 · THRESHOLD = 0</div>

    <div class="console-grid" style="margin-top:14px">
      <div class="console-pane">
        <div class="section-heading"><h3>① 选择官方数据与时间切分</h3><button id="rescanOfficialDatasets" class="secondary compact" type="button">重新扫描受保护数据目录</button></div>
        <div class="form-grid">
          <label class="field span-2">已注册官方数据集<select id="officialDataset" aria-describedby="officialDatasetStatus"><option value="">正在读取…</option></select></label>
          <label class="field span-2">英国站点（Ctrl / Cmd 可多选）<select id="officialSites" multiple aria-label="选择英国官方站点"></select></label>
          <label class="field">Manifest 训练开始<input id="officialTrainStart" type="datetime-local" readonly></label>
          <label class="field">Manifest 训练结束<input id="officialTrainEnd" type="datetime-local" readonly></label>
          <label class="field">Manifest 验证开始<input id="officialValidationStart" type="datetime-local" readonly></label>
          <label class="field">Manifest 验证结束<input id="officialValidationEnd" type="datetime-local" readonly></label>
          <label class="field">Manifest 冻结测试开始<input id="officialTestStart" type="datetime-local" readonly></label>
          <label class="field">Manifest 冻结测试结束<input id="officialTestEnd" type="datetime-local" readonly></label>
        </div>
        <div id="officialDatasetStatus" class="muted compact" style="margin-top:10px" aria-live="polite">正在读取已注册数据集…时间切分只能来自受审计 manifest，界面不能修改以避免泄漏。</div>
        <div id="officialRescanStatus" class="readiness blocked" style="margin-top:10px" aria-live="polite"><span>●</span><div><strong>尚未重新扫描</strong><div class="compact">扫描响应中的每个 bundle 错误都会在这里显示；HTTP 200 不等于所有 bundle 注册成功。</div></div></div>
        <div class="provenance-grid" aria-label="官方数据来源与完整性">
          <div class="provenance-item"><span class="label">官方来源</span><strong id="officialSource">—</strong></div>
          <div class="provenance-item"><span class="label">许可 / 重分发条款</span><strong id="officialLicense">—</strong></div>
          <div class="provenance-item"><span class="label">Dataset SHA-256</span><strong id="officialDatasetHash">—</strong></div>
          <div class="provenance-item"><span class="label">站点 / 日期 / 行数</span><strong id="officialCoverage">—</strong></div>
          <div class="provenance-item"><span class="label">目标标签定义</span><strong id="officialLabelDefinition">—</strong></div>
          <div class="provenance-item"><span class="label">时间切分和防泄漏</span><strong id="officialSplitDefinition">—</strong></div>
          <div class="provenance-item"><span class="label">Dataset provenance assurance</span><strong id="officialDatasetProvenance">—</strong></div>
          <div class="provenance-item"><span class="label">Deterministic importer replay</span><strong id="officialDatasetImporterReplay">—</strong></div>
          <div class="provenance-item span-2"><span class="label">已选站点与证据范围</span><strong id="officialEvidenceScope">尚未选择站点</strong></div>
        </div>
        <div id="officialReadiness" class="readiness blocked" aria-live="polite"><span>●</span><div><strong>未检查</strong><div class="compact">选择数据集和站点后，服务器将验证来源、哈希、标签和时间切分。</div></div></div>
        <ul id="officialBlockers" class="blocker-list" aria-label="训练阻断条件"></ul>
        <div class="tool-row"><button id="trainOfficialModel" type="button" disabled>手动训练官方模型</button><span class="muted compact">不会读取任何 ESP32 会话。</span></div>
      </div>

      <div class="console-pane">
        <div class="section-heading"><h3>② 训练运行、冻结测试与激活</h3><div class="tool-row" style="margin-top:0"><button id="refreshOfficialRuns" class="secondary compact" type="button">刷新运行</button><button id="activateOfficialRun" type="button" disabled>激活为 Shadow</button></div></div>
        <div id="officialRunStatus" class="readiness blocked" aria-live="polite"><span>●</span><div><strong>尚未训练</strong><div class="compact">只有成功且通过官方冻结测试的运行可被激活为 Shadow。</div></div></div>
        <div class="run-history" id="officialRunHistory" aria-label="官方模型训练运行"><div class="muted">正在读取运行历史…</div></div>
        <div id="officialBaselineVerdict" class="notice" style="margin-top:12px" role="status" aria-live="polite"><strong>教授问题：机器学习是否比简单阈值好？</strong><br>等待服务器返回同一冻结测试集上的 classification-only 对比结论。硬阈值没有概率输出，不比较 Brier / ROC AUC / PR AUC。</div>
        <div class="metrics-grid" aria-label="官方冻结测试指标">
          <div class="metric-card"><small>Site-macro PR AUC ↑</small><strong id="officialMetricPRAuc">—</strong><small>稀有极端条件的主科学指标</small></div>
          <div class="metric-card"><small>Site-macro DANGER recall</small><strong id="officialMetricRecall">—</strong><small>完整站点覆盖时的平均漏检能力</small></div>
          <div class="metric-card"><small>Site-macro DANGER precision</small><strong id="officialMetricPrecision">—</strong><small>完整站点覆盖时的平均误报成本</small></div>
          <div class="metric-card"><small>Site-macro DANGER F1</small><strong id="officialMetricF1">—</strong><small>完整站点覆盖时的平均平衡指标</small></div>
          <div class="metric-card"><small>Site-macro ROC AUC</small><strong id="officialMetricRocAuc">—</strong><small>完整站点覆盖时的平均排序能力</small></div>
          <div class="metric-card"><small>Row-level companion Brier ↓</small><strong id="officialMetricBrier">—</strong><small>伴随概率校准指标，不冒充 site-macro</small></div>
          <div class="metric-card"><small>False-positive rows / day · row-level companion ↓</small><strong id="officialMetricFalsePositiveRows">—</strong><small>每天被误分为极端条件的数据行；不是事件级告警数</small></div>
          <div class="metric-card"><small>Decision threshold</small><strong id="officialMetricThreshold">—</strong><small>仅由验证集选定</small></div>
          <div class="metric-card"><small>Site-macro coverage</small><strong id="officialMetricSiteCoverage">—</strong><small>eligible / selected；不完整时主指标为 N/A</small></div>
        </div>
        <div class="feature-groups" aria-label="官方模型与基线对比">
          <div class="split-details"><strong>Logistic Regression</strong><div id="officialModelSummary">等待冻结测试指标。</div></div>
          <div class="split-details"><strong>Water-level Threshold Baseline · validation-selected per-site</strong><div id="officialThresholdBaseline">Baseline unavailable。</div></div>
          <div class="split-details"><strong>Persistence Baseline</strong><div id="officialPersistenceBaseline">Baseline unavailable。</div></div>
        </div>
        <div class="split-details" style="margin-top:10px"><strong>训练运行 provenance</strong><div id="officialRunProvenance" style="margin-top:5px">尚无训练运行。</div></div>
        <div class="split-details" style="margin-top:10px"><strong>模型工件 provenance</strong><div id="officialArtifactProvenance" style="margin-top:5px">尚无工件。</div></div>
      </div>
    </div>
  </section>

  <section class="panel strategy-console sensor-console" id="sensorExternalTestConsole" aria-labelledby="sensorExternalTestHeading">
    <div class="section-heading">
      <div><div class="eyebrow">HARDWARE-IN-THE-LOOP · POST-TRAINING ONLY</div><h2 id="sensorExternalTestHeading">超声波线性映射外部测试台</h2>
        <div class="muted compact">把超声波水位上升量线性放大到指定英国站点量级，然后向已冻结模型做外部硬件测试。</div></div>
      <div class="badges"><span class="badge safe">SENSOR ROWS FOR FIT = 0</span><span class="badge warn">EXTERNAL TEST</span><span class="badge research">LINEAR GAIN V1</span></div>
    </div>
    <div class="notice">超声波测试不会重新拟合、调阈值或更改模型哈希。推理使用 <strong>18 features = 1 个被传感器替换的水位特征 + 17 个官方冻结上下文值</strong>；预测潮位、风浪、阵风、气压、降雨、气温、湿度、水温、流速、时空周期与经纬度均来自冻结官方上下文，不允许人工伪造。</div>
    <div class="console-grid">
      <div class="console-pane">
        <h3>① 预注册并冻结线性映射档案</h3>
        <div class="form-grid">
          <label class="field span-2">冻结官方模型<input id="sensorOfficialModel" readonly value="尚无已激活官方模型"></label>
          <label class="field">目标英国站点<select id="sensorStation"><option value="">先选择官方数据集</option></select></label>
          <label class="field">官方冻结测试上下文<select id="sensorContextId"><option value="">先激活官方模型</option></select></label>
          <label class="field span-2">映射模式<select id="sensorProfileMode"><option value="formal">FORMAL — 由独立校准会话和官方训练分位数派生</option><option value="exploratory">EXPLORATORY — 手动 gain，不进入正式指标</option></select></label>
          <label class="field">线性放大倍数 gain<input id="sensorGain" type="number" min="0.000001" step="0.000001" placeholder="由服务器派生"></label>
          <label class="field">参考海平面 reference (m)<input id="sensorReferenceLevel" type="number" step="0.000001" placeholder="由官方工件派生" readonly></label>
          <label class="field">高程基准 datum<input id="sensorDatum" maxlength="40" placeholder="由官方工件派生" readonly></label>
          <label class="field">独立校准会话<select id="sensorCalibrationSession"><option value="">请选择已结束会话</option></select></label>
        </div>
        <div class="formula" id="sensorMappingFormula">mapped_level_m = reference_level_m + gain × (water_rise_mm / 1000)</div>
        <div class="live-mapping" aria-label="超声波实时映射预览">
          <div class="summary-card"><small>RAW SENSOR</small><strong id="sensorRawRise">—</strong><small>water rise (mm)</small></div>
          <div class="summary-card"><small>MAPPED OFFICIAL SCALE</small><strong id="sensorMappedLevel">—</strong><small>proxy level (m)</small></div>
          <div class="summary-card"><small>TRAIN RANGE</small><strong id="sensorOodState">—</strong><small>OUT-OF-DISTRIBUTION 不会被裁剪</small></div>
        </div>
        <div class="tool-row"><button id="freezeSensorProfile" type="button" disabled>冻结映射档案</button><button id="clearSensorProfile" class="danger" type="button" disabled>删除当前档案</button></div>
        <div id="sensorProfileStatus" class="muted compact" style="margin-top:10px" aria-live="polite">在开始用于正式外部测试的采集之前，必须先冻结 profile、模型哈希和官方上下文。</div>
        <div class="split-details" style="margin-top:10px"><strong>映射与校准 provenance</strong><div id="sensorProfileProvenance" style="margin-top:5px">尚无冻结 profile。FORMAL 模式将记录官方 TRAIN Q05/Q95、独立校准会话 Q05/Q95、gain_m_per_m 和 reference_level_m。</div></div>
      </div>

      <div class="console-pane">
        <h3>② 运行训练后外部测试</h3>
        <div class="form-grid">
          <label class="field span-2">已结束 ESP32 采集会话<select id="sensorTestSession"><option value="">正在读取会话…</option></select></label>
        </div>
        <div class="tool-row"><button id="runSensorExternalTest" type="button" disabled>运行外部测试</button><button id="refreshSensorTestRuns" class="secondary" type="button">刷新测试结果</button></div>
        <div id="sensorTestStatus" class="readiness blocked" aria-live="polite"><span>●</span><div><strong>尚未运行</strong><div class="compact">测试必须使用在采集前冻结的 profile；结果与官方冻结测试指标分开。</div></div></div>
        <div class="metrics-grid" aria-label="超声波外部测试指标">
          <div class="metric-card"><small>Input rows</small><strong id="sensorMetricInputSamples">—</strong><small>采集会话完整输入聚合</small></div>
          <div class="metric-card"><small>Valid ultrasonic rows</small><strong id="sensorMetricValidSamples">—</strong><small>完整输入中的有效超声波行</small></div>
          <div class="metric-card"><small>Evaluated rows</small><strong id="sensorMetricSamples">—</strong><small>实际进入冻结模型的有效行</small></div>
          <div class="metric-card"><small>Excluded invalid rows</small><strong id="sensorMetricInvalidSamples">—</strong><small>未进入推理的异常超声波行</small></div>
          <div class="metric-card"><small>OOD rate</small><strong id="sensorMetricOod">—</strong><small>超出官方训练范围</small></div>
          <div class="metric-card"><small>Mapped min</small><strong id="sensorMetricMin">—</strong><small>proxy level (m)</small></div>
          <div class="metric-card"><small>Mapped max</small><strong id="sensorMetricMax">—</strong><small>proxy level (m)</small></div>
          <div class="metric-card"><small>Mean risk score</small><strong id="sensorMetricMeanRisk">—</strong><small>模型输出，不是灾害概率</small></div>
          <div class="metric-card"><small>Inference latency</small><strong id="sensorMetricLatency">—</strong><small>ms / sample</small></div>
          <div class="metric-card"><small>Result rows aggregated</small><strong id="sensorMetricResultRows">—</strong><small>用于完整统计的预测结果行</small></div>
          <div class="metric-card"><small>Rows returned as preview</small><strong id="sensorMetricPreviewRows">—</strong><small>有界预览，不代表统计样本量</small></div>
        </div>
        <div class="split-details" style="margin-top:10px"><strong>聚合与预览政策</strong><div id="sensorTestEvaluationPolicy" style="margin-top:5px">完整指标聚合在全部 evaluated rows 上；接口仅保存并返回有限、均匀抽样的 rows preview，以避免大数据集拖慢浏览器。</div></div>
        <div class="split-details" style="margin-top:10px"><strong>外部测试追溯</strong><div id="sensorTestProvenance" style="margin-top:5px">尚无外部测试运行。</div></div>
        <div class="run-history" id="sensorTestRunHistory" aria-label="超声波外部测试历史"></div>
        <div class="notice" style="margin-top:12px"><strong>结果解释边界：</strong>该面板只证明“硬件数据 → 线性映射 → 冻结模型”流程可运行。它不能代替真实海岸水文仪器的现场验证。</div>
      </div>
    </div>
  </section>

  <section class="panel" id="simulationPanel">
    <details class="retired-workspace" id="legacySimulationArchive">
      <summary>Legacy archive — 旧版人工模拟场景、标注、会话浏览与删除（不再作为主训练流程）</summary>
    <div class="section-heading">
      <div><div class="eyebrow">LEGACY · OPERATOR-LABELLED ARCHIVE</div><h2>旧版模拟水位数据归档</h2>
        <div class="muted compact">ESP32 触屏采集 → 服务器整理 → 后台人工标注 → 按完整会话隔离训练与测试</div></div>
      <div class="badges"><span class="badge danger">RETIRED FROM PRIMARY TRAINING</span><span class="badge research">RESEARCH</span><span class="badge warn">SIMULATION DATA</span><span class="badge">SHADOW ONLY</span></div>
    </div>
    <div class="notice">仅用于桌面水槽/超声波模拟实验。SAFE、DANGER、UNKNOWN 均由操作者在这里标注；图表不会按水位阈值自动生成灾害标签，模型输出也不代表真实海岸灾害概率。</div>

    <div class="summary-grid" aria-label="数据集总览">
      <div class="summary-card"><small>采集会话</small><strong id="summarySessions">--</strong><small id="summarySessionStates">等待服务器</small></div>
      <div class="summary-card"><small>总样本</small><strong id="summarySamples">--</strong><small>服务器已保存</small></div>
      <div class="summary-card"><small>有效超声波</small><strong id="summaryValid">--</strong><small id="summaryValidRate">数据质量 --</small></div>
      <div class="summary-card"><small>SAFE 标签</small><strong id="summarySafe">--</strong><small>人工标注样本</small></div>
      <div class="summary-card"><small>DANGER 标签</small><strong id="summaryDanger">--</strong><small>人工标注样本</small></div>
      <div class="summary-card"><small>标签覆盖率</small><strong id="summaryCoverage">--</strong><small id="summaryUnknown">UNKNOWN --</small></div>
    </div>

    <div class="simulation-grid">
      <div>
        <label class="field">当前采集会话<select id="simulationSession" aria-label="选择采集会话"></select></label>
        <div class="tool-row">
          <button id="reloadSimulations" class="secondary" type="button">刷新数据工作区</button>
          <button id="stopSimulation" class="danger" type="button">结束选中会话</button>
        </div>
        <div id="simulationStatus" class="muted" style="margin-top:10px">正在读取采集会话…</div>
      </div>
      <div>
        <div class="label" style="margin-bottom:8px">服务器模型状态（ESP32 负责选择，服务器负责推理）</div>
        <div id="modelCatalog" class="model-list"><div class="muted">正在读取模型…</div></div>
      </div>
    </div>

    <div class="scenario-panel" aria-labelledby="scenarioHeading">
      <div class="section-heading">
        <div><div class="eyebrow">SIMULATED / OPERATOR-SUPPLIED</div><h3 id="scenarioHeading" style="margin:4px 0">配置当前 / 下一次实验的模拟海岸</h3>
          <div class="muted compact">先在这里保存并激活场景，再到 ESP32 点击 START。只有超声波水位由 STM32/ESP32 实测；其余变量全部由操作者输入。</div></div>
        <div class="badges"><span class="badge warn">NOT REAL OBSERVATIONS</span><span class="badge research">EDUCATION ONLY</span></div>
      </div>
      <div class="notice">采集开始时，服务器会把当前激活的场景复制为该会话不可修改的快照。若要更改实验变量，请先修改当前场景，再开始一个新会话；没有激活场景时，第三模型实时推理必须拒绝运行。</div>
      <div class="scenario-grid">
        <label class="field span-2">模拟场景名称<input id="scenarioName" maxlength="80" placeholder="例如：SIM COAST · FAST RISE · STRONG WIND"></label>
        <label class="field span-2">模拟观测时间（含本机时区后上传）<input id="scenarioSimulatedAt" type="datetime-local" step="1"></label>
        <label class="field">虚拟纬度 °<input id="scenarioLatitude" type="number" min="-90" max="90" step="0.000001" placeholder="50.120000"></label>
        <label class="field">虚拟经度 °<input id="scenarioLongitude" type="number" min="-180" max="180" step="0.000001" placeholder="-4.130000"></label>
        <label class="field">模拟气温 °C<input id="scenarioAirTemperature" type="number" min="-80" max="60" step="0.1" placeholder="14.0"></label>
        <label class="field">模拟湿度 %<input id="scenarioHumidity" type="number" min="0" max="100" step="0.1" placeholder="78"></label>
        <label class="field">模拟风速 km/h<input id="scenarioWindSpeed" type="number" min="0" max="400" step="0.1" placeholder="35.0"></label>
        <label class="field">模拟浪高 m<input id="scenarioWaveHeight" type="number" min="0" max="40" step="0.01" placeholder="2.50"></label>
        <label class="field">模拟浪周期 s<input id="scenarioWavePeriod" type="number" min="0.1" max="60" step="0.1" placeholder="8.0"></label>
        <label class="field">模拟水温 °C<input id="scenarioWaterTemperature" type="number" min="-5" max="45" step="0.1" placeholder="12.0"></label>
        <label class="field">模拟海平面高度 m<input id="scenarioSeaLevel" type="number" min="-20" max="20" step="0.001" placeholder="0.650"></label>
        <label class="field">模拟海流速度 km/h<input id="scenarioCurrentVelocity" type="number" min="0" max="50" step="0.01" placeholder="1.20"></label>
        <label class="field span-2">操作者说明（可选）<input id="scenarioNote" maxlength="500" placeholder="说明这个虚构场景如何设置，以及为什么标为 SAFE 或 DANGER"></label>
      </div>
      <div class="tool-row">
        <button id="saveScenario" type="button">保存当前场景</button>
        <button id="clearScenarioForm" class="secondary" type="button">清空表单</button>
        <button id="deleteDeviceScenario" class="danger" type="button" disabled>停用并清除当前场景</button>
      </div>
      <details style="margin-top:12px"><summary>从单行 JSON 或两行 CSV 填充表单（不会自动保存）</summary>
        <div class="scenario-import"><label class="field">粘贴对象 JSON，或“表头 + 一行数据”的 CSV<textarea id="scenarioImport" spellcheck="false" placeholder='{"scenario_name":"SIM COAST A","simulated_at":"2026-08-16T12:00:00Z","sim_air_temperature_c":14,"sim_humidity_percent":78,...}'></textarea></label>
          <button id="importScenario" class="secondary" type="button">解析并填充</button></div>
      </details>
      <div id="scenarioStatus" class="muted" style="margin-top:10px" aria-live="polite">正在读取当前模拟场景…</div>
      <div id="scenarioProvenance" class="scenario-provenance" aria-label="模拟场景来源">
        <span class="badge warn">SIMULATED</span><span class="badge">环境来源：OPERATOR-SUPPLIED</span><span class="badge">水位来源：DEVICE-MEASURED</span><span id="scenarioPredictionContext" class="badge danger">CUSTOM PREDICTION BLOCKED · NO FROZEN SCENARIO</span>
      </div>
      <details style="margin-top:12px"><summary>第三模型为什么是 22 个特征？</summary>
        <div class="feature-groups">
          <div class="feature-group"><strong>8 项设备实测水位特征</strong>当前距离、基准距离、当前水位上升、当前变化速度、水位差分、窗口斜率、窗口均值、窗口标准差。</div>
          <div class="feature-group"><strong>8 项操作者输入模拟环境</strong>气温、湿度、风速、浪高、浪周期、水温、海平面高度、海流速度。</div>
          <div class="feature-group"><strong>6 项模拟时空上下文</strong>虚拟纬度、虚拟经度，以及由模拟时间在服务器生成的小时 sin/cos、年内日期 sin/cos。</div>
        </div>
        <div class="table-note">合计 8 + 8 + 6 = 22 项。环境数值和标签均由操作者构造；训练结果只验证机器学习流程，不证明真实自然灾害预测能力。有效样本量首先看独立会话数，而不是每 500 ms 产生的相邻数据行数。</div>
      </details>
      <div class="split-details" style="margin-top:12px"><strong>选中会话的冻结场景快照</strong><div id="selectedScenarioSnapshot" style="margin-top:6px">尚未选择采集会话。</div></div>
    </div>
    <div class="training-selection-toolbar" aria-label="训练数据选择">
      <div><strong>选择用于训练的已结束会话</strong><div id="trainingSelectionCount" class="muted compact">已选择 0 个；不会默认使用全部数据。</div></div>
      <div class="tool-row"><button id="selectAllTrainingSessions" class="secondary compact" type="button">全选已结束会话</button><button id="clearTrainingSessionSelection" class="secondary compact" type="button">清空训练选择</button></div>
    </div>
    <div id="simulationSessionList" class="session-list" aria-label="采集会话列表"></div>
    <div id="sessionDeletionHelp" class="table-note">只能删除已结束且尚未被任何训练模型工件使用的会话；已被训练工件使用的会话为保证模型可追溯性不可删除。</div>
    <div id="sessionDeletionStatus" class="muted compact" style="margin-top:7px;min-height:1.5em" role="status" aria-live="polite"></div>

    <div class="chart-shell">
      <div class="chart-toolbar">
        <div><strong>会话时间线</strong><div class="muted compact" id="chartCaption">请选择会话查看真实采样曲线</div></div>
        <div class="chart-legend">
          <span class="legend-key"><i class="legend-dot" style="background:#4bd6ff"></i>距离 mm</span>
          <span class="legend-key"><i class="legend-dot" style="background:#29d391"></i>水位上升 mm</span>
          <span class="legend-key"><i class="legend-dot" style="background:#ffb84d"></i>变化速度 mm/s</span>
          <span class="legend-key"><i class="legend-dot" style="background:#ff5b61"></i>DANGER 标注区间</span>
        </div>
      </div>
      <div class="chart-scroll"><svg id="simulationChart" viewBox="0 0 1000 382" role="img" aria-label="超声波模拟会话距离、水位上升和变化速度曲线"></svg></div>
      <div class="sticky-actions"><div class="chart-help">在曲线上点击两次可依次设置标注起点和终点；选择范围只是待提交草稿。</div>
        <button id="clearSelection" class="secondary compact" type="button">清空选区</button></div>
    </div>

    <div class="quality-grid">
      <div class="quality-panel"><h3>传感器与数据质量</h3>
        <div class="quality-row"><span>有效超声波样本</span><div class="bar"><span id="qualityValidBar" style="width:0"></span></div><strong id="qualityValidText">--</strong></div>
        <div class="quality-row"><span>无效/排除样本</span><div class="bar"><span id="qualityInvalidBar" style="width:0;background:var(--fault)"></span></div><strong id="qualityInvalidText">--</strong></div>
        <div class="quality-row"><span>标签覆盖</span><div class="bar"><span id="qualityCoverageBar" style="width:0;background:var(--safe)"></span></div><strong id="qualityCoverageText">--</strong></div>
        <div id="qualityDetails" class="table-note">由服务器根据健康位、有效距离与标签记录统计。</div>
      </div>
      <div class="quality-panel"><h3>当前会话标签分布</h3>
        <div class="coverage" aria-label="SAFE DANGER UNKNOWN 标签覆盖"><span id="coverageSafe" class="safe"></span><span id="coverageDanger" class="danger"></span><span id="coverageUnknown" class="unknown"></span></div>
        <div class="coverage-copy"><span>SAFE <strong id="coverageSafeCount">--</strong></span><span>DANGER <strong id="coverageDangerCount">--</strong></span><span>UNKNOWN <strong id="coverageUnknownCount">--</strong></span></div>
        <div id="coverageNote" class="table-note">未覆盖的样本保持 UNKNOWN，不会被当作 SAFE。</div>
      </div>
    </div>

    <div class="tool-row" aria-label="时间段人工标注">
      <label class="field">起始序号<input id="labelStartSeq" type="number" min="0" step="1" placeholder="点击曲线或样本设为起点"></label>
      <label class="field">结束序号<input id="labelEndSeq" type="number" min="0" step="1" placeholder="再次点击设为终点"></label>
      <label class="field">人工标签<select id="simulationLabel"><option value="safe">安全 SAFE</option><option value="danger">危险 DANGER</option><option value="unknown">清除为 UNKNOWN</option></select></label>
      <label class="field">标签版本<input id="labelVersion" type="number" min="1" step="1" value="1"></label>
      <label class="field wide">标注依据<input id="labelNote" maxlength="500" placeholder="必填建议：观察到的动作，例如快速抬高水面"></label>
      <button id="saveSimulationLabel" type="button">保存人工标签</button>
    </div>
    <div id="labelStatus" class="muted" style="margin-top:10px" aria-live="polite">请先选择已结束的采集会话。</div>

    <details open style="margin-top:14px"><summary>样本明细（最多显示最新 300 条）</summary>
      <div style="overflow:auto;margin-top:10px"><table><thead><tr><th>序号</th><th>时间</th><th>距离</th><th>水位上升</th><th>速度</th><th>数据质量</th><th>当前标签</th><th>选区</th></tr></thead>
        <tbody id="simulationSamples"><tr><td colspan="8" class="muted">尚未选择会话</td></tr></tbody></table></div></details>
    <details style="margin-top:12px"><summary>标签区间审计记录</summary>
      <div style="overflow:auto;margin-top:10px"><table><thead><tr><th>版本</th><th>起始</th><th>结束</th><th>标签</th><th>说明</th><th>更新时间</th></tr></thead>
        <tbody id="simulationLabels"><tr><td colspan="6" class="muted">尚无人工标签</td></tr></tbody></table></div></details>

    <div class="training-panel">
      <div class="section-heading"><div><div class="eyebrow">Leakage-safe evaluation</div><h3>第三模型 · 二分类逻辑回归</h3>
        <div class="muted compact">只使用服务器确认可训练的数据，按完整采集会话划分训练集与测试集。</div></div>
        <button id="trainSimulationModel" type="button" hidden disabled aria-hidden="true">旧版训练已从主流程退役</button></div>
      <div class="split-details"><strong>动态硬阻断与证据等级</strong><br>服务器只对当前勾选的会话做动态切分检查：必须有至少 2 个独立已结束会话，并能形成训练集和测试集都同时含 SAFE/DANGER 的完整会话切分。仅 1 个场景时使用 SINGLE SCENARIO whole-session holdout，只能评估同场景跨采集轮次，不能评估环境效应或跨场景泛化，证据等级最高为 EXPLORATORY；至少 2 个场景时强制 scenario-group holdout 且场景不重叠。原来的 12 会话、240 有效已标注样本、每类 80 样本、每类 6 会话、4 个混合标签会话和 3 个场景仅用于 COURSE DEMO 的建议，不再是固定训练门槛；30 个会话是 STRONGER DEMO 建议。证据等级依次为 BLOCKED / EXPLORATORY / COURSE DEMO / STRONGER DEMO。有效实验样本量以独立会话为主，不把高频相邻行当成独立事件。</div>
      <div id="trainingReadiness" class="readiness blocked" aria-live="polite"><span>●</span><div><strong>正在读取训练条件</strong><div class="compact">不会在浏览器端猜测或放宽训练要求。</div></div></div>
      <div id="trainingResultStatus" class="table-note">这里仅显示服务器实际返回的评估结果；没有训练结果时保持为空。</div>
      <div class="metrics-grid" aria-label="测试集指标">
        <div class="metric-card"><small>Balanced accuracy</small><strong id="metricBalanced">--</strong><small>SAFE 与 DANGER 召回的平均</small></div>
        <div class="metric-card"><small>DANGER precision</small><strong id="metricPrecision">--</strong><small>预测危险中真实危险占比</small></div>
        <div class="metric-card"><small>DANGER recall</small><strong id="metricRecall">--</strong><small>真实危险被检出的占比</small></div>
        <div class="metric-card"><small>DANGER F1</small><strong id="metricF1">--</strong><small>Precision 与 recall 的调和均值</small></div>
        <div class="metric-card"><small>Brier score ↓</small><strong id="metricBrier">--</strong><small>概率误差，越低越好</small></div>
        <div class="metric-card"><small>Log loss ↓</small><strong id="metricLogLoss">--</strong><small>错误且自信时惩罚更大</small></div>
        <div class="metric-card"><small>Specificity</small><strong id="metricSpecificity">--</strong><small>真实 SAFE 被正确识别占比</small></div>
        <div class="metric-card"><small>Negative predictive value</small><strong id="metricNpv">--</strong><small>预测 SAFE 中真实 SAFE 占比</small></div>
        <div class="metric-card"><small>ROC AUC</small><strong id="metricRocAuc">--</strong><small>概率排序能力；单类别测试集时不可用</small></div>
        <div class="metric-card"><small>False-positive rate ↓</small><strong id="metricFpr">--</strong><small>SAFE 被误报 DANGER 的比例</small></div>
        <div class="metric-card"><small>False-negative rate ↓</small><strong id="metricFnr">--</strong><small>DANGER 被漏报 SAFE 的比例</small></div>
        <div class="metric-card"><small>Decision threshold</small><strong id="metricThreshold">--</strong><small>逻辑回归概率分类阈值</small></div>
      </div>
      <div class="evaluation-grid">
        <div><div class="label" style="margin:2px 0 8px">测试集混淆矩阵</div>
          <div class="confusion"><div></div><div class="matrix-head">预测 SAFE</div><div class="matrix-head">预测 DANGER</div>
            <div class="matrix-head">实际 SAFE</div><div id="matrixTrueSafe" class="matrix-cell correct">--</div><div id="matrixFalseDanger" class="matrix-cell error">--</div>
            <div class="matrix-head">实际 DANGER</div><div id="matrixFalseSafe" class="matrix-cell error">--</div><div id="matrixTrueDanger" class="matrix-cell correct">--</div></div></div>
        <div id="trainingSplitDetails" class="split-details"><strong>评估尚未运行</strong><br>训练成功后展示真实的会话隔离、样本量、配置和模型哈希。</div>
      </div>
      <div class="feature-groups" aria-label="模型消融与基线对比">
        <div class="split-details"><strong>Combined Logistic Regression · 22 features</strong><div id="modelComparison">等待服务器模型指标。</div></div>
        <div class="split-details"><strong>Ultrasonic-only Logistic Ablation · 8 features</strong><div id="ablationComparison">Ablation unavailable — 服务器尚未返回超声波单独模型。</div></div>
        <div class="split-details"><strong>Environment-only Logistic Ablation · 14 features</strong><div id="environmentAblationComparison">Ablation unavailable — 若服务器提供环境单独模型，将在这里显示。</div></div>
        <div class="split-details"><strong>Simple Water-rise Threshold Baseline</strong><div id="baselineComparison">Baseline unavailable — 服务器尚未返回阈值对照实验。</div></div>
      </div>
      <div id="comparisonDelta" class="table-note">同一测试会话上的差值将在训练后显示；正值不一定代表所有安全指标都改善。</div>
    </div>
    </details>
  </section>
  <section class="panel"><h2>最近遥测</h2><table><thead><tr><th>接收时间</th><th>序号</th><th>距离</th><th>上升量</th><th>速度</th><th>人员</th><th>报警</th><th>RSSI</th></tr></thead>
    <tbody id="history"><tr><td colspan="8" class="muted">等待数据</td></tr></tbody></table></section>
  <div id="error" class="error"></div>
</main><script>
const DEVICE='COAST_01';
const ADMIN_MODE=__COASTWATCH_ADMIN_MODE__;
const ADMIN_BASE=__COASTWATCH_ADMIN_BASE__;
const alarmNames=['安全','提示','警告','严重危险','传感器故障'];
const healthNames=[['超声波',1],['OpenMV',2],['电源',4],['网络',8]];
let lastEnvironmentFetch=0;
let selectedLocation=null;
let locationPresets=[];
let simulationSessions=[];
let selectedSimulationSession=null;
let simulationOverview=null;
let simulationReadiness=null;
let selectedTrainingSessionIds=new Set();
let trainingReadinessRequestSerial=0;
let pendingSessionDeletionId=null;
let pendingSessionDeletionExpiresAt=0;
let pendingSessionDeletionTimer=null;
let deletingSimulationSessionId=null;
let adminCsrfToken='';
let currentTimeline={session:null,points:[],labels:[]};
let customModelMetadata=null;
let simulationRequestSerial=0;
let currentScenario=null;
let selectedScenarioSnapshotRecord=null;
let officialDatasets=[];
let selectedOfficialDataset=null;
let officialReadiness=null;
let officialTrainingRuns=[];
let selectedOfficialRun=null;
let activeOfficialModel=null;
let frozenSensorProfile=null;
let sensorTestRuns=[];
let latestTelemetry=null;
const scenarioFields={
  scenario_name:'scenarioName',simulated_at:'scenarioSimulatedAt',sim_latitude:'scenarioLatitude',
  sim_longitude:'scenarioLongitude',sim_air_temperature_c:'scenarioAirTemperature',
  sim_humidity_percent:'scenarioHumidity',sim_wind_speed_kmh:'scenarioWindSpeed',
  sim_wave_height_m:'scenarioWaveHeight',sim_wave_period_s:'scenarioWavePeriod',
  sim_water_temperature_c:'scenarioWaterTemperature',sim_sea_level_height_m:'scenarioSeaLevel',
  sim_ocean_current_velocity_kmh:'scenarioCurrentVelocity',note:'scenarioNote'
};
const $=id=>document.getElementById(id);
function chooseLocation(location){
  selectedLocation=location;
  $('displayLocation').value=location.display_location || 'COAST STATION';
  $('locationStatus').textContent=`待保存：${location.location}（${Number(location.latitude).toFixed(4)}, ${Number(location.longitude).toFixed(4)}）`;
}
async function loadLocationConfig(){
  try {
    locationPresets=await fetchJson('/api/v1/locations/presets');
    $('locationPreset').replaceChildren(...locationPresets.map((item,index)=>{
      const option=document.createElement('option'); option.value=String(index); option.textContent=item.location; return option;
    }));
    $('locationPreset').addEventListener('change',event=>chooseLocation(locationPresets[Number(event.target.value)]));
    if(locationPresets.length) chooseLocation(locationPresets[0]);
    const current=await fetchOptionalJson(`/api/v1/device-location?device_id=${DEVICE}`);
    if(current){
      const location=current; selectedLocation=location;
      $('displayLocation').value=location.display_location;
      $('locationStatus').textContent=`当前：${location.location} · 屏幕显示 ${location.display_location}`;
    } else {
      $('locationStatus').textContent='尚未保存地区；当前默认候选为青岛海岸';
    }
  } catch(error){ $('error').textContent=error; }
}
async function searchLocations(){
  const query=$('locationQuery').value.trim();
  if(query.length<2){ $('locationStatus').textContent='请输入至少 2 个字的地区名称'; return; }
  $('locationStatus').textContent='正在搜索地区…'; $('locationResults').replaceChildren();
  try {
    const results=await fetchJson(`/api/v1/locations/search?q=${encodeURIComponent(query)}&count=8`);
    const buttons=results.map(item=>{
      const parts=[item.name,item.admin1,item.admin2,item.country].filter((value,index,array)=>value&&array.indexOf(value)===index);
      const locationLabel=parts.join(' · ') || item.location;
      const population=Number.isFinite(item.population)&&item.population>0?` · ${item.population.toLocaleString()} 人`:'';
      const button=document.createElement('button'); button.type='button';
      button.textContent=`${locationLabel}${population} · ${item.display_location}`;
      button.addEventListener('click',()=>chooseLocation({...item,location:locationLabel})); return button;
    });
    $('locationResults').replaceChildren(...buttons);
    $('locationStatus').textContent=results.length?'请选择搜索结果，再点击“保存到设备”':'没有找到匹配地区';
  } catch(error){ $('locationStatus').textContent=String(error); }
}
async function saveLocation(){
  if(!selectedLocation){ $('locationStatus').textContent='请先选择一个地区'; return; }
  const displayLocation=$('displayLocation').value.trim().toUpperCase();
  if(!/^[A-Z0-9 ._-]{1,32}$/.test(displayLocation)){
    $('locationStatus').textContent='液晶短名只能用 1–32 个英文字母、数字、空格、点、横线或下划线'; return;
  }
  const payload={device_id:DEVICE,location:selectedLocation.location,display_location:displayLocation,
    kind:selectedLocation.kind || 'place',latitude:selectedLocation.latitude,
    longitude:selectedLocation.longitude};
  try {
    selectedLocation=await sendJson('/api/v1/device-location','PUT',payload);
    $('locationStatus').textContent=`已保存：${selectedLocation.location} · 屏幕显示 ${selectedLocation.display_location}`;
    lastEnvironmentFetch=0; await refreshEnvironment();
  } catch(error){ $('locationStatus').textContent=String(error); }
}
function alarmName(level){ return alarmNames[level] ?? `未知(${level})`; }
function setLatest(d){
  latestTelemetry=d;
  const age=(Date.now()-new Date(d.received_at).getTime())/1000;
  $('online').textContent=age<=10?'遥测在线':'遥测超时';
  $('online').className='status '+(age<=10?'online':'offline');
  $('alarm').textContent=alarmName(d.alarm_level); $('alarm').dataset.level=d.alarm_level;
  $('distance').textContent=d.distance_mm; $('rise').textContent=d.water_rise_mm;
  $('rate').textContent=d.rise_rate_mm_s; $('person').textContent=d.person_detected?'检测到人员':'无人';
  $('rssi').textContent=d.wifi_rssi; $('sequence').textContent=`#${d.seq} / ${(d.uptime_ms/1000).toFixed(1)} s`;
  $('health').innerHTML=healthNames.map(([name,bit])=>`<span class="chip ${(d.health_flags&bit)?'ok':''}">${name} ${(d.health_flags&bit)?'正常':'异常'}</span>`).join('');
  $('updated').textContent='服务器接收：'+new Date(d.received_at).toLocaleString();
  updateSensorMappingPreview();
}
function setHistory(rows){ $('history').innerHTML=rows.length?rows.map(d=>`<tr><td>${new Date(d.received_at).toLocaleTimeString()}</td><td>${d.seq}</td><td>${d.distance_mm} mm</td><td>${d.water_rise_mm} mm</td><td>${d.rise_rate_mm_s} mm/s</td><td>${d.person_detected?'是':'否'}</td><td>${alarmName(d.alarm_level)}</td><td>${d.wifi_rssi}</td></tr>`).join(''):'<tr><td colspan="8" class="muted">尚无数据</td></tr>'; }
function metric(value,unit,digits=1){ return Number.isFinite(value)?`${Number(value).toFixed(digits)}${unit}`:'--'; }
function setEnvironment(e){
  const source=e.source==='manual'?'SIMULATED · OPERATOR-SUPPLIED':(e.source==='demo'?'演示数据':(e.stale?'缓存数据（已过期）':'Open-Meteo'));
  const parts=[e.location,e.weather,`气温 ${metric(e.air_temperature_c,'℃')}`,`湿度 ${metric(e.humidity_percent,'% ',0).trim()}`,`风 ${metric(e.wind_speed_kmh,' km/h')}`,`浪高 ${metric(e.wave_height_m,' m')}`,`浪周期 ${metric(e.wave_period_s,' s')}`,`海温 ${metric(e.water_temperature_c,'℃')}`,`海平面 ${metric(e.sea_level_height_m,' m',3)}`,e.tide_status,`海流 ${metric(e.ocean_current_velocity_kmh,' km/h')}`,source].filter(Boolean);
  $('environment').textContent=parts.join(' · ');
}
function handleAuthenticationResponse(response){
  if(ADMIN_MODE&&response.status===401) window.location.replace(`${ADMIN_BASE}/login`);
  return response;
}
async function fetchJson(url){ const r=handleAuthenticationResponse(await fetch(url,{cache:'no-store'})); if(!r.ok) throw new Error(`${url} 返回 ${r.status}`); return r.json(); }
async function fetchOptionalJson(url){
  const response=handleAuthenticationResponse(await fetch(url,{cache:'no-store'}));
  if(response.status===404) return null;
  if(!response.ok) throw new Error(`${url} 返回 ${response.status}`);
  return response.json();
}
async function sendJson(url,method,payload){
  const headers={'Content-Type':'application/json'};
  if(ADMIN_MODE) headers['X-CSRF-Token']=adminCsrfToken;
  const options={method,headers};
  if(payload!==undefined) options.body=JSON.stringify(payload);
  const response=handleAuthenticationResponse(await fetch(url,options));
  if(!response.ok){
    let detail='';
    try { const body=await response.json(); detail=body.detail?`: ${body.detail}`:''; } catch(_error) {}
    throw new Error(`${url} 返回 ${response.status}${detail}`);
  }
  return response.status===204?null:response.json();
}
async function loadAdminSession(){
  if(!ADMIN_MODE) return;
  const response=handleAuthenticationResponse(await fetch(`${ADMIN_BASE}/api/auth/session`,{cache:'no-store'}));
  if(!response.ok) throw new Error('管理员会话已失效');
  const session=await response.json(); adminCsrfToken=session.csrf_token;
  $('adminIdentity').textContent=session.username; $('adminControls').style.display='flex';
}
async function logoutAdmin(){
  if(!ADMIN_MODE) return;
  const response=await fetch(`${ADMIN_BASE}/api/auth/logout`,{method:'POST',headers:{'X-CSRF-Token':adminCsrfToken}});
  if(response.ok||response.status===401){ window.location.replace(`${ADMIN_BASE}/login`); return; }
  $('error').textContent=`退出失败：${response.status}`;
}
function addTextCell(row,value,className=''){
  const cell=document.createElement('td'); cell.textContent=String(value ?? '--');
  if(className) cell.className=className; row.appendChild(cell); return cell;
}
function asNumber(value){
  if(value===null||value===undefined||value===''||typeof value==='boolean') return null;
  const number=Number(value); return Number.isFinite(number)?number:null;
}
function formatCount(value){ const number=asNumber(value); return number===null?'--':Math.round(number).toLocaleString(); }
function formatPercent(value,digits=1){ const number=asNumber(value); return number===null?'--':`${(number*100).toFixed(digits)}%`; }
function shortHash(value){ return typeof value==='string'&&value.length>12?`${value.slice(0,12)}…`:value||'--'; }
function normaliseSessionSummary(raw){
  if(!raw||typeof raw!=='object') return raw;
  return raw.session&&typeof raw.session==='object'?{...raw.session,...raw}:raw;
}
function localDatetimeValue(value){
  if(!value) return '';
  const date=new Date(value); if(Number.isNaN(date.getTime())) return '';
  return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,19);
}
function objectArray(payload,...keys){
  if(Array.isArray(payload)) return payload;
  for(const key of keys){ if(Array.isArray(payload?.[key])) return payload[key]; }
  return [];
}
function valueText(value,fallback='—'){
  if(value===null||value===undefined||value==='') return fallback;
  if(Array.isArray(value)) return value.join(', ')||fallback;
  if(typeof value==='object') return JSON.stringify(value);
  return String(value);
}
function metricText(value,digits=3){
  const number=asNumber(value); return number===null?'—':number.toFixed(digits);
}
function statusBox(id,ready,title,detail){
  const box=$(id); if(!box) return;
  box.className=`readiness ${ready?'ready':'blocked'}`;
  const dot=document.createElement('span'); dot.textContent='●';
  const body=document.createElement('div'); const heading=document.createElement('strong'); heading.textContent=title;
  const copy=document.createElement('div'); copy.className='compact'; copy.textContent=detail;
  body.append(heading,copy); box.replaceChildren(dot,body);
}
function siteIdentity(site){
  if(typeof site==='string') return {id:site,label:site};
  const id=site?.site_id||site?.station_id||site?.id||site?.code||site?.name;
  const label=site?.display_name||site?.name||site?.station_name||id;
  return {id:String(id||''),label:String(label||id||'UNKNOWN SITE')};
}
function officialDatasetIdentity(dataset){
  return String(dataset?.dataset_id||dataset?.id||dataset?.version_id||'');
}
function selectedOfficialSiteIds(){
  return Array.from($('officialSites').selectedOptions).map(option=>option.value).filter(Boolean);
}
function renderOfficialEvidenceScope(){
  const count=selectedOfficialSiteIds().length;
  let tier='尚未选择站点',scope='不会在界面端自行解除服务器训练阻断。';
  if(count===1){ tier='1 站点 · EXPLORATORY / SINGLE-COAST · NOT ACTIVATABLE'; scope='仅支持单海岸时间泛化的有限声明；服务器策略要求至少 3 站点才可激活。'; }
  else if(count===2){ tier='2 站点 · PRELIMINARY · NOT ACTIVATABLE'; scope='可做初步跨站点对比，但服务器策略要求至少 3 站点才可激活。'; }
  else if(count>=3){ tier=`${count} 站点 · COURSE DEMO / MULTI-COAST`; scope='可用于课程级多站点演示；科学声明仍受站点、年份和标签定义限制。'; }
  $('officialEvidenceScope').textContent=`${tier} · ${scope} 训练是否可运行由服务器 readiness 决定；激活还要求每个 split 至少 200 行且 site-macro 覆盖完整。浏览器只显示服务器策略，不自行放宽门槛。`;
}
function splitBoundary(dataset,splitName,boundary){
  const split=dataset?.splits?.[splitName]||dataset?.split?.[splitName]||dataset?.[`${splitName}_split`]||{};
  return split?.[boundary]||split?.[`${boundary}_at`]||dataset?.[`${splitName}_${boundary}`]||'';
}
function renderOfficialDataset(dataset){
  selectedOfficialDataset=dataset;
  const sites=objectArray(dataset?.sites||dataset?.site_ids,'items','stations').map(siteIdentity).filter(site=>site.id);
  const previousSites=new Set(selectedOfficialSiteIds());
  $('officialSites').replaceChildren(...sites.map(site=>{
    const option=document.createElement('option'); option.value=site.id; option.textContent=site.label;
    option.selected=previousSites.size?previousSites.has(site.id):true; return option;
  }));
  $('sensorStation').replaceChildren(...sites.map(site=>{
    const option=document.createElement('option'); option.value=site.id; option.textContent=site.label; return option;
  }));
  if(!sites.length){
    const option=document.createElement('option'); option.value=''; option.textContent='该 manifest 没有可用站点';
    $('officialSites').replaceChildren(option); $('sensorStation').replaceChildren(option.cloneNode(true));
  }
  renderOfficialEvidenceScope();
  const source=dataset?.source||dataset?.sources||dataset?.source_manifest?.sources||dataset?.provenance?.sources||dataset?.data_sources;
  const sourceLicenses=Array.isArray(source)?source.map(item=>item?.license||item?.licence||item?.terms).filter(Boolean):[];
  const license=dataset?.license||dataset?.licence||dataset?.source_manifest?.license||dataset?.source_manifest?.licence||dataset?.provenance?.license||dataset?.provenance?.licence||sourceLicenses;
  const hash=dataset?.sha256||dataset?.dataset_sha256||dataset?.dataset_hash||dataset?.hash||dataset?.manifest_sha256||dataset?.manifest_hash;
  const dateRange=dataset?.date_range||dataset?.coverage?.date_range||{};
  const start=dateRange?.start||dateRange?.start_at||dataset?.date_start||dataset?.start_at;
  const end=dateRange?.end||dateRange?.end_at||dataset?.date_end||dataset?.end_at;
  const rows=dataset?.row_count??dataset?.rows??dataset?.sample_count;
  $('officialSource').textContent=valueText(source);
  $('officialLicense').textContent=valueText(license);
  $('officialDatasetHash').textContent=valueText(hash);
  $('officialCoverage').textContent=`${sites.length} 站点 · ${valueText(start)} → ${valueText(end)} · ${formatCount(rows)} 行`;
  $('officialLabelDefinition').textContent=valueText(dataset?.label_definition||dataset?.source_manifest?.label_definition||dataset?.target_definition||dataset?.target?.definition);
  const gap=dataset?.leakage_gap||dataset?.splits?.leakage_gap||dataset?.source_manifest?.splits?.leakage_gap||dataset?.split?.leakage_gap;
  $('officialSplitDefinition').textContent=`TRAIN → VALIDATION → FROZEN TEST · leakage gap ${valueText(gap)}`;
  const provenanceAssurance=dataset?.provenance_assurance||dataset?.source_manifest?.provenance_assurance||'NOT REPORTED';
  const importerReplay=dataset?.deterministic_importer_replay_verified??dataset?.source_manifest?.deterministic_importer_replay_verified;
  $('officialDatasetProvenance').textContent=`${provenanceAssurance} · SERVER VERIFIED: raw bytes / SHA-256 / manifest structure`;
  $('officialDatasetImporterReplay').textContent=`deterministic_importer_replay_verified=${importerReplay===true?'true':'false'} · operator-attested: official ownership, licence, harmonisation and label derivation`;
  const dateInputs=[
    ['officialTrainStart','train','start'],['officialTrainEnd','train','end'],
    ['officialValidationStart','validation','start'],['officialValidationEnd','validation','end'],
    ['officialTestStart','frozen_test','start'],['officialTestEnd','frozen_test','end']
  ];
  dateInputs.forEach(([id,split,boundary])=>{ $(id).value=localDatetimeValue(splitBoundary(dataset,split,boundary)); });
  $('officialDatasetStatus').textContent=`${dataset?.display_name||dataset?.name||officialDatasetIdentity(dataset)} · manifest 时间切分只读，界面不能调整。`;
  if(activeOfficialModel) populateFrozenSensorContexts();
  updateSensorProfileControls();
}
async function loadOfficialDatasets({preserveSelection=true}={}){
  const previous=preserveSelection?$('officialDataset').value:'';
  $('officialDatasetStatus').textContent='正在读取已注册英国官方数据集…';
  try {
    const payload=await fetchJson('/api/v1/official-datasets');
    officialDatasets=objectArray(payload,'datasets','items','results');
    const options=officialDatasets.map(dataset=>{
      const option=document.createElement('option'); option.value=officialDatasetIdentity(dataset);
      option.textContent=dataset.display_name||dataset.name||option.value; return option;
    });
    $('officialDataset').replaceChildren(...options);
    if(!officialDatasets.length){
      const option=document.createElement('option'); option.value=''; option.textContent='未找到已注册官方数据集'; $('officialDataset').appendChild(option);
      selectedOfficialDataset=null; renderOfficialReadiness(null);
      $('officialDatasetStatus').textContent='受保护数据目录中尚无通过完整性检查的官方 manifest；不会使用 Open-Meteo 或人工模拟数据替代。';
      return;
    }
    if(previous&&officialDatasets.some(dataset=>officialDatasetIdentity(dataset)===previous)) $('officialDataset').value=previous;
    await loadSelectedOfficialDataset();
  } catch(error){
    officialDatasets=[]; selectedOfficialDataset=null; $('officialDatasetStatus').textContent=String(error);
    renderOfficialReadiness(null);
  }
}
async function rescanOfficialDatasets(){
  const button=$('rescanOfficialDatasets'); button.disabled=true; button.textContent='扫描中…';
  const previousSelection=$('officialDataset').value;
  statusBox('officialRescanStatus',false,'正在扫描','服务器正在逐个验证受保护目录中的 manifest、raw 文件和 immutable dataset id。');
  try {
    const scan=await sendJson('/api/v1/official-datasets/rescan','POST',undefined);
    const errors=objectArray(scan,'errors');
    const errorCount=asNumber(scan?.error_count)??errors.length;
    const registeredCount=asNumber(scan?.registered_count);
    await loadOfficialDatasets({preserveSelection:true});
    const retained=Boolean(previousSelection&&$('officialDataset').value===previousSelection);
    const selectionNote=previousSelection?(retained?`原选择 ${previousSelection} 已明确保留。`:`原选择 ${previousSelection} 已不在注册表中。`):'扫描前没有选择数据集。';
    const bundleErrors=errors.map(item=>`${valueText(item?.bundle,'UNKNOWN BUNDLE')}: ${valueText(item?.detail||item?.message,'未提供详情')}`).join(' | ');
    const fullyAccepted=errorCount===0;
    statusBox('officialRescanStatus',fullyAccepted,fullyAccepted?'扫描完成 · 所有发现的数据包均已接受':`扫描完成但有 ${formatCount(errorCount)} 个数据包被拒绝`,`${formatCount(registeredCount)} 个数据包在本次扫描中注册。${selectionNote}${bundleErrors?` Bundle errors: ${bundleErrors}`:''}`);
  }
  catch(error){ statusBox('officialRescanStatus',false,'扫描请求失败',String(error)); $('officialDatasetStatus').textContent=String(error); }
  finally { button.disabled=false; button.textContent='重新扫描受保护数据目录'; }
}
async function loadSelectedOfficialDataset(){
  const datasetId=$('officialDataset').value;
  if(!datasetId){ selectedOfficialDataset=null; renderOfficialReadiness(null); return; }
  try {
    const detail=await fetchJson(`/api/v1/official-datasets/${encodeURIComponent(datasetId)}`);
    renderOfficialDataset(detail?.dataset||detail); await loadOfficialTrainingReadiness();
  } catch(error){ $('officialDatasetStatus').textContent=String(error); renderOfficialReadiness(null); }
}
function officialReadinessUrl(){
  const params=new URLSearchParams({dataset_id:$('officialDataset').value});
  selectedOfficialSiteIds().forEach(siteId=>params.append('site_id',siteId));
  return `/api/v1/official-training/readiness?${params.toString()}`;
}
function renderOfficialReadiness(readiness){
  officialReadiness=readiness; const ready=Boolean(readiness?.ready);
  const activationReady=readiness?.activation_ready===true;
  const blockers=objectArray(readiness,'blockers','errors','reasons').map(item=>typeof item==='string'?item:(item?.message||item?.code||JSON.stringify(item)));
  const warnings=[...objectArray(readiness,'warnings','evidence_warnings'),...objectArray(readiness,'activation_blockers')].map(item=>typeof item==='string'?item:(item?.message||item?.code||JSON.stringify(item)));
  const assurance=readiness?.provenance_assurance||'NOT REPORTED';
  const replay=readiness?.deterministic_importer_replay_verified===true;
  const verifiedBoundary=`${assurance} · 服务器验证 raw 字节/SHA-256、manifest 结构、双类标签形态、时间切分和 leakage gap；官方归属、许可、harmonisation 与标签派生由操作者声明；deterministic_importer_replay_verified=${replay?'true':'false'}。`;
  const readinessTitle=ready?(activationReady?'官方训练与激活证据条件已通过':'可以训练，但当前结果不可激活'):'训练被阻断';
  const readinessDetail=ready?`${verifiedBoundary}${activationReady?'':' 激活仍被服务器策略阻断：至少 3 站点、每个 split 至少 200 行、每个选中站点冻结测试双类齐全。'}`:(blockers[0]||'请先选择有效官方数据集和站点。');
  statusBox('officialReadiness',ready,readinessTitle,readinessDetail);
  const provenanceWarning=`Provenance：${verifiedBoundary}`;
  const listItems=[...blockers.map(text=>`阻断：${text}`),...warnings.map(text=>`提醒：${text}`),provenanceWarning].map(text=>{ const item=document.createElement('li'); item.textContent=text; return item; });
  $('officialBlockers').replaceChildren(...listItems);
  $('trainOfficialModel').disabled=!ready;
  const contract=readiness?.data_contract||{};
  $('officialLeakageInvariant').textContent=`SENSOR ROWS USED FOR FIT = ${contract.sensor_rows_used_for_fit??readiness?.sensor_rows_used_for_fit??0} · SCALER = ${contract.sensor_rows_used_for_scaler??readiness?.sensor_rows_used_for_scaler??0} · THRESHOLD = ${contract.sensor_rows_used_for_threshold??readiness?.sensor_rows_used_for_threshold??0}`;
}
async function loadOfficialTrainingReadiness(){
  if(!$('officialDataset').value||!selectedOfficialSiteIds().length){ renderOfficialReadiness(null); return; }
  statusBox('officialReadiness',false,'正在检查','服务器正在验证官方 manifest 和防泄漏条件。');
  try { renderOfficialReadiness(await fetchJson(officialReadinessUrl())); }
  catch(error){ renderOfficialReadiness({ready:false,blockers:[String(error)]}); }
}
function officialTrainingPayload(){
  const payload={dataset_id:$('officialDataset').value}; const sites=selectedOfficialSiteIds();
  if(sites.length) payload.selected_site_ids=sites; return payload;
}
async function trainOfficialModel(){
  if(!officialReadiness?.ready) return;
  const button=$('trainOfficialModel'); button.disabled=true; button.textContent='服务器训练中…';
  statusBox('officialRunStatus',false,'训练运行已提交','正在拟合官方训练集、使用官方验证集选阈值，然后一次性计算冻结测试指标。');
  try {
    const run=await sendJson('/api/v1/official-training/runs','POST',officialTrainingPayload());
    renderOfficialRun(run?.run||run); await loadOfficialTrainingRuns();
  } catch(error){ statusBox('officialRunStatus',false,'训练失败',String(error)); }
  finally { button.textContent='手动训练官方模型'; button.disabled=!officialReadiness?.ready; }
}
function officialRunId(run){ return String(run?.run_id||run?.id||''); }
function officialRunMetrics(run){
  return run?.frozen_test_metrics||run?.metrics?.frozen_test||run?.metrics?.test||run?.metrics||{};
}
function renderOfficialBaselineVerdict(run){
  const comparison=run?.metrics?.delta_vs_water_level_threshold||run?.delta_vs_water_level_threshold;
  const box=$('officialBaselineVerdict');
  if(!comparison){
    box.textContent='教授问题：服务器尚未返回 delta_vs_water_level_threshold，界面不自行发明“机器学习更好”的结论。硬阈值没有概率输出，不比较 Brier / ROC AUC / PR AUC。'; return;
  }
  if(comparison.available===false||!comparison.verdict){
    box.textContent=`教授问题结论（服务器）：N/A — 当前没有覆盖全部选中站点的公平 site-macro 模型对阈值结论。${comparison.professor_summary?` ${comparison.professor_summary}`:''} 界面不会用 row-level 或 eligible-subset 指标代替主结论；硬阈值没有概率输出，不比较 Brier / ROC AUC / PR AUC。`; return;
  }
  const rawVerdict=String(comparison.verdict||run?.metrics?.baseline_verdict||'').toLowerCase();
  const improves=['ml_improves_baseline','improves_baseline','ml_improves','outperforms_threshold_on_comparable_frozen_test_metrics'].includes(rawVerdict);
  const verdict=improves?'ML improves baseline':'No demonstrated improvement; prefer simple rule';
  const labels={balanced_accuracy:'balanced accuracy',precision:'precision',recall:'recall',f1:'F1',specificity:'specificity',false_positive_rows_per_day:'false-positive rows/day'};
  const comparable=comparison.comparable_metric_deltas_model_minus_threshold||comparison;
  const deltas=Object.entries(labels).filter(([key])=>asNumber(comparable[key])!==null).map(([key,label])=>`${label} ${Number(comparable[key])>=0?'+':''}${Number(comparable[key]).toFixed(3)}`);
  box.textContent=`教授问题结论（服务器）：${verdict}。${comparison.professor_summary?` ${comparison.professor_summary}`:''}${deltas.length?` 同一冻结测试集 classification-only 差值：${deltas.join(' · ')}。`:''} 不将 Brier / ROC AUC / PR AUC 与硬阈值对比。`;
}
function renderOfficialRun(run){
  selectedOfficialRun=run||null;
  if(!run){
    renderOfficialBaselineVerdict(null);
    statusBox('officialRunStatus',false,'尚未训练','只有成功且通过官方冻结测试的运行可被激活为 Shadow。');
    $('activateOfficialRun').disabled=true;
    ['officialMetricPRAuc','officialMetricRecall','officialMetricPrecision','officialMetricF1','officialMetricRocAuc','officialMetricBrier','officialMetricFalsePositiveRows','officialMetricThreshold','officialMetricSiteCoverage'].forEach(id=>$(id).textContent='—');
    $('officialModelSummary').textContent='等待冻结测试指标。';
    $('officialRunProvenance').textContent='尚无训练运行。';
    $('officialArtifactProvenance').textContent='尚无工件。';
    return;
  }
  const status=String(run.status||run.state||'unknown').toLowerCase();
  const succeeded=['completed','succeeded','ready','trained','active'].includes(status);
  const metrics=officialRunMetrics(run);
  const perSite=run.metrics?.per_site_frozen_test||{};
  const selectedSiteCount=asNumber(perSite.selected_site_count);
  const eligibleSiteCount=asNumber(perSite.eligible_site_count);
  const macroCandidate=perSite.macro_average;
  const completeMacroCoverage=perSite.complete_coverage===true&&macroCandidate&&selectedSiteCount!==null&&selectedSiteCount>0&&eligibleSiteCount===selectedSiteCount;
  const macro=completeMacroCoverage?macroCandidate:null;
  const macroMetric=name=>macro?metricText(macro[name]):'N/A';
  const siteCoverage=`${eligibleSiteCount===null?'N/A':formatCount(eligibleSiteCount)} / ${selectedSiteCount===null?'N/A':formatCount(selectedSiteCount)} eligible / selected`;
  const activatable=run.activatable!==false&&completeMacroCoverage;
  const activationBlockers=objectArray(run,'activation_blockers').map(value=>typeof value==='string'?value:JSON.stringify(value));
  if(!completeMacroCoverage) activationBlockers.push(`site-macro unavailable: ${siteCoverage}`);
  const runDetail=succeeded
    ?(activatable?(run.message||run.detail||'已生成带哈希且 site-macro 覆盖完整的官方模型工件。'):`训练已完成，但证据等级不足以激活为 Shadow。 ${activationBlockers.join(' · ')}`)
    :(run.message||run.detail||'运行尚未完成或已被阻断。');
  statusBox('officialRunStatus',succeeded&&activatable,`${officialRunId(run)||'运行'} · ${status.toUpperCase()}`,runDetail);
  $('activateOfficialRun').disabled=!succeeded||!activatable||Boolean(run.active||run.activated||run.activated_at||run.active_since);
  renderOfficialBaselineVerdict(run);
  $('officialMetricPRAuc').textContent=macroMetric('pr_auc');
  $('officialMetricRecall').textContent=macroMetric('recall');
  $('officialMetricPrecision').textContent=macroMetric('precision');
  $('officialMetricF1').textContent=macroMetric('f1');
  $('officialMetricRocAuc').textContent=macroMetric('roc_auc');
  $('officialMetricBrier').textContent=metricText(metrics.brier_score??metrics.brier);
  $('officialMetricFalsePositiveRows').textContent=metricText(metrics.false_positive_rows_per_day);
  $('officialMetricThreshold').textContent=metricText(run.decision_threshold??metrics.decision_threshold);
  $('officialMetricSiteCoverage').textContent=siteCoverage;
  const baselines=run.baselines||run.metrics?.baselines||metrics.baselines||{};
  const threshold=baselines.water_level_threshold||baselines.threshold||{};
  const persistence=baselines.observable_water_level_persistence||baselines.persistence||{};
  const thresholdMetrics=threshold.frozen_test||threshold;
  const persistenceMetrics=persistence.frozen_test||persistence;
  const rowLevelPRAuc=metrics.pr_auc??metrics.average_precision;
  $('officialModelSummary').textContent=completeMacroCoverage
    ?`site-macro (PRIMARY) PR AUC ${macroMetric('pr_auc')} · recall ${macroMetric('recall')} · F1 ${macroMetric('f1')} · coverage ${siteCoverage} · row-level companion PR AUC ${metricText(rowLevelPRAuc)} · Brier ${metricText(metrics.brier_score??metrics.brier)}`
    :`site-macro (PRIMARY) N/A · coverage ${siteCoverage}; not every selected site has both classes in frozen test. Row-level companion (NOT PRIMARY) PR AUC ${metricText(rowLevelPRAuc)} · Brier ${metricText(metrics.brier_score??metrics.brier)}.`;
  const thresholdSelectionSplit=threshold.threshold_selection_split||threshold.threshold_selected_on||'NOT REPORTED';
  const perSiteThresholds=threshold.per_site_thresholds_m||threshold.thresholds_by_site||threshold.per_site_thresholds||{};
  const thresholdPerSiteMetrics=threshold.per_site_frozen_test||threshold.per_site_metrics||{};
  const thresholdCoverageSource=threshold.selected_site_coverage||thresholdPerSiteMetrics;
  const thresholdMacroCandidate=thresholdPerSiteMetrics.macro_average||threshold.site_macro;
  const thresholdCompleteCoverage=thresholdPerSiteMetrics.complete_coverage===true&&Boolean(thresholdMacroCandidate);
  const thresholdMacro=thresholdCompleteCoverage?thresholdMacroCandidate:null;
  const thresholdSelectedSites=asNumber(thresholdCoverageSource.selected_site_count??threshold.selected_site_count);
  const thresholdEligibleSites=asNumber(thresholdCoverageSource.eligible_site_count??threshold.eligible_site_count);
  const thresholdCoverage=`${thresholdEligibleSites===null?'N/A':formatCount(thresholdEligibleSites)} / ${thresholdSelectedSites===null?'N/A':formatCount(thresholdSelectedSites)} eligible / selected`;
  const thresholdList=Object.entries(perSiteThresholds).map(([siteId,value])=>`${siteId}=${metricText(value&&typeof value==='object'?(value.threshold_m??value.threshold):value,4)} m`).join(', ');
  $('officialThresholdBaseline').textContent=Object.keys(threshold).length&&threshold.available!==false?`per-site hard classifier · threshold_selection_split=${thresholdSelectionSplit} · thresholds ${thresholdList||'NOT REPORTED'} · coverage ${thresholdCoverage} · site-macro recall ${thresholdMacro?metricText(thresholdMacro.recall):'N/A'} · F1 ${thresholdMacro?metricText(thresholdMacro.f1):'N/A'} · row-level companion false-positive rows/day ${metricText(thresholdMetrics.false_positive_rows_per_day)}`:`Baseline unavailable · ${threshold.reason||'服务器未返回可比较阈值结果'}。`;
  $('officialPersistenceBaseline').textContent=Object.keys(persistence).length&&persistence.available!==false?`hard classifier · recall ${metricText(persistenceMetrics.danger_recall??persistenceMetrics.recall)} · F1 ${metricText(persistenceMetrics.danger_f1??persistenceMetrics.f1)} · false-positive rows/day ${metricText(persistenceMetrics.false_positive_rows_per_day)}`:`Baseline unavailable · ${persistence.reason||'服务器未返回可观测持续性基线'}。`;
  const contract=run.data_contract||{};
  const assurance=run.provenance_assurance||run.source_manifest?.provenance_assurance||'NOT REPORTED';
  const importerReplay=run.deterministic_importer_replay_verified??run.source_manifest?.deterministic_importer_replay_verified;
  const provenanceLimitation=run.source_manifest?.provenance_limitation||'Raw archive bytes and hashes are server-verified; official ownership, licence, harmonisation and target derivation remain operator-attested and were not independently replayed.';
  $('officialRunProvenance').textContent=`run ${officialRunId(run)||'—'} · dataset ${run.dataset_id||run.source_manifest?.dataset_id||'—'} · selected sites ${valueText(run.selected_site_ids||run.source_manifest?.site_ids)} · provenance_assurance=${assurance} · deterministic_importer_replay_verified=${importerReplay===true?'true':'false'} · ${provenanceLimitation}`;
  $('officialArtifactProvenance').textContent=`model ${run.model_id||'uk-official-coast-logreg-v2'} · artifact ${shortHash(run.artifact_sha256||run.artifact_hash||run.model_hash)} · dataset registration ${shortHash(run.source_manifest?.dataset_registration_sha256||run.dataset_hash)} · provenance_assurance=${assurance} · deterministic_importer_replay_verified=${importerReplay===true?'true':'false'} · ${formatCount(contract.sensor_rows_used_for_fit??run.sensor_rows_used_for_fit??0)} sensor fit rows · ${valueText(run.finished_at||run.created_at||run.started_at)}`;
  Array.from($('officialRunHistory').querySelectorAll('button')).forEach(button=>button.setAttribute('aria-current',String(button.dataset.runId===officialRunId(run))));
}
async function selectOfficialRun(runId){
  try { renderOfficialRun(await fetchJson(`/api/v1/official-training/runs/${encodeURIComponent(runId)}`)); }
  catch(error){ statusBox('officialRunStatus',false,'无法读取运行',String(error)); }
}
function renderOfficialRunHistory(){
  const nodes=officialTrainingRuns.map(run=>{
    const button=document.createElement('button'); button.type='button'; button.className='run-button'; button.dataset.runId=officialRunId(run);
    const status=String(run.status||run.state||'unknown').toUpperCase();
    button.textContent=`${officialRunId(run)||'未命名运行'} · ${status} · ${run.dataset_id||'—'} · ${run.created_at?new Date(run.created_at).toLocaleString():'—'}`;
    button.addEventListener('click',()=>selectOfficialRun(officialRunId(run))); return button;
  });
  if(!nodes.length){ const empty=document.createElement('div'); empty.className='muted'; empty.textContent='尚无官方训练运行。'; nodes.push(empty); }
  $('officialRunHistory').replaceChildren(...nodes);
}
async function loadOfficialTrainingRuns(){
  try {
    const payload=await fetchJson('/api/v1/official-training/runs?limit=20');
    officialTrainingRuns=objectArray(payload,'runs','items','results'); renderOfficialRunHistory();
    const selectedId=officialRunId(selectedOfficialRun);
    const next=officialTrainingRuns.find(run=>officialRunId(run)===selectedId)||officialTrainingRuns[0];
    if(next) await selectOfficialRun(officialRunId(next)); else renderOfficialRun(null);
  } catch(error){ $('officialRunHistory').textContent=String(error); }
}
async function activateOfficialRun(){
  const runId=officialRunId(selectedOfficialRun); if(!runId) return;
  const button=$('activateOfficialRun'); button.disabled=true; button.textContent='激活中…';
  try {
    const run=await sendJson(`/api/v1/official-training/runs/${encodeURIComponent(runId)}/activate`,'POST',undefined);
    renderOfficialRun(run?.run||run); await Promise.all([loadOfficialModel(),loadOfficialTrainingRuns(),loadModels()]);
  } catch(error){ statusBox('officialRunStatus',false,'激活失败',String(error)); }
  finally { button.textContent='激活为 Shadow'; }
}
async function loadOfficialModel(){
  try {
    const response=await fetchOptionalJson('/api/v1/official-model');
    activeOfficialModel=response?.artifact?{...response.artifact,active_run:response.active_run}:response;
    $('sensorOfficialModel').value=activeOfficialModel?`${activeOfficialModel.model_id||'uk-official-coast-logreg-v2'} · ${shortHash(activeOfficialModel.artifact_sha256||activeOfficialModel.artifact_hash||activeOfficialModel.hash)} · ${activeOfficialModel.deployment_mode||activeOfficialModel.mode||'SHADOW'}`:'尚无已激活官方模型';
    populateFrozenSensorContexts(); updateSensorProfileControls();
  } catch(error){ activeOfficialModel=null; $('sensorOfficialModel').value=String(error); updateSensorProfileControls(); }
}
function frozenSensorContexts(){
  return objectArray(activeOfficialModel?.sensor_test_contexts||activeOfficialModel?.source_manifest?.frozen_sensor_contexts||activeOfficialModel?.frozen_sensor_contexts||activeOfficialModel?.sensor_contexts,'items','contexts');
}
function sensorContextId(context){ return String(context?.context_id||context?.id||context?.source_row_sha256||''); }
function populateFrozenSensorContexts(){
  const previous=$('sensorContextId').value; const contexts=frozenSensorContexts();
  const stations=[...new Map(contexts.map(context=>{
    const id=String(context.station_id||context.site_id||''); return [id,{id,label:context.station_name||context.site_name||id}];
  }).filter(([id])=>id)).values()];
  if(stations.length){
    const previousStation=$('sensorStation').value;
    $('sensorStation').replaceChildren(...stations.map(station=>{ const option=document.createElement('option'); option.value=station.id; option.textContent=station.label; return option; }));
    if(stations.some(station=>station.id===previousStation)) $('sensorStation').value=previousStation;
  }
  const options=contexts.map(context=>{
    const option=document.createElement('option'); option.value=sensorContextId(context);
    option.textContent=`${context.station_id||context.site_id||'未知站点'} · ${context.timestamp||context.observed_at||option.value} · ${context.source_split||'FROZEN TEST'}`; return option;
  });
  if(!options.length){ const option=document.createElement('option'); option.value=''; option.textContent=activeOfficialModel?'已激活工件没有可用 frozen context':'先激活官方模型'; options.push(option); }
  $('sensorContextId').replaceChildren(...options);
  if(previous&&contexts.some(context=>sensorContextId(context)===previous)) $('sensorContextId').value=previous;
  applySelectedSensorContext();
}
function selectedFrozenSensorContext(){
  const id=$('sensorContextId').value; return frozenSensorContexts().find(context=>sensorContextId(context)===id)||null;
}
function applySelectedSensorContext(){
  const context=selectedFrozenSensorContext(); if(!context){ updateSensorProfileControls(); updateSensorMappingPreview(); return; }
  const stationId=String(context.station_id||context.site_id||'');
  if(stationId){
    if(!Array.from($('sensorStation').options).some(option=>option.value===stationId)){
      const option=document.createElement('option'); option.value=stationId; option.textContent=stationId; $('sensorStation').appendChild(option);
    }
    $('sensorStation').value=stationId;
  }
  const datum=context.datum||context.vertical_datum||activeOfficialModel?.datum;
  if(!frozenSensorProfile){ $('sensorGain').value=''; $('sensorReferenceLevel').value=''; }
  if(datum) $('sensorDatum').value=String(datum);
  updateSensorProfileControls(); updateSensorMappingPreview();
}
function completedSensorSessions(){ return simulationSessions.filter(session=>session.state==='completed'); }
function populateSensorSessionSelectors(){
  const sessions=completedSensorSessions();
  const makeOptions=(placeholder)=>{
    const first=document.createElement('option'); first.value=''; first.textContent=placeholder;
    return [first,...sessions.map(session=>{ const option=document.createElement('option'); option.value=session.session_id; option.textContent=`${session.name||'ESP32 WATER SIMULATION'} · ${session.session_id.slice(-8)} · ${formatCount(session.sample_count)} 样本`; return option; })];
  };
  const calibrationPrevious=$('sensorCalibrationSession').value; const testPrevious=$('sensorTestSession').value;
  $('sensorCalibrationSession').replaceChildren(...makeOptions('请选择已结束独立校准会话'));
  $('sensorTestSession').replaceChildren(...makeOptions('请选择已结束外部测试会话'));
  if(sessions.some(session=>session.session_id===calibrationPrevious)) $('sensorCalibrationSession').value=calibrationPrevious;
  if(sessions.some(session=>session.session_id===testPrevious)) $('sensorTestSession').value=testPrevious;
  updateSensorProfileControls(); updateSensorTestControls();
}
function updateSensorProfileControls(){
  const mode=$('sensorProfileMode').value; const formal=mode==='formal'; const context=selectedFrozenSensorContext(); const frozen=Boolean(frozenSensorProfile);
  $('sensorProfileMode').disabled=frozen; $('sensorContextId').disabled=frozen; $('sensorStation').disabled=frozen;
  $('sensorGain').readOnly=formal||frozen; $('sensorReferenceLevel').readOnly=formal||frozen;
  $('sensorGain').placeholder=formal?'由官方 TRAIN Q05/Q95 与校准会话派生':'必填：手动 exploratory gain';
  $('sensorReferenceLevel').placeholder=formal?'由官方 TRAIN 基准派生':'必填：手动 exploratory reference';
  $('sensorCalibrationSession').disabled=!formal||frozen;
  const gain=asNumber($('sensorGain').value),reference=asNumber($('sensorReferenceLevel').value);
  const formalReady=formal&&Boolean($('sensorCalibrationSession').value);
  const exploratoryReady=!formal&&gain!==null&&gain>0&&reference!==null;
  $('freezeSensorProfile').disabled=frozen||!activeOfficialModel||!context||!(formalReady||exploratoryReady);
  $('clearSensorProfile').disabled=!frozen;
  const profileMode=frozenSensorProfile?.mode||mode;
  $('sensorMappingFormula').textContent=`mapped_level_m = ${reference===null?'reference_level_m':reference} + ${gain===null?'gain':gain} × (water_rise_mm / 1000) · ${String(profileMode).toUpperCase()}`;
  updateSensorTestControls();
}
function sensorMappingRange(){
  const context=selectedFrozenSensorContext(); const range=frozenSensorProfile?.official_train_range||frozenSensorProfile?.train_range||context?.official_train_range||context?.train_range||activeOfficialModel?.training_feature_ranges?.relative_water_level_m||{};
  return {min:asNumber(range.min??range.minimum??range.q00),max:asNumber(range.max??range.maximum??range.q100)};
}
function updateSensorMappingPreview(){
  const rise=asNumber(latestTelemetry?.water_rise_mm); const gain=asNumber(frozenSensorProfile?.gain??$('sensorGain').value); const reference=asNumber(frozenSensorProfile?.reference_level_m??$('sensorReferenceLevel').value);
  $('sensorRawRise').textContent=rise===null?'—':`${rise.toFixed(1)} mm`;
  if(rise===null||gain===null||reference===null){ $('sensorMappedLevel').textContent='—'; $('sensorOodState').textContent='—'; return; }
  const mapped=reference+gain*(rise/1000); $('sensorMappedLevel').textContent=`${mapped.toFixed(4)} m`;
  const range=sensorMappingRange(); const ood=(range.min!==null&&mapped<range.min)||(range.max!==null&&mapped>range.max);
  $('sensorOodState').textContent=(range.min===null&&range.max===null)?'RANGE UNKNOWN':(ood?'OOD':'IN RANGE');
  $('sensorOodState').style.color=ood?'var(--warn)':'var(--safe)';
}
function renderSensorProfile(profile){
  frozenSensorProfile=profile||null;
  if(!profile){
    $('sensorProfileStatus').textContent='尚无冻结映射档案。正式外部测试必须在采集前预注册 profile、模型哈希和官方上下文。';
    $('sensorProfileProvenance').textContent='尚无冻结 profile。FORMAL 模式将记录官方 TRAIN Q05/Q95、独立校准会话 Q05/Q95、gain_m_per_m 和 reference_level_m。';
    updateSensorProfileControls(); updateSensorMappingPreview(); return;
  }
  const artifact=profile.profile||profile;
  const exploratory=artifact.exploratory===true||profile.mode==='exploratory'||artifact.mode==='exploratory_manual_linear';
  $('sensorProfileMode').value=exploratory?'exploratory':'formal';
  const context=artifact.official_context||{};
  const mapping=artifact.mapping||profile.mapping||{};
  const calibration=artifact.calibration_source||{};
  const contextId=profile.context_id||profile.frozen_context_id||context.context_id;
  if(contextId&&Array.from($('sensorContextId').options).some(option=>option.value===contextId)) $('sensorContextId').value=contextId;
  const stationId=profile.station_id||profile.site_id||artifact.site_id;
  if(stationId) $('sensorStation').value=stationId;
  const gain=profile.gain??profile.gain_m_per_m??mapping.gain_m_per_m;
  const reference=profile.reference_level_m??mapping.reference_level_m;
  if(gain!==undefined) $('sensorGain').value=String(gain);
  if(reference!==undefined) $('sensorReferenceLevel').value=String(reference);
  if(profile.datum||artifact.datum) $('sensorDatum').value=String(profile.datum||artifact.datum);
  if(profile.calibration_session_id) $('sensorCalibrationSession').value=profile.calibration_session_id;
  $('sensorProfileStatus').textContent=`FROZEN · ${exploratory?'EXPLORATORY':'FORMAL'} · ${stationId||'—'} · context ${shortHash(contextId)} · artifact ${shortHash(profile.artifact_sha256||artifact.official_model_artifact_sha256||profile.artifact_hash)} · profile ${shortHash(profile.profile_sha256||artifact.profile_sha256||profile.profile_hash)}${exploratory?' · EXCLUDED FROM FORMAL METRICS':''}`;
  $('sensorProfileProvenance').textContent=`calibration session ${profile.calibration_session_id||calibration.session_id||'NOT USED (EXPLORATORY)'} · device ${calibration.device_id||'—'} · ${formatCount(calibration.sample_count)} valid samples · samples sha ${shortHash(calibration.samples_sha256)} · official TRAIN Q05/Q95 ${valueText(mapping.official_train_q05_m)} / ${valueText(mapping.official_train_q95_m)} m · calibration Q05/Q95 ${valueText(mapping.calibration_rise_q05_mm)} / ${valueText(mapping.calibration_rise_q95_mm)} mm · gain_m_per_m ${valueText(gain)} · reference_level_m ${valueText(reference)} · clipping ${mapping.clipping===false?'FALSE':'—'} · datum ${profile.datum||artifact.datum||'—'} · source row ${shortHash(profile.source_row_sha256||context.source_row_sha256)}`;
  updateSensorProfileControls(); updateSensorMappingPreview();
}
async function loadSensorProfile(){
  try { renderSensorProfile(await fetchOptionalJson(`/api/v1/sensor-test/device-profile?device_id=${encodeURIComponent(DEVICE)}`)); }
  catch(error){ renderSensorProfile(null); $('sensorProfileStatus').textContent=String(error); }
}
async function freezeSensorProfile(){
  const mode=$('sensorProfileMode').value; const payload={device_id:DEVICE,context_id:$('sensorContextId').value,mode};
  if(mode==='formal') payload.calibration_session_id=$('sensorCalibrationSession').value;
  else { payload.manual_gain=Number($('sensorGain').value); payload.manual_reference_level_m=Number($('sensorReferenceLevel').value); }
  const button=$('freezeSensorProfile'); button.disabled=true; button.textContent='冻结中…';
  try { renderSensorProfile(await sendJson('/api/v1/sensor-test/device-profile','PUT',payload)); }
  catch(error){ $('sensorProfileStatus').textContent=String(error); }
  finally { button.textContent='冻结映射档案'; updateSensorProfileControls(); }
}
async function clearSensorProfile(){
  const button=$('clearSensorProfile'); button.disabled=true;
  try { await sendJson(`/api/v1/sensor-test/device-profile?device_id=${encodeURIComponent(DEVICE)}`,'DELETE',undefined); renderSensorProfile(null); }
  catch(error){ $('sensorProfileStatus').textContent=String(error); }
  finally { updateSensorProfileControls(); }
}
function updateSensorTestControls(){
  $('runSensorExternalTest').disabled=!frozenSensorProfile||!$('sensorTestSession').value;
}
function sensorRunId(run){ return String(run?.run_id||run?.id||''); }
function renderSensorTestRun(run){
  if(!run){
    statusBox('sensorTestStatus',false,'尚未运行','测试必须使用在采集前冻结的 profile；结果与官方冻结测试指标分开。');
    ['sensorMetricInputSamples','sensorMetricValidSamples','sensorMetricSamples','sensorMetricInvalidSamples','sensorMetricOod','sensorMetricMin','sensorMetricMax','sensorMetricMeanRisk','sensorMetricLatency','sensorMetricResultRows','sensorMetricPreviewRows'].forEach(id=>$(id).textContent='—');
    $('sensorTestEvaluationPolicy').textContent='完整指标聚合在全部 evaluated rows 上；接口仅保存并返回有限、均匀抽样的 rows preview，以避免大数据集拖慢浏览器。';
    $('sensorTestProvenance').textContent='尚无外部测试运行。';
    return;
  }
  const status=String(run.status||run.state||'completed').toLowerCase(); const complete=['completed','succeeded','ready'].includes(status);
  statusBox('sensorTestStatus',complete,`${sensorRunId(run)||'外部测试'} · ${status.toUpperCase()}`,run.message||run.detail||(complete?'线性映射与冻结模型推理已完成。':'运行尚未完成或已被阻断。'));
  const result=run.result||run; const metrics=result.metrics||result.external_test_metrics||result;
  const previewRows=objectArray(result.rows,'items');
  const inputSampleCount=asNumber(result.input_sample_count??metrics.input_sample_count);
  const validInputSampleCount=asNumber(result.valid_input_sample_count??metrics.valid_input_sample_count);
  const invalidInputSampleCount=asNumber(result.excluded_invalid_ultrasonic_samples??metrics.excluded_invalid_ultrasonic_samples);
  const sampleCount=asNumber(result.evaluated_sample_count??metrics.evaluated_sample_count??metrics.mapped_sample_count??result.sample_count??run.sample_count);
  const truncatedValidSampleCount=asNumber(result.truncated_valid_sample_count??metrics.truncated_valid_sample_count);
  const resultRowCount=asNumber(result.result_row_count??metrics.result_row_count);
  const previewRowCount=asNumber(result.preview_row_count??metrics.preview_row_count)??previewRows.length;
  const oodCount=asNumber(result.out_of_distribution_count??metrics.out_of_distribution_count);
  const oodRate=asNumber(metrics.ood_rate)??(sampleCount&&oodCount!==null?oodCount/sampleCount:null);
  $('sensorMetricInputSamples').textContent=formatCount(inputSampleCount);
  $('sensorMetricValidSamples').textContent=formatCount(validInputSampleCount);
  $('sensorMetricSamples').textContent=formatCount(sampleCount);
  $('sensorMetricInvalidSamples').textContent=formatCount(invalidInputSampleCount);
  $('sensorMetricOod').textContent=formatPercent(oodRate);
  $('sensorMetricMin').textContent=metricText(result.mapped_min_m??metrics.mapped_min_m??metrics.proxy_level_min_m,4);
  $('sensorMetricMax').textContent=metricText(result.mapped_max_m??metrics.mapped_max_m??metrics.proxy_level_max_m,4);
  $('sensorMetricMeanRisk').textContent=metricText(result.mean_extreme_water_probability??metrics.mean_risk_score??metrics.mean_probability??metrics.mean_extreme_water_probability);
  $('sensorMetricLatency').textContent=metricText(result.inference_latency_ms??metrics.inference_latency_ms??metrics.mean_inference_latency_ms,2);
  $('sensorMetricResultRows').textContent=formatCount(resultRowCount);
  $('sensorMetricPreviewRows').textContent=`${formatCount(previewRowCount)} / limit ${formatCount(result.preview_row_limit??metrics.preview_row_limit)}`;
  const evaluationTruncated=result.evaluation_truncated===true;
  $('sensorTestEvaluationPolicy').textContent=`input ${formatCount(inputSampleCount)} · valid ${formatCount(validInputSampleCount)} · invalid excluded ${formatCount(invalidInputSampleCount)} · evaluated ${formatCount(sampleCount)} / limit ${formatCount(result.evaluation_sample_limit)} using ${valueText(result.sampling_policy)} · truncated valid ${formatCount(truncatedValidSampleCount)} (${evaluationTruncated?'YES':'NO'}) · result_row_count ${formatCount(resultRowCount)} · rows preview ${formatCount(previewRowCount)} / limit ${formatCount(result.preview_row_limit)} using ${valueText(result.preview_sampling_policy)}. OOD、min/max 与风险均值聚合自全部 evaluated rows，不由 preview 反推。`;
  const unchanged=(result.model_artifact_unchanged??metrics.model_artifact_unchanged)===true?'· ARTIFACT HASH UNCHANGED':'';
  $('sensorTestProvenance').textContent=`session ${run.session_id||metrics.session_id||'—'} · evaluated samples sha ${shortHash(result.evaluated_samples_sha256)} · profile ${shortHash(run.profile_sha256||metrics.profile_sha256||run.profile_hash)} · model ${shortHash(run.artifact_sha256||run.artifact_sha256_before||result.official_model_artifact_sha256_before||run.artifact_hash)} · context ${shortHash(run.context_id||metrics.context_id)} · SENSOR_PROXY_EXTERNAL_TEST ${unchanged} · ${(run.formal_metrics_eligible??metrics.formal_metrics_eligible)===false?'EXCLUDED FROM FORMAL METRICS':'FORMAL PROFILE'}`;
  Array.from($('sensorTestRunHistory').querySelectorAll('button')).forEach(button=>button.setAttribute('aria-current',String(button.dataset.runId===sensorRunId(run))));
}
async function selectSensorTestRun(runId){
  try { renderSensorTestRun(await fetchJson(`/api/v1/sensor-test/runs/${encodeURIComponent(runId)}`)); }
  catch(error){ statusBox('sensorTestStatus',false,'无法读取外部测试',String(error)); }
}
function renderSensorTestRunHistory(){
  const nodes=sensorTestRuns.map(run=>{
    const button=document.createElement('button'); button.type='button'; button.className='run-button'; button.dataset.runId=sensorRunId(run);
    button.textContent=`${sensorRunId(run)||'未命名测试'} · ${String(run.status||run.state||'completed').toUpperCase()} · session ${String(run.session_id||'—').slice(-8)} · ${run.created_at?new Date(run.created_at).toLocaleString():'—'}`;
    button.addEventListener('click',()=>selectSensorTestRun(sensorRunId(run))); return button;
  });
  if(!nodes.length){ const empty=document.createElement('div'); empty.className='muted'; empty.textContent='尚无外部测试运行。'; nodes.push(empty); }
  $('sensorTestRunHistory').replaceChildren(...nodes);
}
async function loadSensorTestRuns(){
  try {
    const payload=await fetchJson(`/api/v1/sensor-test/runs?device_id=${encodeURIComponent(DEVICE)}&limit=20`);
    sensorTestRuns=objectArray(payload,'runs','items','results'); renderSensorTestRunHistory();
    if(sensorTestRuns.length) await selectSensorTestRun(sensorRunId(sensorTestRuns[0])); else renderSensorTestRun(null);
  } catch(error){ $('sensorTestRunHistory').textContent=String(error); }
}
async function runSensorExternalTest(){
  const sessionId=$('sensorTestSession').value; if(!sessionId||!frozenSensorProfile) return;
  const button=$('runSensorExternalTest'); button.disabled=true; button.textContent='外部测试中…';
  statusBox('sensorTestStatus',false,'正在运行','使用已冻结 profile 进行线性映射；模型参数、标准化器和阈值均不会改变。');
  try {
    const run=await sendJson('/api/v1/sensor-test/runs','POST',{device_id:DEVICE,session_id:sessionId});
    renderSensorTestRun(run?.run||run); await loadSensorTestRuns();
  } catch(error){ statusBox('sensorTestStatus',false,'外部测试失败',String(error)); }
  finally { button.textContent='运行外部测试'; updateSensorTestControls(); }
}
function clearScenarioInputs(){
  Object.values(scenarioFields).forEach(id=>{ $(id).value=''; });
}
function setScenarioForm(scenario){
  clearScenarioInputs();
  if(!scenario) return;
  Object.entries(scenarioFields).forEach(([key,id])=>{
    const value=scenario[key];
    if(value===null||value===undefined) return;
    $(id).value=key==='simulated_at'?localDatetimeValue(value):String(value);
  });
}
function updateScenarioControls(){
  const collecting=simulationSessions.some(session=>session.state==='active');
  $('saveScenario').disabled=collecting;
  $('deleteDeviceScenario').disabled=collecting||!currentScenario;
  if(collecting) $('scenarioStatus').textContent='设备正在采集：当前场景已锁定。先在 ESP32 结束会话，再配置下一次模拟海岸。';
}
function renderDeviceScenario(scenario){
  currentScenario=scenario;
  const context=$('scenarioPredictionContext');
  if(!scenario){
    context.className='badge danger'; context.textContent='CUSTOM PREDICTION BLOCKED · NO ACTIVE SIMULATED SCENARIO';
    $('scenarioStatus').textContent='当前没有模拟海岸。请填写、保存，再到 ESP32 点击 START；服务器不会改用 Open-Meteo 或最近一次会话。';
  } else {
    context.className='badge safe'; context.textContent=`ACTIVE SIMULATED SCENARIO · ${scenario.scenario_name}`;
    $('scenarioStatus').textContent=`当前已保存并激活：${scenario.scenario_name} · OPERATOR-SUPPLIED · hash ${shortHash(scenario.scenario_hash)} · 更新 ${new Date(scenario.updated_at).toLocaleString()}`;
  }
  updateScenarioControls();
}
async function loadDeviceScenario(){
  try {
    const scenario=await fetchOptionalJson(`/api/v1/simulations/device-scenario?device_id=${DEVICE}`);
    setScenarioForm(scenario); renderDeviceScenario(scenario);
  } catch(error){
    currentScenario=null; $('scenarioStatus').textContent=String(error); updateScenarioControls();
  }
}
function collectScenarioPayload(){
  const name=$('scenarioName').value.trim();
  if(!name) throw new Error('模拟场景名称不能为空。');
  if(name.length>80) throw new Error('模拟场景名称不能超过 80 个字符。');
  const localTime=$('scenarioSimulatedAt').value;
  if(!localTime) throw new Error('模拟观测时间不能为空。');
  const date=new Date(localTime);
  if(Number.isNaN(date.getTime())) throw new Error('模拟观测时间无效。');
  const note=$('scenarioNote').value.trim();
  if(note.length>500) throw new Error('操作者说明不能超过 500 个字符。');
  const payload={device_id:DEVICE,scenario_name:name,simulated_at:date.toISOString(),note};
  const numericKeys=Object.keys(scenarioFields).filter(key=>key.startsWith('sim_')&&key!=='simulated_at');
  numericKeys.forEach(key=>{
    const input=$(scenarioFields[key]);
    if(input.value.trim()===''||!input.checkValidity()) throw new Error(`请填写有效的“${input.closest('label').firstChild.textContent.trim()}”。`);
    const value=Number(input.value); if(!Number.isFinite(value)) throw new Error('所有模拟数值都必须是有限数字。');
    payload[key]=value;
  });
  return payload;
}
async function saveDeviceScenario(){
  try {
    const saved=await sendJson('/api/v1/simulations/device-scenario','PUT',collectScenarioPayload());
    setScenarioForm(saved); renderDeviceScenario(saved);
    $('scenarioStatus').textContent=`已保存并激活：${saved.scenario_name} · hash ${shortHash(saved.scenario_hash)}。现在可在 ESP32 点击 START，服务器会冻结快照。`;
    await loadSimulationSessions();
  } catch(error){ $('scenarioStatus').textContent=String(error); }
}
async function deleteDeviceScenario(){
  if(!currentScenario) return;
  try {
    await sendJson(`/api/v1/simulations/device-scenario?device_id=${DEVICE}`,'DELETE',undefined);
    clearScenarioInputs(); renderDeviceScenario(null);
  } catch(error){ $('scenarioStatus').textContent=String(error); }
}
function parseCsvLine(line){
  const values=[]; let value=''; let quoted=false;
  for(let index=0;index<line.length;index+=1){
    const character=line[index];
    if(character==='"'){
      if(quoted&&line[index+1]==='"'){ value+='"'; index+=1; } else quoted=!quoted;
    } else if(character===','&&!quoted){ values.push(value.trim()); value=''; }
    else value+=character;
  }
  if(quoted) throw new Error('CSV 引号未闭合。');
  values.push(value.trim()); return values;
}
function parseScenarioImport(text){
  const trimmed=text.trim(); if(!trimmed) throw new Error('请先粘贴 JSON 或 CSV。');
  if(trimmed.startsWith('{')){
    const parsed=JSON.parse(trimmed);
    if(!parsed||Array.isArray(parsed)||typeof parsed!=='object') throw new Error('JSON 必须是单个对象。');
    return parsed.environment&&typeof parsed.environment==='object'?{...parsed,...parsed.environment}:parsed;
  }
  const lines=trimmed.split(/\r?\n/).filter(line=>line.trim());
  if(lines.length!==2) throw new Error('CSV 必须恰好包含一行表头和一行数据。');
  const headers=parseCsvLine(lines[0]); const values=parseCsvLine(lines[1]);
  if(headers.length!==values.length) throw new Error('CSV 表头与数据列数不一致。');
  return Object.fromEntries(headers.map((header,index)=>[header.trim(),values[index]]));
}
function importScenarioIntoForm(){
  try {
    const scenario=parseScenarioImport($('scenarioImport').value); let imported=0;
    Object.entries(scenarioFields).forEach(([key,id])=>{
      if(!(key in scenario)||scenario[key]===null||scenario[key]===undefined) return;
      $(id).value=key==='simulated_at'?localDatetimeValue(scenario[key]):String(scenario[key]); imported+=1;
    });
    if(!imported) throw new Error('没有找到受支持的场景字段；请使用服务器字段名。');
    $('scenarioStatus').textContent=`已从本地文本填充 ${imported} 个字段，尚未上传。请检查后点击“保存当前场景”。`;
  } catch(error){ $('scenarioStatus').textContent=`导入失败：${error}`; }
}
async function loadSelectedScenarioSnapshot(sessionId){
  const target=$('selectedScenarioSnapshot');
  selectedScenarioSnapshotRecord=null; $('saveSimulationLabel').disabled=true;
  target.textContent='正在读取不可修改的会话场景快照…';
  try {
    const snapshot=await fetchOptionalJson(`/api/v1/simulations/sessions/${encodeURIComponent(sessionId)}/scenario?device_id=${DEVICE}`);
    if($('simulationSession').value!==sessionId) return;
    if(!snapshot){ target.textContent='该会话没有冻结场景（旧数据或不完整实验），服务器会阻止标注和 22 特征训练。'; return; }
    selectedScenarioSnapshotRecord=snapshot;
    $('saveSimulationLabel').disabled=selectedSimulationSession?.state!=='completed';
    target.textContent=`${snapshot.scenario_name} · SIMULATED / OPERATOR-SUPPLIED · ${new Date(snapshot.simulated_at).toLocaleString()} · 虚拟坐标 ${Number(snapshot.sim_latitude).toFixed(4)}, ${Number(snapshot.sim_longitude).toFixed(4)} · 气温 ${snapshot.sim_air_temperature_c} °C · 湿度 ${snapshot.sim_humidity_percent}% · 风 ${snapshot.sim_wind_speed_kmh} km/h · 浪高 ${snapshot.sim_wave_height_m} m · 浪周期 ${snapshot.sim_wave_period_s} s · 水温 ${snapshot.sim_water_temperature_c} °C · 海平面 ${snapshot.sim_sea_level_height_m} m · 海流 ${snapshot.sim_ocean_current_velocity_kmh} km/h · schema ${snapshot.scenario_schema||'--'} v${snapshot.scenario_schema_version??'--'} · hash ${shortHash(snapshot.scenario_hash)} · IMMUTABLE SNAPSHOT`;
  } catch(error){ if($('simulationSession').value===sessionId){ target.textContent=String(error); $('saveSimulationLabel').disabled=true; } }
}
function statusLabel(status){ return ({ready:'READY',unavailable:'UNAVAILABLE',not_trained:'NOT TRAINED'})[status]||String(status||'UNKNOWN').toUpperCase(); }
function renderModelCatalog(catalog){
  const cards=catalog.models.map(model=>{
    const card=document.createElement('div');
    card.className='model-card'+(model.model_id===catalog.selected_model_id?' selected':'');
    const title=document.createElement('div'); title.className='section-heading';
    const name=document.createElement('strong'); name.textContent=model.display_name;
    const badges=document.createElement('div'); badges.className='badges';
    const state=document.createElement('span');
    state.className=`badge ${model.status==='ready'?'safe':(model.status==='unavailable'?'danger':'warn')}`;
    state.textContent=statusLabel(model.status); badges.appendChild(state);
    if(model.model_id===catalog.selected_model_id){ const selected=document.createElement('span'); selected.className='badge active'; selected.textContent='ESP32 SELECTED'; badges.appendChild(selected); }
    title.append(name,badges);
    const meta=document.createElement('div'); meta.className='model-meta';
    meta.textContent=`${model.model_id} · ${model.mode} · ${model.description}`;
    card.append(title,meta);
    if(model.model_id==='custom-water-logreg-v1'&&customModelMetadata){
      const artifact=document.createElement('div'); artifact.className='model-meta';
      artifact.textContent=`artifact ${customModelMetadata.version||'--'} · ${customModelMetadata.created_at?new Date(customModelMetadata.created_at).toLocaleString():'--'} · hash ${shortHash(customModelMetadata.hash||customModelMetadata.artifact_hash)}`;
      card.appendChild(artifact);
    }
    return card;
  });
  $('modelCatalog').replaceChildren(...cards);
}
async function loadModels(){
  try {
    const [catalog,metadata]=await Promise.all([
      fetchJson(`/api/v1/models?device_id=${DEVICE}`),
      fetchOptionalJson('/api/v1/simulations/model')
    ]);
    customModelMetadata=metadata; renderModelCatalog(catalog);
    if(metadata) renderTrainingMetrics(metadata,'服务器当前模型工件'); else clearTrainingMetrics();
  } catch(error){ $('modelCatalog').textContent=String(error); }
}
function svgNode(name,attributes={},text=''){
  const node=document.createElementNS('http://www.w3.org/2000/svg',name);
  Object.entries(attributes).forEach(([key,value])=>node.setAttribute(key,String(value)));
  if(text) node.textContent=text; return node;
}
function currentSelection(){
  const startText=$('labelStartSeq').value; const endText=$('labelEndSeq').value;
  const start=startText===''?null:asNumber(startText); const end=endText===''?null:asNumber(endText);
  return {start,end,complete:start!==null&&end!==null};
}
function renderSimulationChart(points=currentTimeline.points,labels=currentTimeline.labels){
  const svg=$('simulationChart'); svg.replaceChildren();
  if(!points.length){
    svg.appendChild(svgNode('text',{x:500,y:190,'text-anchor':'middle',fill:'#87a8b3','font-size':16},'等待会话样本'));
    return;
  }
  const width=1000,left=88,right=20,top=28,bandHeight=88,gap=24;
  const sorted=[...points].sort((a,b)=>Number(a.seq)-Number(b.seq));
  const seqMin=Number(sorted[0].seq),seqMax=Number(sorted.at(-1).seq);
  const seqSpan=Math.max(1,seqMax-seqMin);
  const xFor=seq=>left+(Number(seq)-seqMin)/seqSpan*(width-left-right);
  const plotBottom=top+3*bandHeight+2*gap;
  const labelColors={safe:'#29d391',danger:'#ff5b61',unknown:'#6b7f89'};
  labels.forEach(label=>{
    const start=Math.max(seqMin,Number(label.start_seq)); const end=Math.min(seqMax,Number(label.end_seq));
    if(end<seqMin||start>seqMax) return;
    const x1=xFor(start),x2=xFor(end);
    svg.appendChild(svgNode('rect',{x:x1,y:top,width:Math.max(3,x2-x1),height:plotBottom-top,fill:labelColors[label.label]||labelColors.unknown,opacity:.09}));
  });
  const selection=currentSelection();
  if(selection.start!==null){
    const first=Math.min(selection.start,selection.end??selection.start),last=Math.max(selection.start,selection.end??selection.start);
    const x1=xFor(first),x2=xFor(last);
    svg.appendChild(svgNode('rect',{x:x1,y:top,width:Math.max(2,x2-x1),height:plotBottom-top,fill:'#4bd6ff',opacity:.13,stroke:'#4bd6ff','stroke-width':1.5}));
  }
  const series=[
    {key:'distance_mm',name:'距离',unit:'mm',color:'#4bd6ff'},
    {key:'water_rise_mm',name:'水位上升',unit:'mm',color:'#29d391'},
    {key:'rise_rate_mm_s',name:'变化速度',unit:'mm/s',color:'#ffb84d'}
  ];
  const step=Math.max(1,Math.ceil(sorted.length/650));
  const displayed=sorted.filter((_point,index)=>index%step===0||index===sorted.length-1);
  series.forEach((seriesItem,index)=>{
    const yTop=top+index*(bandHeight+gap); const yBottom=yTop+bandHeight;
    const valid=sorted.filter(point=>point.valid_ultrasonic!==false&&asNumber(point[seriesItem.key])!==null);
    let minimum=Math.min(...valid.map(point=>Number(point[seriesItem.key])));
    let maximum=Math.max(...valid.map(point=>Number(point[seriesItem.key])));
    if(!valid.length){ minimum=0; maximum=1; }
    if(maximum===minimum){ maximum+=1; minimum-=1; }
    const padding=(maximum-minimum)*.08; minimum-=padding; maximum+=padding;
    const yFor=value=>yBottom-(Number(value)-minimum)/(maximum-minimum)*bandHeight;
    svg.appendChild(svgNode('rect',{x:left,y:yTop,width:width-left-right,height:bandHeight,rx:6,fill:'#0d2631',stroke:'#214452'}));
    [0,.5,1].forEach(fraction=>svg.appendChild(svgNode('line',{x1:left,y1:yTop+fraction*bandHeight,x2:width-right,y2:yTop+fraction*bandHeight,stroke:'#214452','stroke-width':1,'stroke-dasharray':'3 6'})));
    svg.appendChild(svgNode('text',{x:10,y:yTop+16,fill:seriesItem.color,'font-size':12,'font-weight':700},seriesItem.name));
    svg.appendChild(svgNode('text',{x:10,y:yTop+34,fill:'#87a8b3','font-size':10},`${maximum.toFixed(1)} ${seriesItem.unit}`));
    svg.appendChild(svgNode('text',{x:10,y:yBottom,fill:'#87a8b3','font-size':10},`${minimum.toFixed(1)} ${seriesItem.unit}`));
    let segment=[];
    const flush=()=>{ if(segment.length>1) svg.appendChild(svgNode('polyline',{points:segment.join(' '),fill:'none',stroke:seriesItem.color,'stroke-width':2,'stroke-linecap':'round','stroke-linejoin':'round'})); segment=[]; };
    displayed.forEach(point=>{
      const value=asNumber(point[seriesItem.key]);
      if(point.valid_ultrasonic===false||value===null){ flush(); return; }
      segment.push(`${xFor(point.seq).toFixed(2)},${yFor(value).toFixed(2)}`);
    }); flush();
  });
  displayed.filter(point=>point.valid_ultrasonic===false).forEach(point=>{
    const x=xFor(point.seq); svg.appendChild(svgNode('line',{x1:x-3,y1:top-9,x2:x+3,y2:top-3,stroke:'#c584ff','stroke-width':2}));
    svg.appendChild(svgNode('line',{x1:x+3,y1:top-9,x2:x-3,y2:top-3,stroke:'#c584ff','stroke-width':2}));
  });
  svg.appendChild(svgNode('text',{x:left,y:plotBottom+20,fill:'#87a8b3','font-size':10},`SEQ ${seqMin}`));
  svg.appendChild(svgNode('text',{x:width-right,y:plotBottom+20,'text-anchor':'end',fill:'#87a8b3','font-size':10},`SEQ ${seqMax}`));
}
function updateSelectionVisuals(){
  renderSimulationChart(); renderSimulationSamples(currentTimeline.points);
}
function selectChartSequence(event){
  if(!currentTimeline.points.length||!selectedSimulationSession||selectedSimulationSession.state!=='completed') return;
  const box=$('simulationChart').getBoundingClientRect();
  const viewX=(event.clientX-box.left)/box.width*1000;
  const sorted=[...currentTimeline.points].sort((a,b)=>Number(a.seq)-Number(b.seq));
  const first=Number(sorted[0].seq),last=Number(sorted.at(-1).seq);
  const target=first+Math.max(0,Math.min(1,(viewX-88)/(1000-88-20)))*(last-first);
  const closest=sorted.reduce((best,point)=>Math.abs(Number(point.seq)-target)<Math.abs(Number(best.seq)-target)?point:best,sorted[0]);
  const selection=currentSelection();
  if(selection.start===null||selection.complete){ $('labelStartSeq').value=closest.seq; $('labelEndSeq').value=''; }
  else {
    const start=Math.min(Number(selection.start),Number(closest.seq)); const end=Math.max(Number(selection.start),Number(closest.seq));
    $('labelStartSeq').value=start; $('labelEndSeq').value=end;
  }
  updateSelectionVisuals();
}
function labelPill(label){
  const value=['safe','danger'].includes(label)?label:'unknown';
  const span=document.createElement('span'); span.className=`label-pill ${value}`; span.textContent=value.toUpperCase(); return span;
}
function renderSimulationSamples(samples){
  if(!samples.length){ $('simulationSamples').innerHTML='<tr><td colspan="8" class="muted">该会话尚无样本</td></tr>'; return; }
  const selection=currentSelection(); const visible=samples.length>300?samples.slice(-300):samples;
  const rows=visible.map(sample=>{
    const row=document.createElement('tr');
    if(selection.complete&&sample.seq>=selection.start&&sample.seq<=selection.end) row.className='selected-sample';
    addTextCell(row,sample.seq); addTextCell(row,new Date(sample.received_at).toLocaleTimeString());
    addTextCell(row,`${sample.distance_mm} mm`); addTextCell(row,`${sample.water_rise_mm} mm`);
    addTextCell(row,`${sample.rise_rate_mm_s} mm/s`);
    addTextCell(row,sample.valid_ultrasonic?'有效':'排除',sample.valid_ultrasonic?'online':'offline');
    const labelCell=document.createElement('td'); labelCell.appendChild(labelPill(sample.label)); row.appendChild(labelCell);
    const controls=document.createElement('td');
    const begin=document.createElement('button'); begin.type='button'; begin.className='secondary compact'; begin.textContent='设起点';
    begin.addEventListener('click',()=>{ $('labelStartSeq').value=sample.seq; $('labelEndSeq').value=''; updateSelectionVisuals(); });
    const finish=document.createElement('button'); finish.type='button'; finish.className='secondary compact'; finish.textContent='设终点';
    finish.style.marginLeft='6px'; finish.addEventListener('click',()=>{
      const start=asNumber($('labelStartSeq').value); $('labelStartSeq').value=start===null?sample.seq:Math.min(start,sample.seq); $('labelEndSeq').value=start===null?sample.seq:Math.max(start,sample.seq); updateSelectionVisuals();
    });
    controls.append(begin,finish); row.appendChild(controls); return row;
  });
  $('simulationSamples').replaceChildren(...rows);
}
function renderSimulationLabels(labels){
  if(!labels.length){ $('simulationLabels').innerHTML='<tr><td colspan="6" class="muted">尚无人工标签；未覆盖样本保持 UNKNOWN</td></tr>'; return; }
  const rows=labels.map(label=>{
    const row=document.createElement('tr');
    addTextCell(row,label.version); addTextCell(row,label.start_seq); addTextCell(row,label.end_seq);
    const labelCell=document.createElement('td'); labelCell.appendChild(labelPill(label.label)); row.appendChild(labelCell);
    addTextCell(row,label.note||'--'); addTextCell(row,new Date(label.updated_at).toLocaleString()); return row;
  });
  $('simulationLabels').replaceChildren(...rows);
}
function setBar(id,fraction){ const value=Math.max(0,Math.min(1,asNumber(fraction)??0)); $(id).style.width=`${(value*100).toFixed(1)}%`; }
function renderOverview(overview){
  const totals=overview?.totals||{}; const count=asNumber(totals.sample_count)||0;
  $('summarySessions').textContent=formatCount(totals.session_count);
  $('summarySessionStates').textContent=`${formatCount(totals.active_session_count)} 采集中 · ${formatCount(totals.completed_session_count)} 已结束`;
  $('summarySamples').textContent=formatCount(totals.sample_count); $('summaryValid').textContent=formatCount(totals.valid_ultrasonic_samples);
  const validRate=count?Number(totals.valid_ultrasonic_samples||0)/count:0; $('summaryValidRate').textContent=`数据有效率 ${formatPercent(validRate)}`;
  const labels=totals.label_counts||{}; $('summarySafe').textContent=formatCount(labels.safe); $('summaryDanger').textContent=formatCount(labels.danger);
  $('summaryCoverage').textContent=formatPercent(totals.label_coverage); $('summaryUnknown').textContent=`UNKNOWN ${formatCount(labels.unknown)}`;
}
function completedTrainingSessions(){ return simulationSessions.filter(session=>session.state==='completed'); }
function selectedTrainingSessionIdsInOrder(){
  const selected=selectedTrainingSessionIds;
  return completedTrainingSessions().map(session=>session.session_id).filter(sessionId=>selected.has(sessionId));
}
function reconcileTrainingSessionSelection(){
  const available=new Set(completedTrainingSessions().map(session=>session.session_id));
  selectedTrainingSessionIds=new Set([...selectedTrainingSessionIds].filter(sessionId=>available.has(sessionId)));
}
function renderTrainingSelectionSummary(){
  const completed=completedTrainingSessions(); const selected=selectedTrainingSessionIdsInOrder();
  $('trainingSelectionCount').textContent=selected.length
    ?`已选择 ${formatCount(selected.length)} / ${formatCount(completed.length)} 个已结束会话；readiness 和训练只使用这些 ID。`
    :`已选择 0 / ${formatCount(completed.length)} 个已结束会话；不会默认使用全部数据。`;
  $('selectAllTrainingSessions').disabled=!completed.length||selected.length===completed.length;
  $('clearTrainingSessionSelection').disabled=!selected.length;
}
function clearPendingSessionDeletion(){
  if(pendingSessionDeletionTimer!==null) window.clearTimeout(pendingSessionDeletionTimer);
  pendingSessionDeletionTimer=null; pendingSessionDeletionId=null; pendingSessionDeletionExpiresAt=0;
}
function sessionDeletionDescriptor(session){
  return `${session.name}（会话 ID ${session.session_id}，${formatCount(session.sample_count)} 个样本）`;
}
function focusSessionDeleteButton(sessionId){
  window.requestAnimationFrame(()=>{
    const button=[...document.querySelectorAll('.session-delete-button')]
      .find(candidate=>candidate.dataset.sessionId===sessionId);
    if(button) button.focus();
  });
}
function armSessionDeletion(session){
  clearPendingSessionDeletion(); pendingSessionDeletionId=session.session_id;
  pendingSessionDeletionExpiresAt=Date.now()+5000;
  pendingSessionDeletionTimer=window.setTimeout(()=>{
    if(pendingSessionDeletionId!==session.session_id) return;
    clearPendingSessionDeletion();
    $('sessionDeletionStatus').textContent=`已取消删除 ${sessionDeletionDescriptor(session)}：5 秒确认时间已过。`;
    renderSessionList();
  },5000);
  $('sessionDeletionStatus').textContent=`防误触确认：5 秒内再次点击红色按钮，才会永久删除 ${sessionDeletionDescriptor(session)} 及其样本、标签和场景快照。`;
  renderSessionList(); focusSessionDeleteButton(session.session_id);
}
function describeSessionDeletionFailure(error){
  const message=String(error);
  if(message.includes('is referenced by training artifact')) return `删除被拒绝：该会话已被训练模型工件使用，为保持模型可追溯性不可删除。${message}`;
  if(message.includes('cannot be verified; session deletion is blocked')) return `删除被拒绝：服务器无法验证现有训练工件，因此为保护训练来源暂不允许删除。${message}`;
  if(message.includes('active simulation session')) return `删除被拒绝：该会话仍在采集中，请先结束会话。${message}`;
  if(message.includes('not found')) return `删除失败：服务器未找到该会话，可能已被其他管理员删除。${message}`;
  return `删除失败：${message}`;
}
async function deleteCompletedSimulationSession(session){
  if(session.state!=='completed'){
    $('sessionDeletionStatus').textContent='采集中的会话不能删除；请先结束会话。'; return;
  }
  if(pendingSessionDeletionId!==session.session_id||Date.now()>pendingSessionDeletionExpiresAt){
    armSessionDeletion(session); return;
  }
  clearPendingSessionDeletion(); deletingSimulationSessionId=session.session_id;
  $('sessionDeletionStatus').textContent=`正在永久删除 ${sessionDeletionDescriptor(session)}…`;
  renderSessionList();
  let result;
  try {
    result=await sendJson(`/api/v1/simulations/sessions/${encodeURIComponent(session.session_id)}?device_id=${encodeURIComponent(DEVICE)}`,'DELETE',undefined);
  } catch(error){
    deletingSimulationSessionId=null; $('sessionDeletionStatus').textContent=describeSessionDeletionFailure(error);
    renderSessionList(); return;
  }
  selectedTrainingSessionIds.delete(session.session_id); simulationReadiness=null;
  simulationSessions=simulationSessions.filter(item=>item.session_id!==session.session_id);
  if(selectedSimulationSession?.session_id===session.session_id){
    simulationRequestSerial+=1; selectedSimulationSession=null; emptyTimeline();
  }
  deletingSimulationSessionId=null; reconcileTrainingSessionSelection(); renderSessionList();
  const counts=result?.deleted_counts||{};
  const successMessage=`已删除 ${result?.session_id||session.session_id}：样本 ${formatCount(counts.samples)}、标签 ${formatCount(counts.labels)}、场景快照 ${formatCount(counts.scenario_snapshots)}；已解除遥测关联 ${formatCount(result?.detached_telemetry_count)} 条。`;
  $('sessionDeletionStatus').textContent=`${successMessage} 正在刷新总览、时间线和训练条件…`;
  try {
    await loadSimulationSessions({throwOnError:true});
    $('sessionDeletionStatus').textContent=`${successMessage} 总览、时间线和训练条件已刷新。`;
  } catch(refreshError){
    $('sessionDeletionStatus').textContent=`${successMessage} 删除成功但刷新失败，将自动重试：${String(refreshError)}`;
  }
}
function renderSessionList(){
  if(!simulationSessions.length){
    $('simulationSessionList').innerHTML='<div class="muted">等待 ESP32 在 LCD 的 COLLECTION 页面开始采集。</div>';
    renderTrainingSelectionSummary(); return;
  }
  const selectedId=$('simulationSession').value;
  const cards=simulationSessions.map(session=>{
    const trainingSelected=selectedTrainingSessionIds.has(session.session_id);
    const card=document.createElement('article');
    card.className='session-card'+(session.session_id===selectedId?' selected':'')+(trainingSelected?' training-selected':'');
    const button=document.createElement('button'); button.type='button'; button.className='session-button';
    const top=document.createElement('div'); top.className='session-top'; const name=document.createElement('span'); name.textContent=session.name;
    const state=document.createElement('span'); state.className=`badge ${session.state==='active'?'active':''}`; state.textContent=session.state==='active'?'采集中':'已结束'; top.append(name,state);
    const counts=session.label_counts||{}; const meta=document.createElement('div'); meta.className='session-meta';
    meta.textContent=`${formatCount(session.sample_count)} 样本 · 有效 ${formatCount(session.valid_ultrasonic_samples)} · SAFE ${formatCount(counts.safe)} / DANGER ${formatCount(counts.danger)} · ${new Date(session.started_at).toLocaleString()}`;
    button.append(top,meta); button.addEventListener('click',async()=>{ $('simulationSession').value=session.session_id; await loadSimulationDetails(); renderSessionList(); });
    card.appendChild(button);
    if(session.state==='completed'){
      const toggle=document.createElement('label'); toggle.className='session-training-toggle';
      const checkbox=document.createElement('input'); checkbox.type='checkbox'; checkbox.checked=trainingSelected;
      checkbox.setAttribute('aria-label',`选择 ${session.name} 用于训练`);
      const copy=document.createElement('span'); copy.textContent=trainingSelected?'已加入本次训练':'勾选加入本次训练'; toggle.append(checkbox,copy);
      checkbox.addEventListener('change',()=>{
        if(checkbox.checked) selectedTrainingSessionIds.add(session.session_id); else selectedTrainingSessionIds.delete(session.session_id);
        renderSessionList(); loadTrainingReadiness();
      });
      card.appendChild(toggle);
    } else {
      const unavailable=document.createElement('div'); unavailable.className='session-training-toggle'; unavailable.textContent='采集中；结束后才可选作训练数据'; card.appendChild(unavailable);
    }
    const actions=document.createElement('div'); actions.className='session-actions';
    const deleteButton=document.createElement('button'); deleteButton.type='button';
    deleteButton.className='danger compact session-delete-button'; deleteButton.dataset.sessionId=session.session_id;
    deleteButton.setAttribute('aria-describedby','sessionDeletionHelp sessionDeletionStatus');
    if(session.state!=='completed'){
      deleteButton.disabled=true; deleteButton.textContent='采集中，不能删除';
      deleteButton.setAttribute('aria-label',`${session.name}，会话 ID ${session.session_id}，${formatCount(session.sample_count)} 个样本，正在采集中，不能删除`);
    } else {
      const pending=pendingSessionDeletionId===session.session_id&&Date.now()<=pendingSessionDeletionExpiresAt;
      const deleting=deletingSimulationSessionId===session.session_id;
      const shortSessionId=String(session.session_id).slice(-8);
      deleteButton.disabled=deleting; deleteButton.classList.toggle('confirm-delete',pending);
      deleteButton.setAttribute('aria-pressed',String(pending));
      deleteButton.setAttribute('aria-label',pending
        ?`确认永久删除 ${session.name}，会话 ID ${session.session_id}，${formatCount(session.sample_count)} 个样本`
        :`删除已结束会话 ${session.name}，会话 ID ${session.session_id}，${formatCount(session.sample_count)} 个样本`);
      deleteButton.textContent=deleting?'正在删除…':(pending?`确认删除 …${shortSessionId} · ${formatCount(session.sample_count)} 样本（5 秒内）`:'删除这个无用会话');
      deleteButton.addEventListener('click',()=>deleteCompletedSimulationSession(session));
    }
    actions.appendChild(deleteButton); card.appendChild(actions);
    return card;
  });
  $('simulationSessionList').replaceChildren(...cards); renderTrainingSelectionSummary();
}
function renderSessionQuality(summary){
  const count=asNumber(summary?.sample_count)||0; const valid=asNumber(summary?.valid_ultrasonic_samples)||0;
  const invalid=asNumber(summary?.invalid_ultrasonic_samples)||0; const labels=summary?.label_counts||{};
  const safe=asNumber(labels.safe)||0,danger=asNumber(labels.danger)||0,unknown=asNumber(labels.unknown)||0;
  const validRate=count?valid/count:0; const invalidRate=count?invalid/count:0; const coverage=asNumber(summary?.label_coverage)??(count?(safe+danger)/count:0);
  setBar('qualityValidBar',validRate); setBar('qualityInvalidBar',invalidRate); setBar('qualityCoverageBar',coverage);
  $('qualityValidText').textContent=`${formatCount(valid)} / ${formatCount(count)}`; $('qualityInvalidText').textContent=formatCount(invalid); $('qualityCoverageText').textContent=formatPercent(coverage);
  $('qualityDetails').textContent=`服务器统计 · 超声波有效率 ${formatPercent(validRate)} · 距离 ${formatCount(summary?.distance_min_mm)}–${formatCount(summary?.distance_max_mm)} mm · 水位上升 ${formatCount(summary?.water_rise_min_mm)}–${formatCount(summary?.water_rise_max_mm)} mm`;
  const divisor=Math.max(1,safe+danger+unknown); $('coverageSafe').style.width=`${safe/divisor*100}%`; $('coverageDanger').style.width=`${danger/divisor*100}%`; $('coverageUnknown').style.width=`${unknown/divisor*100}%`;
  $('coverageSafeCount').textContent=formatCount(safe); $('coverageDangerCount').textContent=formatCount(danger); $('coverageUnknownCount').textContent=formatCount(unknown);
  $('coverageNote').textContent=`标签版本 ${$('labelVersion').value} · 未标注/清除的样本保持 UNKNOWN，训练时由服务器排除。`;
}
function emptyTimeline(){
  currentTimeline={session:null,points:[],labels:[]}; selectedScenarioSnapshotRecord=null; renderSimulationChart(); renderSimulationSamples([]); renderSimulationLabels([]);
  renderSessionQuality({}); $('chartCaption').textContent='请选择会话查看真实采样曲线';
  $('selectedScenarioSnapshot').textContent='尚未选择采集会话。'; $('saveSimulationLabel').disabled=true;
}
async function loadSimulationDetails(){
  const id=$('simulationSession').value;
  selectedSimulationSession=simulationSessions.find(session=>session.session_id===id)||null;
  $('stopSimulation').disabled=!selectedSimulationSession||selectedSimulationSession.state!=='active';
  $('saveSimulationLabel').disabled=true;
  renderSessionList();
  if(!selectedSimulationSession){ emptyTimeline(); return; }
  loadSelectedScenarioSnapshot(id);
  $('simulationStatus').textContent=`${selectedSimulationSession.name} · ${selectedSimulationSession.state.toUpperCase()} · ${formatCount(selectedSimulationSession.sample_count)} 个样本 · baseline ${selectedSimulationSession.baseline_distance_mm??'--'} mm · SYNTHETIC`;
  const serial=++simulationRequestSerial;
  try {
    const version=Math.max(1,Number($('labelVersion').value)||1);
    const timeline=await fetchJson(`/api/v1/simulations/sessions/${encodeURIComponent(id)}/timeline?device_id=${DEVICE}&label_version=${version}&limit=5000`);
    if(serial!==simulationRequestSerial||$('simulationSession').value!==id) return;
    currentTimeline={session:normaliseSessionSummary(timeline.session),points:timeline.points||[],labels:timeline.labels||[]};
    selectedSimulationSession={...selectedSimulationSession,...currentTimeline.session};
    renderSimulationSamples(currentTimeline.points); renderSimulationLabels(currentTimeline.labels); renderSimulationChart(); renderSessionQuality(currentTimeline.session);
    const first=currentTimeline.session?.first_seq, last=currentTimeline.session?.last_seq;
    $('chartCaption').textContent=`${formatCount(currentTimeline.points.length)} 个时间点 · SEQ ${first??'--'}–${last??'--'} · 紫色叉号/曲线断点表示服务器判定无效的超声波样本`;
    $('labelStatus').textContent=selectedSimulationSession.state==='completed'?'可在曲线上点击两次选择时间段，然后由操作者保存 SAFE、DANGER 或 UNKNOWN。':'正在采集；结束会话后才能人工标注，当前样本仍会实时刷新。';
  } catch(error){ $('labelStatus').textContent=String(error); emptyTimeline(); }
}
function renderTrainingReadiness(readiness){
  simulationReadiness=readiness; const ready=Boolean(readiness?.ready); const box=$('trainingReadiness');
  box.className=`readiness ${ready?'ready':'blocked'}`; const dot=document.createElement('span'); dot.textContent=ready?'●':'▲';
  const body=document.createElement('div'); const evidence=readiness?.evidence_quality||{};
  const tierLabels={blocked:'BLOCKED',exploratory:'EXPLORATORY',course_demo:'COURSE DEMO',stronger_demo:'STRONGER DEMO'};
  const tier=tierLabels[evidence.tier]||String(evidence.tier|| (ready?'EXPLORATORY':'BLOCKED')).toUpperCase().replaceAll('_',' ');
  const title=document.createElement('strong'); title.textContent=ready?`服务器确认：可以训练 · ${tier}`:`硬阻断：暂不可训练 · ${tier}`; body.appendChild(title);
  const blockers=readiness?.blockers||[];
  if(blockers.length){ const hard=document.createElement('div'); hard.className='compact'; hard.textContent=`硬阻断：${blockers.join(' · ')}`; body.appendChild(hard); }
  const qualitySummary=document.createElement('div'); qualitySummary.className='compact';
  qualitySummary.textContent=evidence.summary||'服务器未返回证据质量摘要。'; body.appendChild(qualitySummary);
  const evaluationScope=evidence.evaluation_scope||'blocked'; const scenarioGeneralization=evidence.scenario_generalization_evaluable===true;
  const environmentEffectsLearnable=evidence.environment_effects_learnable===true;
  const scopeLine=document.createElement('div'); scopeLine.className='compact muted'; scopeLine.style.marginTop='6px';
  scopeLine.textContent=`评估范围：${evaluationScope.replaceAll('_',' ').toUpperCase()} · 跨场景泛化 ${scenarioGeneralization?'可评估':'不可评估'} · 环境效应 ${environmentEffectsLearnable?'可学习':'不可学习'}`; body.appendChild(scopeLine);
  if(evaluationScope==='single_scenario_session_holdout'){
    const singleScenario=document.createElement('div'); singleScenario.className='compact'; singleScenario.textContent='SINGLE SCENARIO：只评估同场景跨采集轮次；环境效应与环境系数不可解释，不代表跨场景泛化。'; body.appendChild(singleScenario);
  } else if(evidence.environment_effects_learnable===false){
    const environmentWarning=document.createElement('div'); environmentWarning.className='compact'; environmentWarning.textContent='环境效应不可学习：环境系数不可解释。'; body.appendChild(environmentWarning);
  }
  const warnings=readiness?.warnings||[];
  if(warnings.length){ const warning=document.createElement('div'); warning.className='compact muted'; warning.style.marginTop='6px'; warning.textContent=`质量建议：${warnings.join(' · ')}`; body.appendChild(warning); }
  const criteria=Object.entries(evidence.criteria||{});
  if(criteria.length){ const criteriaLine=document.createElement('div'); criteriaLine.className='compact muted'; criteriaLine.style.marginTop='6px';
    criteriaLine.textContent=`证据等级建议：${criteria.map(([name,item])=>`${name.replaceAll('_',' ')} ${formatCount(item.actual)}/${formatCount(item.recommended)} ${item.met?'✓':'△'}`).join(' · ')}`; body.appendChild(criteriaLine); }
  const selection=readiness?.selection||{}; const serverSelected=selection.selected_session_ids||[]; const effectiveSelected=selection.effective_session_ids||[];
  const selectionLine=document.createElement('div'); selectionLine.className='compact muted'; selectionLine.style.marginTop='6px';
  selectionLine.textContent=`服务器回显选择：${selection.mode||'--'} · selected ${formatCount(serverSelected.length)} [${serverSelected.join(', ')||'--'}] · effective ${formatCount(effectiveSelected.length)} [${effectiveSelected.join(', ')||'--'}] · selection hash ${selection.selection_hash||'--'}`; body.appendChild(selectionLine);
  const quality=readiness?.data_quality;
  if(quality){ const counts=quality.label_counts||{}; const eligibleCounts=quality.eligible_class_counts||counts; const data=document.createElement('div'); data.className='compact muted'; data.style.marginTop='6px';
    data.textContent=`可用会话 ${formatCount(quality.eligible_session_count)} · 有效超声波 ${formatCount(quality.valid_ultrasonic_samples)} · 排除无效 ${formatCount(quality.excluded_invalid_ultrasonic_samples)} · 可训练 SAFE ${formatCount(eligibleCounts.safe)} · 可训练 DANGER ${formatCount(eligibleCounts.danger)} · RAW UNKNOWN ${formatCount(counts.unknown)} · 覆盖 ${formatPercent(quality.label_coverage)}`; body.appendChild(data); }
  if(quality){ const scenarios=document.createElement('div'); scenarios.className='compact muted'; scenarios.style.marginTop='6px';
    scenarios.textContent=`冻结场景 ${formatCount(quality.scenario_configured_session_count)} · 缺场景 ${formatCount(quality.missing_scenario_session_count)} · 独立 14-feature 场景 ${formatCount(quality.distinct_scenario_count)} · SAFE 会话 ${formatCount(quality.safe_session_count)} · DANGER 会话 ${formatCount(quality.danger_session_count)} · 混合标签会话 ${formatCount(quality.mixed_label_session_count)}`; body.appendChild(scenarios);
    const diversity=quality.scenario_distinct_values||{}; const entries=Object.entries(diversity);
    if(entries.length){ const diversityLine=document.createElement('div'); diversityLine.className='compact muted'; diversityLine.style.marginTop='6px';
      diversityLine.textContent=`模拟环境每字段不同值：${entries.map(([key,value])=>`${key}=${formatCount(value)}`).join(' · ')}`; body.appendChild(diversityLine); }
    if((quality.missing_scenario_session_ids||[]).length){ const missing=document.createElement('div'); missing.className='compact'; missing.style.marginTop='6px';
      missing.textContent=`缺少冻结场景的会话：${quality.missing_scenario_session_ids.join(', ')}`; body.appendChild(missing); }
  }
  const contract=document.createElement('div'); contract.className='compact muted'; contract.style.marginTop='6px';
  contract.textContent=`模型特征 ${formatCount(readiness?.feature_count)} 项 · 硬阻断由当前选择的可切分性决定 · 12/30 会话是证据质量建议，不是固定门槛 · 有效样本量以会话为主`; body.appendChild(contract);
  if(readiness?.planned_split){ const split=document.createElement('div'); split.className='compact muted'; split.style.marginTop='6px';
    split.textContent=`计划划分：${readiness.planned_split.strategy||'--'} · train ${(readiness.planned_split.train_sessions||[]).join(', ')||'--'} · test ${(readiness.planned_split.test_sessions||[]).join(', ')||'--'} · session overlap ${(readiness.planned_split.session_overlap||[]).length} · scenario group overlap ${(readiness.planned_split.scenario_group_overlap||[]).length} · train/test scenario groups ${formatCount(readiness.planned_split.train_scenario_group_count)}/${formatCount(readiness.planned_split.test_scenario_group_count)} · 跨场景泛化 ${readiness.planned_split.scenario_generalization_evaluable===true?'可评估':'不可评估'} · 环境效应 ${readiness.planned_split.environment_effects_learnable===true?'可学习':'不可学习'}`; body.appendChild(split); }
  box.replaceChildren(dot,body); const trainButton=$('trainSimulationModel'); trainButton.disabled=!ready||!selectedTrainingSessionIdsInOrder().length;
  if(trainButton.dataset.busy!=='1') trainButton.textContent=ready?'训练第三模型':'训练条件不足';
}
function renderNoTrainingSelection(){
  trainingReadinessRequestSerial+=1; simulationReadiness=null; const box=$('trainingReadiness'); box.className='readiness blocked';
  const dot=document.createElement('span'); dot.textContent='▲'; const body=document.createElement('div'); const title=document.createElement('strong'); title.textContent='请先勾选至少一个已结束会话';
  const detail=document.createElement('div'); detail.className='compact'; detail.textContent='当前选择为空；浏览器不会发送全量 readiness 或训练请求。'; body.append(title,detail); box.replaceChildren(dot,body);
  const trainButton=$('trainSimulationModel'); trainButton.disabled=true; if(trainButton.dataset.busy!=='1') trainButton.textContent='先选择训练会话';
}
function trainingReadinessUrl(sessionIds,version){
  let url=`/api/v1/simulations/training-readiness?device_id=${DEVICE}&label_version=${version}`;
  sessionIds.forEach(sessionId=>{ url+=`&session_id=${encodeURIComponent(sessionId)}`; }); return url;
}
async function loadTrainingReadiness(){
  const sessionIds=selectedTrainingSessionIdsInOrder();
  if(!sessionIds.length){ renderNoTrainingSelection(); return; }
  const serial=++trainingReadinessRequestSerial; simulationReadiness=null; const button=$('trainSimulationModel'); button.disabled=true;
  if(button.dataset.busy!=='1') button.textContent='检查选中会话…';
  try {
    const version=Math.max(1,Number($('labelVersion').value)||1);
    const readiness=await fetchJson(trainingReadinessUrl(sessionIds,version));
    if(serial!==trainingReadinessRequestSerial) return; renderTrainingReadiness(readiness);
  } catch(error){
    if(serial!==trainingReadinessRequestSerial) return;
    renderTrainingReadiness({ready:false,blockers:[String(error)],warnings:[],selection:{mode:'explicit',selected_session_ids:sessionIds,selection_hash:''},evidence_quality:{tier:'blocked',summary:'服务器未接受当前会话选择。',criteria:{}},data_quality:null});
  }
}
async function loadSimulationSessions({throwOnError=false}={}){
  const previous=$('simulationSession').value;
  try {
    const version=Math.max(1,Number($('labelVersion').value)||1);
    const overview=await fetchJson(`/api/v1/simulations/overview?device_id=${DEVICE}&label_version=${version}`);
    simulationOverview=overview; renderOverview(overview);
    simulationSessions=(overview.sessions||[]).map(normaliseSessionSummary);
    reconcileTrainingSessionSelection();
    populateSensorSessionSelectors();
    updateScenarioControls();
    const options=simulationSessions.map(session=>{
      const option=document.createElement('option'); option.value=session.session_id;
      option.textContent=`${session.name} · ${session.state} · ${formatCount(session.sample_count)} samples`; return option;
    });
    $('simulationSession').replaceChildren(...options);
    if(simulationSessions.some(session=>session.session_id===previous)) $('simulationSession').value=previous;
    if(!simulationSessions.length){
      selectedSimulationSession=null; $('simulationStatus').textContent='尚无会话；请在 ESP32 的 COLLECTION 页面触摸“开始采集”。';
      renderSessionList(); emptyTimeline(); renderNoTrainingSelection(); populateSensorSessionSelectors(); return;
    }
    await Promise.all([loadSimulationDetails(),loadTrainingReadiness()]);
  } catch(error){
    $('simulationStatus').textContent=String(error); $('trainSimulationModel').disabled=true;
    renderTrainingReadiness({ready:false,blockers:['无法从服务器读取会话'],warnings:[],selection:{mode:'explicit',selected_session_ids:selectedTrainingSessionIdsInOrder(),selection_hash:''},evidence_quality:{tier:'blocked',summary:String(error),criteria:{}},data_quality:null});
    if(throwOnError) throw error;
  }
}
async function stopSelectedSimulation(){
  if(!selectedSimulationSession||selectedSimulationSession.state!=='active') return;
  try {
    await sendJson(`/api/v1/simulations/sessions/${encodeURIComponent(selectedSimulationSession.session_id)}/stop`,'POST',{device_id:DEVICE});
    $('labelStatus').textContent='采集会话已结束，现在可以在曲线上选择时间段并人工标注。'; await loadSimulationSessions();
  } catch(error){ $('labelStatus').textContent=String(error); }
}
async function saveSimulationLabel(){
  if(!selectedSimulationSession||selectedSimulationSession.state!=='completed'){
    $('labelStatus').textContent='必须先结束采集会话，才能进行后台人工标注。'; return;
  }
  if(!selectedScenarioSnapshotRecord){
    $('labelStatus').textContent='该会话没有不可修改的模拟海岸快照，不能标注或进入 22 特征训练。'; return;
  }
  const startText=$('labelStartSeq').value; const endText=$('labelEndSeq').value;
  const start=Number(startText); const end=Number(endText); const version=Number($('labelVersion').value);
  if(!startText||!endText||!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<start||!Number.isInteger(version)||version<1){
    $('labelStatus').textContent='请填写有效的起始/结束序号和标签版本。'; return;
  }
  try {
    const saved=await sendJson('/api/v1/simulations/labels','PUT',{session_id:selectedSimulationSession.session_id,
      device_id:DEVICE,start_seq:start,end_seq:end,label:$('simulationLabel').value,note:$('labelNote').value.trim(),version});
    $('labelStatus').textContent=`已保存人工标签：${saved.label.toUpperCase()} · #${saved.start_seq}–#${saved.end_seq} · version ${saved.version}`;
    await loadSimulationSessions();
  } catch(error){ $('labelStatus').textContent=String(error); }
}
function setMetric(id,value,kind='percent'){
  const number=asNumber(value); $(id).textContent=number===null?'--':(kind==='percent'?formatPercent(number):(number.toFixed(4)));
}
function clearTrainingMetrics(){
  ['metricBalanced','metricPrecision','metricRecall','metricF1','metricBrier','metricLogLoss','metricSpecificity','metricNpv','metricRocAuc','metricFpr','metricFnr','metricThreshold','matrixTrueSafe','matrixFalseDanger','matrixFalseSafe','matrixTrueDanger'].forEach(id=>$(id).textContent='--');
  $('trainingResultStatus').textContent='尚无可验证的自定义模型工件；这里不会显示占位或估算指标。';
  $('trainingSplitDetails').innerHTML='<strong>评估尚未运行</strong><br>训练成功后展示真实的会话隔离、样本量、配置和模型哈希。';
  $('modelComparison').textContent='等待服务器模型指标。';
  $('ablationComparison').textContent='Ablation unavailable — 服务器尚未返回超声波单独模型。';
  $('environmentAblationComparison').textContent='Ablation unavailable — 服务器尚未返回环境单独模型。';
  $('baselineComparison').textContent='Baseline unavailable — 服务器尚未返回阈值对照实验。';
  $('comparisonDelta').textContent='同一测试会话上的差值将在训练后显示；正值不一定代表所有安全指标都改善。';
}
function compactMetricSummary(metrics,includeProbabilityMetrics=true){
  if(!metrics||typeof metrics!=='object') return '指标不可用';
  const parts=[];
  if(asNumber(metrics.balanced_accuracy)!==null) parts.push(`balanced accuracy ${formatPercent(metrics.balanced_accuracy)}`);
  if(asNumber(metrics.danger_precision)!==null) parts.push(`precision ${formatPercent(metrics.danger_precision)}`);
  if(asNumber(metrics.danger_recall)!==null) parts.push(`recall ${formatPercent(metrics.danger_recall)}`);
  if(asNumber(metrics.danger_f1)!==null) parts.push(`F1 ${formatPercent(metrics.danger_f1)}`);
  if(includeProbabilityMetrics&&asNumber(metrics.brier_score)!==null) parts.push(`Brier ${Number(metrics.brier_score).toFixed(4)}`);
  if(includeProbabilityMetrics&&asNumber(metrics.log_loss)!==null) parts.push(`log loss ${Number(metrics.log_loss).toFixed(4)}`);
  if(asNumber(metrics.specificity)!==null) parts.push(`specificity ${formatPercent(metrics.specificity)}`);
  if(asNumber(metrics.negative_predictive_value)!==null) parts.push(`NPV ${formatPercent(metrics.negative_predictive_value)}`);
  if(includeProbabilityMetrics&&asNumber(metrics.roc_auc)!==null) parts.push(`ROC AUC ${Number(metrics.roc_auc).toFixed(4)}`);
  if(asNumber(metrics.false_positive_rate)!==null) parts.push(`FPR ${formatPercent(metrics.false_positive_rate)}`);
  if(asNumber(metrics.false_negative_rate)!==null) parts.push(`FNR ${formatPercent(metrics.false_negative_rate)}`);
  return parts.length?parts.join(' · '):'指标不可用';
}
function compactDeltaSummary(delta,includeProbabilityMetrics=true){
  if(!delta||typeof delta!=='object') return '差值不可用';
  const probabilityMetrics=new Set(['brier_score','brier_score_reduction','log_loss','log_loss_reduction','roc_auc']);
  const values=Object.entries(delta)
    .filter(([key,value])=>asNumber(value)!==null&&(includeProbabilityMetrics||!probabilityMetrics.has(key)))
    .map(([key,value])=>`${key} ${Number(value)>=0?'+':''}${Number(value).toFixed(4)}`);
  return values.length?values.join(', '):'差值不可用';
}
function twoLevelMetricSummary(rowLevel,sessionMacro,includeProbabilityMetrics=true){
  const sessionCount=asNumber(sessionMacro?.session_count);
  const macroLabel=sessionCount===null?'session count --':`${formatCount(sessionCount)} test sessions`;
  return `row-level: ${compactMetricSummary(rowLevel,includeProbabilityMetrics)} · session-macro (PRIMARY SCIENTIFIC VIEW · ${macroLabel}): ${compactMetricSummary(sessionMacro,includeProbabilityMetrics)}`;
}
function renderTrainingMetrics(result,provenance='本次训练响应'){
  const metrics=result?.metrics||{}; const test=metrics.test||{}; const confusion=test.confusion||{};
  setMetric('metricBalanced',test.balanced_accuracy); setMetric('metricPrecision',test.danger_precision);
  setMetric('metricRecall',test.danger_recall); setMetric('metricF1',test.danger_f1);
  setMetric('metricBrier',test.brier_score,'score'); setMetric('metricLogLoss',test.log_loss,'score');
  setMetric('metricSpecificity',test.specificity); setMetric('metricNpv',test.negative_predictive_value);
  setMetric('metricRocAuc',test.roc_auc,'score'); setMetric('metricFpr',test.false_positive_rate);
  setMetric('metricFnr',test.false_negative_rate); setMetric('metricThreshold',test.threshold??test.decision_threshold,'score');
  $('matrixTrueSafe').textContent=formatCount(confusion.true_safe); $('matrixFalseDanger').textContent=formatCount(confusion.false_danger);
  $('matrixFalseSafe').textContent=formatCount(confusion.false_safe); $('matrixTrueDanger').textContent=formatCount(confusion.true_danger);
  const artifactHash=result.artifact_hash||result.hash; const datasetHash=result.dataset_hash||result.source_manifest?.dataset_hash;
  const selection=result.selection||result.source_manifest?.selection||{}; const evidence=result.evidence_quality||result.source_manifest?.evidence_quality||{};
  const selectedIds=selection.selected_session_ids||[]; const effectiveIds=selection.effective_session_ids||selectedIds; const requestedIds=selection.requested_session_ids||selectedIds;
  const tierLabels={blocked:'BLOCKED',exploratory:'EXPLORATORY',course_demo:'COURSE DEMO',stronger_demo:'STRONGER DEMO'};
  const evidenceTier=tierLabels[evidence.tier]||String(evidence.tier||'--').toUpperCase().replaceAll('_',' ');
  $('trainingResultStatus').textContent=`${provenance} · ${result.model_id||'custom-water-logreg-v1'} ${result.version||'--'} · ${evidenceTier} · SIMULATION / SHADOW · 创建 ${result.created_at?new Date(result.created_at).toLocaleString():'--'}`;
  const detailBox=$('trainingSplitDetails'); detailBox.replaceChildren();
  const heading=document.createElement('strong'); heading.textContent='可审计训练与测试信息'; detailBox.appendChild(heading);
  const details=[
    `训练选择：${selection.mode||'--'} · selected ${formatCount(selectedIds.length)} sessions [${selectedIds.join(', ')||'--'}] · effective ${formatCount(effectiveIds.length)} [${effectiveIds.join(', ')||'--'}]`,
    `选择 provenance：requested ${requestedIds.join(', ')||'--'} · selection hash ${selection.selection_hash||'--'} · device ${result.source_manifest?.device_id||DEVICE} · label version ${result.source_manifest?.label_version??result.training_config?.label_version??'--'}`,
    `证据等级：${evidenceTier} · scope ${evidence.evaluation_scope||'--'} · cross-scenario generalization ${evidence.scenario_generalization_evaluable===true?'可评估':(evidence.scenario_generalization_evaluable===false?'不可评估':'--')} · environment effects ${evidence.environment_effects_learnable===true?'可学习':(evidence.environment_effects_learnable===false?'不可学习，环境系数不可解释':'--')} · ${evidence.summary||'--'}`,
    `划分：${metrics.split_strategy||'--'}（会话重叠 ${Array.isArray(metrics.session_overlap)?metrics.session_overlap.length:'--'}）`,
    `样本：train ${formatCount(metrics.train_samples)} · test ${formatCount(metrics.test_samples)} · labelled ${formatCount(metrics.labelled_samples)} · excluded UNKNOWN ${formatCount(metrics.excluded_unknown_samples)}`,
    `会话：train ${(metrics.train_sessions||[]).join(', ')||'--'} · test ${(metrics.test_sessions||[]).join(', ')||'--'}`,
    `配置：22-feature fusion · window ${metrics.window_size??result.training_config?.window_size??'--'} · threshold ${test.threshold??result.training_config?.decision_threshold??'--'} · random state ${metrics.random_state??result.training_config?.random_state??'--'} · train/test scenario groups ${formatCount(metrics.train_scenario_group_count??result.training_config?.train_scenario_group_count)}/${formatCount(metrics.test_scenario_group_count??result.training_config?.test_scenario_group_count)}`,
    `模型 hash ${shortHash(artifactHash)} · 数据集 hash ${shortHash(datasetHash)}`
  ];
  details.forEach(text=>{ const line=document.createElement('div'); line.textContent=text; detailBox.appendChild(line); });
  $('modelComparison').textContent=twoLevelMetricSummary(test,metrics.test_session_macro);
  const baseline=metrics.baselines?.water_rise_threshold; const baselineMetrics=baseline?.test||baseline?.metrics?.test||baseline;
  const baselineSessionMacro=baseline?.test_session_macro||baseline?.metrics?.test_session_macro;
  if(baseline&&typeof baseline==='object'){
    const extras=[]; const threshold=baseline.threshold_mm??baseline.threshold??baseline.config?.threshold_mm;
    if(asNumber(threshold)!==null) extras.push(`阈值 ${threshold} mm`);
    const delta=metrics.delta_vs_baseline||baseline.delta_vs_logistic_regression;
    if(delta&&typeof delta==='object'){
      extras.push(`模型差值 ${compactDeltaSummary(delta,false)}`);
    }
    extras.push('hard classifier · Brier / log loss / ROC AUC 不适用');
    $('baselineComparison').textContent=[twoLevelMetricSummary(baselineMetrics,baselineSessionMacro,false),...extras].join(' · ');
  } else $('baselineComparison').textContent='Baseline unavailable — 服务器当前模型工件未提供阈值对照指标。';
  const ultrasonicAblation=metrics.baselines?.ultrasonic_only_logistic_regression||metrics.baselines?.ultrasonic_only_logreg;
  const ultrasonicTest=ultrasonicAblation?.test||ultrasonicAblation?.metrics?.test;
  const ultrasonicSessionMacro=ultrasonicAblation?.test_session_macro||ultrasonicAblation?.metrics?.test_session_macro;
  $('ablationComparison').textContent=ultrasonicAblation
    ?`${twoLevelMetricSummary(ultrasonicTest,ultrasonicSessionMacro)} · same split · ${Array.isArray(ultrasonicAblation.feature_order)?ultrasonicAblation.feature_order.length:'8'} features`
    :'Ablation unavailable — 服务器当前模型工件未提供超声波单独模型。';
  const environmentAblation=metrics.baselines?.environment_only_logistic_regression||metrics.baselines?.environment_only_logreg;
  const environmentTest=environmentAblation?.test||environmentAblation?.metrics?.test;
  const environmentSessionMacro=environmentAblation?.test_session_macro||environmentAblation?.metrics?.test_session_macro;
  $('environmentAblationComparison').textContent=environmentAblation
    ?`${twoLevelMetricSummary(environmentTest,environmentSessionMacro)} · same split · ${Array.isArray(environmentAblation.feature_order)?environmentAblation.feature_order.length:'14'} features`
    :'Ablation unavailable — 服务器当前模型工件未提供环境单独模型。';
  $('comparisonDelta').textContent=`Row-level deltas only — Combined − ultrasonic-only: ${compactDeltaSummary(metrics.delta_vs_ultrasonic_only)} · Combined − environment-only: ${compactDeltaSummary(metrics.delta_vs_environment_only)} · Combined − water-rise threshold (classification metrics only): ${compactDeltaSummary(metrics.delta_vs_baseline,false)}`;
}
async function trainSimulationModel(){
  const version=Math.max(1,Number($('labelVersion').value)||1); const button=$('trainSimulationModel'); const sessionIds=selectedTrainingSessionIdsInOrder();
  if(!sessionIds.length){ $('trainingResultStatus').textContent='当前没有勾选训练会话；请先在会话卡中选择，浏览器不会默认使用全部数据。'; renderNoTrainingSelection(); return; }
  if(!simulationReadiness?.ready){ $('trainingResultStatus').textContent='服务器尚未确认训练条件；训练请求未发送。'; return; }
  button.dataset.busy='1'; button.disabled=true; button.textContent='训练与评估中…'; $('trainingResultStatus').textContent='服务器正在按完整会话切分、训练、评估并保存带哈希的模型工件…';
  try {
    const result=await sendJson('/api/v1/simulations/train','POST',{device_id:DEVICE,label_version:version,session_ids:sessionIds});
    await Promise.all([loadModels(),loadSimulationSessions()]); renderTrainingMetrics(result,'本次服务器训练响应');
    $('labelStatus').textContent=`训练完成：${result.model_id}-${result.version} · ${formatCount(result.labelled_sample_count)} 个已标注样本 · SIMULATION / SHADOW`;
  } catch(error){ $('trainingResultStatus').textContent=String(error); }
  finally { delete button.dataset.busy; button.textContent='训练第三模型'; button.disabled=!simulationReadiness?.ready||!selectedTrainingSessionIdsInOrder().length; }
}
async function refreshEnvironment(){
  const environment=await fetchJson(`/api/v1/environment?device_id=${DEVICE}`);
  setEnvironment(environment); lastEnvironmentFetch=Date.now();
}
async function refresh(){
  try {
    const [latest,history]=await Promise.all([fetchJson(`/api/v1/telemetry/latest?device_id=${DEVICE}`),fetchJson(`/api/v1/telemetry?device_id=${DEVICE}&limit=20`)]);
    setLatest(latest); setHistory(history); $('error').textContent='';
  } catch(e) { $('online').textContent='等待遥测'; $('online').className='status offline'; if(!String(e).includes('404')) $('error').textContent=e; }
  if(Date.now()-lastEnvironmentFetch>60000){ try { await refreshEnvironment(); } catch(e){ $('error').textContent=e; } }
}
$('searchLocation').addEventListener('click',searchLocations);
$('locationQuery').addEventListener('keydown',event=>{ if(event.key==='Enter') searchLocations(); });
$('saveLocation').addEventListener('click',saveLocation);
$('saveScenario').addEventListener('click',saveDeviceScenario);
$('clearScenarioForm').addEventListener('click',()=>{ clearScenarioInputs(); $('scenarioStatus').textContent='表单已在浏览器中清空，服务器当前场景未改变。'; });
$('deleteDeviceScenario').addEventListener('click',deleteDeviceScenario);
$('importScenario').addEventListener('click',importScenarioIntoForm);
$('simulationSession').addEventListener('change',loadSimulationDetails);
$('reloadSimulations').addEventListener('click',loadSimulationSessions);
$('stopSimulation').addEventListener('click',stopSelectedSimulation);
$('saveSimulationLabel').addEventListener('click',saveSimulationLabel);
$('selectAllTrainingSessions').addEventListener('click',()=>{
  selectedTrainingSessionIds=new Set(completedTrainingSessions().map(session=>session.session_id));
  renderSessionList(); loadTrainingReadiness();
});
$('clearTrainingSessionSelection').addEventListener('click',()=>{
  selectedTrainingSessionIds.clear(); renderSessionList(); renderNoTrainingSelection();
});
$('trainSimulationModel').addEventListener('click',trainSimulationModel);
$('simulationChart').addEventListener('click',selectChartSequence);
$('clearSelection').addEventListener('click',()=>{ $('labelStartSeq').value=''; $('labelEndSeq').value=''; updateSelectionVisuals(); });
$('labelStartSeq').addEventListener('input',updateSelectionVisuals);
$('labelEndSeq').addEventListener('input',updateSelectionVisuals);
$('labelVersion').addEventListener('change',loadSimulationSessions);
$('officialDataset').addEventListener('change',loadSelectedOfficialDataset);
$('officialSites').addEventListener('change',()=>{ renderOfficialEvidenceScope(); loadOfficialTrainingReadiness(); });
$('rescanOfficialDatasets').addEventListener('click',rescanOfficialDatasets);
$('trainOfficialModel').addEventListener('click',trainOfficialModel);
$('refreshOfficialRuns').addEventListener('click',loadOfficialTrainingRuns);
$('activateOfficialRun').addEventListener('click',activateOfficialRun);
$('sensorContextId').addEventListener('change',applySelectedSensorContext);
$('sensorStation').addEventListener('change',()=>{
  const station=$('sensorStation').value;
  const context=frozenSensorContexts().find(item=>String(item.station_id||item.site_id||'')===station);
  if(context) $('sensorContextId').value=sensorContextId(context);
  applySelectedSensorContext();
});
$('sensorProfileMode').addEventListener('change',updateSensorProfileControls);
$('sensorGain').addEventListener('input',()=>{ updateSensorProfileControls(); updateSensorMappingPreview(); });
$('sensorReferenceLevel').addEventListener('input',()=>{ updateSensorProfileControls(); updateSensorMappingPreview(); });
$('sensorCalibrationSession').addEventListener('change',updateSensorProfileControls);
$('freezeSensorProfile').addEventListener('click',freezeSensorProfile);
$('clearSensorProfile').addEventListener('click',clearSensorProfile);
$('sensorTestSession').addEventListener('change',updateSensorTestControls);
$('runSensorExternalTest').addEventListener('click',runSensorExternalTest);
$('refreshSensorTestRuns').addEventListener('click',loadSensorTestRuns);
$('logoutAdmin').addEventListener('click',logoutAdmin);
async function bootstrap(){
  try { await loadAdminSession(); }
  catch(error){ if(!ADMIN_MODE) $('error').textContent=String(error); return; }
  if(!ADMIN_MODE){ $('officialTrainingConsole').hidden=true; $('sensorExternalTestConsole').hidden=true; $('simulationPanel').hidden=true; }
  else { loadOfficialDatasets(); loadOfficialTrainingRuns(); loadOfficialModel().then(loadSensorProfile); loadSensorTestRuns(); }
  loadLocationConfig(); loadModels(); loadDeviceScenario(); loadSimulationSessions(); refresh();
  setInterval(refresh,2000); setInterval(loadSimulationSessions,5000); setInterval(loadModels,30000);
}
bootstrap();
</script></body></html>"""


def render_dashboard(*, api_prefix: str = "", admin_mode: bool = False) -> str:
    prefix = api_prefix.rstrip("/")
    if prefix and (not prefix.startswith("/") or ".." in prefix):
        raise ValueError("dashboard API prefix must be an absolute application path")
    rendered = _DASHBOARD_TEMPLATE.replace(
        "__COASTWATCH_ADMIN_MODE__", "true" if admin_mode else "false"
    ).replace("__COASTWATCH_ADMIN_BASE__", json.dumps(prefix))
    if prefix:
        rendered = rendered.replace("/api/v1/", f"{prefix}/api/v1/")
    return rendered


DASHBOARD_HTML = render_dashboard()
