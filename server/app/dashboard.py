import json

_DASHBOARD_TEMPLATE = r"""<!doctype html>
<html lang="en-GB">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CoastWatch Great Yarmouth Monitoring Console</title>
  <style>
    :root { color-scheme: dark; --bg:#07131b; --panel:#102530; --line:#214452;
      --text:#e7f7fb; --muted:#87a8b3; --safe:#29d391; --warn:#ffb84d;
      --danger:#ff5b61; --fault:#c584ff; --accent:#4bd6ff; --ink:#08161d; }
    * { box-sizing:border-box; }
    [hidden] { display:none !important; }
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
    .session-training-toggle { display:none !important; gap:8px; align-items:center; border-top:1px solid var(--line);
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
  <header><div><h1>CoastWatch Monitoring Console</h1><div class="sub">Great Yarmouth, England · Device COAST_01</div></div>
    <div><div id="online" class="status offline">Waiting for telemetry</div>
      <div id="adminControls" class="tool-row" style="display:none;margin-top:8px;justify-content:flex-end"><span id="adminIdentity" class="muted"></span><button id="logoutAdmin" class="secondary compact" type="button">Sign out</button></div>
    </div></header>
  <section class="grid">
    <div class="card"><div class="label">Alarm status</div><div id="alarm" class="value alarm">--</div></div>
    <div class="card"><div class="label">Sensor gap</div><div class="value"><span id="distance">--</span><span class="unit">mm</span></div></div>
    <div class="card"><div class="label">Water rise</div><div class="value"><span id="rise">--</span><span class="unit">mm</span></div></div>
    <div class="card"><div class="label">Rise rate</div><div class="value"><span id="rate">--</span><span class="unit">mm/s</span></div></div>
    <div class="card"><div class="label">Person detection</div><div id="person" class="value">--</div></div>
    <div class="card"><div class="label">Wi-Fi signal</div><div class="value"><span id="rssi">--</span><span class="unit">dBm</span></div></div>
    <div class="card"><div class="label">Sequence / uptime</div><div id="sequence" class="value" style="font-size:20px">--</div></div>
    <div class="card"><div class="label">Live environment source</div><div id="environment" class="value" style="font-size:17px">--</div></div>
  </section>
  <section class="panel"><h2>Device health</h2><div id="health" class="chips"></div>
    <div id="updated" class="muted" style="margin-top:12px">No data received</div></section>

  <section class="panel strategy-console" id="officialTrainingConsole" aria-labelledby="officialTrainingHeading">
    <div class="section-heading">
      <div><div class="eyebrow">UK OFFICIAL DATA · MANUAL TRAINING</div><h2 id="officialTrainingHeading">Official coastal model training</h2>
        <div class="muted compact">Train Logistic Regression only from registered, audited UK coastal datasets.</div></div>
      <div class="badges"><span class="badge safe">OFFICIAL DATA ONLY</span><span class="badge research">LOGISTIC REGRESSION</span><span class="badge warn">SHADOW ONLY</span></div>
    </div>
    <div class="notice">The output is an <strong>extreme sea-level condition probability</strong>, not a tsunami, flood, or public-warning probability.</div>
    <div class="notice provenance-disclosure"><strong>Provenance boundary:</strong> the server verifies raw bytes, SHA-256 values, and manifest structure. Ownership, licensing, harmonisation, and label derivation remain operator-attested.</div>
    <div class="notice"><strong>18-feature contract:</strong> one relative-water-level feature may be replaced by the sensor mapping; the other 17 official context features remain frozen.</div>
    <div class="boundary-banner" aria-label="Training and external-test boundary">
      <div class="boundary-step"><span class="badge safe">1 · FIT</span><strong>Official training period</strong><small>Fit the scaler and Logistic Regression parameters.</small></div>
      <div class="boundary-step"><span class="badge research">2 · EVALUATE</span><strong>Validation and frozen test</strong><small>Select the threshold on validation data; report final metrics on the frozen test set.</small></div>
      <div class="boundary-step"><span class="badge warn">3 · EXTERNAL TEST</span><strong>ESP32 is excluded from training</strong><small>Ultrasonic readings are used only after model freezing.</small></div>
    </div>
    <div class="invariant" id="officialLeakageInvariant" role="status">SENSOR ROWS USED FOR FIT = 0 · SCALER = 0 · THRESHOLD = 0</div>

    <div class="console-grid" style="margin-top:14px">
      <div class="console-pane">
        <div class="section-heading"><h3>1. Select official data</h3><button id="rescanOfficialDatasets" class="secondary compact" type="button">Rescan protected data</button></div>
        <div class="form-grid">
          <label class="field span-2">Registered official dataset<select id="officialDataset" aria-describedby="officialDatasetStatus"><option value="">Loading…</option></select></label>
          <label class="field span-2">UK official sites (Ctrl / Cmd for multi-select; Great Yarmouth prioritised)<select id="officialSites" multiple aria-label="Select one or more UK official sites"></select></label>
          <label class="field">Training start<input id="officialTrainStart" type="datetime-local" readonly></label>
          <label class="field">Training end<input id="officialTrainEnd" type="datetime-local" readonly></label>
          <label class="field">Validation start<input id="officialValidationStart" type="datetime-local" readonly></label>
          <label class="field">Validation end<input id="officialValidationEnd" type="datetime-local" readonly></label>
          <label class="field">Frozen-test start<input id="officialTestStart" type="datetime-local" readonly></label>
          <label class="field">Frozen-test end<input id="officialTestEnd" type="datetime-local" readonly></label>
        </div>
        <div id="officialDatasetStatus" class="muted compact" style="margin-top:10px" aria-live="polite">Loading registered datasets. Manifest splits are read-only.</div>
        <div id="officialRescanStatus" class="readiness blocked" style="margin-top:10px" aria-live="polite"><span>●</span><div><strong>Not rescanned</strong><div class="compact">Bundle validation errors will be reported here.</div></div></div>
        <div class="provenance-grid" aria-label="Official data provenance and integrity">
          <div class="provenance-item"><span class="label">Official source</span><strong id="officialSource">—</strong></div>
          <div class="provenance-item"><span class="label">Licence / redistribution</span><strong id="officialLicense">—</strong></div>
          <div class="provenance-item"><span class="label">Dataset SHA-256</span><strong id="officialDatasetHash">—</strong></div>
          <div class="provenance-item"><span class="label">Sites / dates / rows</span><strong id="officialCoverage">—</strong></div>
          <div class="provenance-item"><span class="label">Target definition</span><strong id="officialLabelDefinition">—</strong></div>
          <div class="provenance-item"><span class="label">Splits and leakage control</span><strong id="officialSplitDefinition">—</strong></div>
          <div class="provenance-item"><span class="label">Dataset provenance assurance</span><strong id="officialDatasetProvenance">—</strong></div>
          <div class="provenance-item"><span class="label">Deterministic importer replay</span><strong id="officialDatasetImporterReplay">—</strong></div>
          <div class="provenance-item span-2"><span class="label">Selected site and evidence scope</span><strong id="officialEvidenceScope">No site selected</strong></div>
        </div>
        <div id="officialReadiness" class="readiness blocked" aria-live="polite"><span>●</span><div><strong>Not checked</strong><div class="compact">The server will validate provenance, hashes, labels, and time splits.</div></div></div>
        <ul id="officialBlockers" class="blocker-list" aria-label="Training blockers"></ul>
        <div class="tool-row"><button id="trainOfficialModel" type="button" disabled>Train official model</button><span class="muted compact">No ESP32 session is used for fitting.</span></div>
      </div>

      <div class="console-pane">
        <div class="section-heading"><h3>2. Runs, frozen testing, and activation</h3><div class="tool-row" style="margin-top:0"><button id="refreshOfficialRuns" class="secondary compact" type="button">Refresh runs</button><button id="activateOfficialRun" type="button" disabled>Activate as Shadow</button></div></div>
        <div id="officialRunStatus" class="readiness blocked" aria-live="polite"><span>●</span><div><strong>No run selected</strong><div class="compact">Only successful, activation-ready runs may be activated.</div></div></div>
        <div class="run-history" id="officialRunHistory" aria-label="Official model training runs"><div class="muted">Loading run history…</div></div>
        <div id="officialBaselineVerdict" class="notice" style="margin-top:12px" role="status" aria-live="polite"><strong>Does machine learning outperform a simple threshold?</strong><br>Waiting for the server-side frozen-test comparison.</div>
        <div class="metrics-grid" aria-label="Official frozen-test metrics">
          <div class="metric-card"><small>Site-macro PR AUC ↑</small><strong id="officialMetricPRAuc">—</strong><small>Primary rare-condition metric</small></div>
          <div class="metric-card"><small>Site-macro DANGER recall</small><strong id="officialMetricRecall">—</strong><small>Unsafe-class sensitivity</small></div>
          <div class="metric-card"><small>Site-macro DANGER precision</small><strong id="officialMetricPrecision">—</strong><small>False-alarm cost</small></div>
          <div class="metric-card"><small>Site-macro DANGER F1</small><strong id="officialMetricF1">—</strong><small>Recall/precision balance</small></div>
          <div class="metric-card"><small>Site-macro ROC AUC</small><strong id="officialMetricRocAuc">—</strong><small>Ranking performance</small></div>
          <div class="metric-card"><small>Row-level companion Brier ↓</small><strong id="officialMetricBrier">—</strong><small>Probability calibration</small></div>
          <div class="metric-card"><small>False-positive rows / day ↓</small><strong id="officialMetricFalsePositiveRows">—</strong><small>Misclassified rows, not warning events</small></div>
          <div class="metric-card"><small>Decision threshold</small><strong id="officialMetricThreshold">—</strong><small>Selected on validation data only</small></div>
          <div class="metric-card"><small>Site-macro coverage</small><strong id="officialMetricSiteCoverage">—</strong><small>Eligible / selected sites</small></div>
        </div>
        <div class="feature-groups" aria-label="Official model and baseline comparison">
          <div class="split-details"><strong>Logistic Regression</strong><div id="officialModelSummary">Waiting for frozen-test metrics.</div></div>
          <div class="split-details"><strong>Water-level Threshold Baseline</strong><div id="officialThresholdBaseline">Baseline unavailable.</div></div>
          <div class="split-details"><strong>Persistence Baseline</strong><div id="officialPersistenceBaseline">Baseline unavailable.</div></div>
        </div>
        <div class="split-details" style="margin-top:10px"><strong>Training-run provenance</strong><div id="officialRunProvenance" style="margin-top:5px">No training run.</div></div>
        <div class="split-details" style="margin-top:10px"><strong>Model-artifact provenance</strong><div id="officialArtifactProvenance" style="margin-top:5px">No artifact.</div></div>
      </div>
    </div>
  </section>

  <section class="panel strategy-console sensor-console" id="sensorExternalTestConsole" aria-labelledby="sensorExternalTestHeading">
    <div class="section-heading">
      <div><div class="eyebrow">HARDWARE-IN-THE-LOOP · POST-TRAINING ONLY</div><h2 id="sensorExternalTestHeading">Ultrasonic external-test console</h2>
        <div class="muted compact">Map measured water rise to the frozen official model without changing model parameters.</div></div>
      <div class="badges"><span class="badge safe">SENSOR ROWS FOR FIT = 0</span><span class="badge warn">EXTERNAL TEST</span><span class="badge research">LINEAR GAIN V1</span></div>
    </div>
    <div class="notice">The ultrasonic test never refits the model, retunes its threshold, or changes its artifact hash. Inference uses <strong>one sensor-mapped level feature and 17 frozen official context features</strong>.</div>
    <div class="console-grid">
      <div class="console-pane">
        <h3>1. Freeze a linear sensor profile</h3>
        <div class="form-grid">
          <label class="field span-2">Frozen official model<input id="sensorOfficialModel" readonly value="No active official model"></label>
          <label class="field">Great Yarmouth site<select id="sensorStation"><option value="">Select an official dataset first</option></select></label>
          <label class="field">Frozen official context<select id="sensorContextId"><option value="">Activate an official model first</option></select></label>
          <label class="field span-2">Mapping mode<select id="sensorProfileMode"><option value="formal">FORMAL — derived from an independent calibration session</option><option value="exploratory">EXPLORATORY — manual gain, excluded from formal metrics</option></select></label>
          <label class="field">Linear gain<input id="sensorGain" type="number" min="0.000001" step="0.000001" placeholder="Derived by the server"></label>
          <label class="field">Reference sea level (m)<input id="sensorReferenceLevel" type="number" step="0.000001" placeholder="Derived from the official artifact" readonly></label>
          <label class="field">Vertical datum<input id="sensorDatum" maxlength="40" placeholder="Derived from the official artifact" readonly></label>
          <label class="field">Independent calibration session<select id="sensorCalibrationSession"><option value="">Select a completed session</option></select></label>
        </div>
        <div class="formula" id="sensorMappingFormula">mapped_level_m = reference_level_m + gain × (water_rise_mm / 1000)</div>
        <div class="live-mapping" aria-label="Live ultrasonic mapping preview">
          <div class="summary-card"><small>RAW SENSOR</small><strong id="sensorRawRise">—</strong><small>water rise (mm)</small></div>
          <div class="summary-card"><small>MAPPED OFFICIAL SCALE</small><strong id="sensorMappedLevel">—</strong><small>proxy level (m)</small></div>
          <div class="summary-card"><small>TRAIN RANGE</small><strong id="sensorOodState">—</strong><small>Out-of-distribution values are not clipped</small></div>
        </div>
        <div class="tool-row"><button id="freezeSensorProfile" type="button" disabled>Freeze sensor profile</button><button id="clearSensorProfile" class="danger" type="button" disabled>Delete profile</button></div>
        <div id="sensorProfileStatus" class="muted compact" style="margin-top:10px" aria-live="polite">Freeze the profile, model hash, and official context before formal collection.</div>
        <div class="split-details" style="margin-top:10px"><strong>Mapping and calibration provenance</strong><div id="sensorProfileProvenance" style="margin-top:5px">No frozen profile.</div></div>
      </div>

      <div class="console-pane">
        <h3>2. Run a post-training external test</h3>
        <div class="form-grid">
          <label class="field span-2">Completed ESP32 collection session<select id="sensorTestSession"><option value="">Loading sessions…</option></select></label>
        </div>
        <div class="tool-row"><button id="runSensorExternalTest" type="button" disabled>Run external test</button><button id="refreshSensorTestRuns" class="secondary" type="button">Refresh results</button></div>
        <div id="sensorTestStatus" class="readiness blocked" aria-live="polite"><span>●</span><div><strong>Not run</strong><div class="compact">The test must use a profile frozen before collection.</div></div></div>
        <div class="metrics-grid" aria-label="Ultrasonic external-test metrics">
          <div class="metric-card"><small>Input rows</small><strong id="sensorMetricInputSamples">—</strong><small>Complete session input</small></div>
          <div class="metric-card"><small>Valid ultrasonic rows</small><strong id="sensorMetricValidSamples">—</strong><small>Valid sensor input</small></div>
          <div class="metric-card"><small>Evaluated rows</small><strong id="sensorMetricSamples">—</strong><small>Rows evaluated by the frozen model</small></div>
          <div class="metric-card"><small>Excluded invalid rows</small><strong id="sensorMetricInvalidSamples">—</strong><small>Invalid sensor rows excluded</small></div>
          <div class="metric-card"><small>OOD rate</small><strong id="sensorMetricOod">—</strong><small>Outside the official training range</small></div>
          <div class="metric-card"><small>Mapped min</small><strong id="sensorMetricMin">—</strong><small>proxy level (m)</small></div>
          <div class="metric-card"><small>Mapped max</small><strong id="sensorMetricMax">—</strong><small>proxy level (m)</small></div>
          <div class="metric-card"><small>Mean risk score</small><strong id="sensorMetricMeanRisk">—</strong><small>Model output, not a disaster probability</small></div>
          <div class="metric-card"><small>Inference latency</small><strong id="sensorMetricLatency">—</strong><small>ms / sample</small></div>
          <div class="metric-card"><small>Result rows aggregated</small><strong id="sensorMetricResultRows">—</strong><small>Rows used for complete statistics</small></div>
          <div class="metric-card"><small>Rows returned as preview</small><strong id="sensorMetricPreviewRows">—</strong><small>Bounded preview only</small></div>
        </div>
        <div class="split-details" style="margin-top:10px"><strong>Aggregation and preview policy</strong><div id="sensorTestEvaluationPolicy" style="margin-top:5px">Metrics use all evaluated rows; the API returns only a bounded preview.</div></div>
        <div class="split-details" style="margin-top:10px"><strong>External-test provenance</strong><div id="sensorTestProvenance" style="margin-top:5px">No external-test run.</div></div>
        <div class="run-history" id="sensorTestRunHistory" aria-label="Ultrasonic external-test history"></div>
        <div class="notice" style="margin-top:12px"><strong>Interpretation boundary:</strong> this panel validates the hardware-to-model pipeline. It does not replace field validation with coastal hydrology instruments.</div>
      </div>
    </div>
  </section>

  <section class="panel" id="simulationPanel">
    <details class="retired-workspace" id="legacySimulationArchive" open>
      <summary>Ultrasonic sensor collection</summary>
    <div class="section-heading">
      <div><div class="eyebrow">DEVICE-MEASURED · ESP32 COLLECTION</div><h2>Ultrasonic sensor data</h2>
        <div class="muted compact">ESP32 ultrasonic measurement → server storage. Collection starts on the ESP32.</div></div>
      <div class="badges"><span class="badge safe">ULTRASONIC CONNECTED</span><span class="badge research">DEVICE-MEASURED</span><span class="badge">NO LOCAL TRAINING</span></div>
    </div>
    <div class="notice">This console displays measurements received through the existing hardware pipeline. It does not generate synthetic readings and does not expose local simulation training.</div>

    <div class="summary-grid" aria-label="Collection overview">
      <div class="summary-card"><small>Sessions</small><strong id="summarySessions">--</strong><small id="summarySessionStates">Waiting for server</small></div>
      <div class="summary-card"><small>Total samples</small><strong id="summarySamples">--</strong><small>Stored by the server</small></div>
      <div class="summary-card"><small>Valid ultrasonic</small><strong id="summaryValid">--</strong><small id="summaryValidRate">Data quality --</small></div>
      <div class="summary-card"><small>SAFE labels</small><strong id="summarySafe">--</strong><small>Operator annotations</small></div>
      <div class="summary-card"><small>DANGER labels</small><strong id="summaryDanger">--</strong><small>Operator annotations</small></div>
      <div class="summary-card"><small>Label coverage</small><strong id="summaryCoverage">--</strong><small id="summaryUnknown">UNKNOWN --</small></div>
    </div>

    <div class="simulation-grid">
      <div>
        <label class="field">Collection session<select id="simulationSession" aria-label="Select a collection session"></select></label>
        <div class="tool-row">
          <button id="reloadSimulations" class="secondary" type="button">Refresh collections</button>
          <button id="stopSimulation" class="danger" type="button">Stop selected session</button>
        </div>
        <div id="simulationStatus" class="muted" style="margin-top:10px">Loading collection sessions…</div>
      </div>
      <div>
        <div class="label" style="margin-bottom:8px">Server model status (selection remains controlled by ESP32)</div>
        <div id="modelCatalog" class="model-list"><div class="muted">Loading models…</div></div>
      </div>
    </div>

    <div id="simulationSessionList" class="session-list" aria-label="Sensor collection sessions"></div>
    <div id="sessionDeletionHelp" class="table-note">Only completed, unreferenced sessions can be deleted. Active collection and telemetry transmission are never deleted here.</div>
    <div id="sessionDeletionStatus" class="muted compact" style="margin-top:7px;min-height:1.5em" role="status" aria-live="polite"></div>

    <div class="chart-shell">
      <div class="chart-toolbar">
        <div><strong>Session timeline</strong><div class="muted compact" id="chartCaption">Select a session to view measured samples</div></div>
        <div class="chart-legend">
          <span class="legend-key"><i class="legend-dot" style="background:#4bd6ff"></i>Distance (mm)</span>
          <span class="legend-key"><i class="legend-dot" style="background:#29d391"></i>Water rise (mm)</span>
          <span class="legend-key"><i class="legend-dot" style="background:#ffb84d"></i>Rise rate (mm/s)</span>
          <span class="legend-key"><i class="legend-dot" style="background:#ff5b61"></i>DANGER annotation</span>
        </div>
      </div>
      <div class="chart-scroll"><svg id="simulationChart" viewBox="0 0 1000 382" role="img" aria-label="Ultrasonic distance, water-rise, and rise-rate chart"></svg></div>
      <div class="sticky-actions"><div class="chart-help">Click twice on the chart to set the start and end of an optional annotation range.</div>
        <button id="clearSelection" class="secondary compact" type="button">Clear selection</button></div>
    </div>

    <div class="quality-grid">
      <div class="quality-panel"><h3>Sensor and data quality</h3>
        <div class="quality-row"><span>Valid ultrasonic samples</span><div class="bar"><span id="qualityValidBar" style="width:0"></span></div><strong id="qualityValidText">--</strong></div>
        <div class="quality-row"><span>Invalid / excluded</span><div class="bar"><span id="qualityInvalidBar" style="width:0;background:var(--fault)"></span></div><strong id="qualityInvalidText">--</strong></div>
        <div class="quality-row"><span>Label coverage</span><div class="bar"><span id="qualityCoverageBar" style="width:0;background:var(--safe)"></span></div><strong id="qualityCoverageText">--</strong></div>
        <div id="qualityDetails" class="table-note">Calculated by the server from device health flags, valid distance, and annotation records.</div>
      </div>
      <div class="quality-panel"><h3>Current session labels</h3>
        <div class="coverage" aria-label="SAFE DANGER UNKNOWN label coverage"><span id="coverageSafe" class="safe"></span><span id="coverageDanger" class="danger"></span><span id="coverageUnknown" class="unknown"></span></div>
        <div class="coverage-copy"><span>SAFE <strong id="coverageSafeCount">--</strong></span><span>DANGER <strong id="coverageDangerCount">--</strong></span><span>UNKNOWN <strong id="coverageUnknownCount">--</strong></span></div>
        <div id="coverageNote" class="table-note">Unlabelled samples remain UNKNOWN and are never treated as SAFE.</div>
      </div>
    </div>

    <div class="tool-row" aria-label="Manual interval annotation">
      <label class="field">Start sequence<input id="labelStartSeq" type="number" min="0" step="1" placeholder="Select a chart point"></label>
      <label class="field">End sequence<input id="labelEndSeq" type="number" min="0" step="1" placeholder="Select a second point"></label>
      <label class="field">Manual label<select id="simulationLabel"><option value="safe">SAFE</option><option value="danger">DANGER</option><option value="unknown">Clear to UNKNOWN</option></select></label>
      <label class="field">Label version<input id="labelVersion" type="number" min="1" step="1" value="1"></label>
      <label class="field wide">Annotation evidence<input id="labelNote" maxlength="500" placeholder="Describe the observed event"></label>
      <button id="saveSimulationLabel" type="button">Save annotation</button>
    </div>
    <div id="labelStatus" class="muted" style="margin-top:10px" aria-live="polite">Select a completed collection session.</div>

    <details open style="margin-top:14px"><summary>Sample details (latest 300)</summary>
      <div style="overflow:auto;margin-top:10px"><table><thead><tr><th>Sequence</th><th>Time</th><th>Distance</th><th>Water rise</th><th>Rate</th><th>Quality</th><th>Label</th><th>Selection</th></tr></thead>
        <tbody id="simulationSamples"><tr><td colspan="8" class="muted">No session selected</td></tr></tbody></table></div></details>
    <details style="margin-top:12px"><summary>Annotation audit records</summary>
      <div style="overflow:auto;margin-top:10px"><table><thead><tr><th>Version</th><th>Start</th><th>End</th><th>Label</th><th>Note</th><th>Updated</th></tr></thead>
        <tbody id="simulationLabels"><tr><td colspan="6" class="muted">No manual annotations</td></tr></tbody></table></div></details>

    </details>
  </section>
  <section class="panel"><h2>Recent telemetry</h2><table><thead><tr><th>Received</th><th>Sequence</th><th>Distance</th><th>Water rise</th><th>Rate</th><th>Person</th><th>Alarm</th><th>RSSI</th></tr></thead>
    <tbody id="history"><tr><td colspan="8" class="muted">Waiting for data</td></tr></tbody></table></section>
  <div id="error" class="error"></div>
</main><script>
const DEVICE='COAST_01';
const ADMIN_MODE=__COASTWATCH_ADMIN_MODE__;
const ADMIN_BASE=__COASTWATCH_ADMIN_BASE__;
const alarmNames=['SAFE','ADVISORY','WARNING','CRITICAL','SENSOR FAULT'];
const healthNames=[['Ultrasonic',1,true],['OpenMV',2,true],['Power monitoring',4,false],['Network',8,true]];
let lastEnvironmentFetch=0;
let simulationSessions=[];
let selectedSimulationSession=null;
let simulationOverview=null;
let pendingSessionDeletionId=null;
let pendingSessionDeletionExpiresAt=0;
let pendingSessionDeletionTimer=null;
let deletingSimulationSessionId=null;
let adminCsrfToken='';
let currentTimeline={session:null,points:[],labels:[]};
let simulationRequestSerial=0;
let officialDatasets=[];
let selectedOfficialDataset=null;
let officialReadiness=null;
let officialTrainingRuns=[];
let selectedOfficialRun=null;
let activeOfficialModel=null;
let frozenSensorProfile=null;
let sensorTestRuns=[];
let latestTelemetry=null;
const $=id=>document.getElementById(id);
function alarmName(level){ return alarmNames[level] ?? `UNKNOWN (${level})`; }
function setLatest(d){
  latestTelemetry=d;
  const age=(Date.now()-new Date(d.received_at).getTime())/1000;
  $('online').textContent=age<=10?'Telemetry online':'Telemetry timeout';
  $('online').className='status '+(age<=10?'online':'offline');
  $('alarm').textContent=alarmName(d.alarm_level); $('alarm').dataset.level=d.alarm_level;
  $('distance').textContent=d.distance_mm; $('rise').textContent=d.water_rise_mm;
  $('rate').textContent=d.rise_rate_mm_s; $('person').textContent=d.person_detected?'DETECTED':'CLEAR';
  $('rssi').textContent=d.wifi_rssi; $('sequence').textContent=`#${d.seq} / ${(d.uptime_ms/1000).toFixed(1)} s`;
  $('health').innerHTML=healthNames.map(([name,bit,monitored])=>monitored
    ? `<span class="chip ${(d.health_flags&bit)?'ok':''}">${name} ${(d.health_flags&bit)?'OK':'FAULT'}</span>`
    : `<span class="chip">${name} NOT CONFIGURED</span>`).join('');
  $('updated').textContent='Server received: '+new Date(d.received_at).toLocaleString('en-GB');
  updateSensorMappingPreview();
}
function setHistory(rows){ $('history').innerHTML=rows.length?rows.map(d=>`<tr><td>${new Date(d.received_at).toLocaleTimeString('en-GB')}</td><td>${d.seq}</td><td>${d.distance_mm} mm</td><td>${d.water_rise_mm} mm</td><td>${d.rise_rate_mm_s} mm/s</td><td>${d.person_detected?'YES':'NO'}</td><td>${alarmName(d.alarm_level)}</td><td>${d.wifi_rssi}</td></tr>`).join(''):'<tr><td colspan="8" class="muted">No telemetry data</td></tr>'; }
function metric(value,unit,digits=1){ return Number.isFinite(value)?`${Number(value).toFixed(digits)}${unit}`:'--'; }
function setEnvironment(e){
  const source=e.stale?'STALE DATA':String(e.source||'').toUpperCase();
  const location=String(e.location||e.display_location||'LOCATION NOT REPORTED');
  const parts=[location,`Air ${metric(e.air_temperature_c,' °C')}`,`Humidity ${metric(e.humidity_percent,'%',0)}`,`Wind ${metric(e.wind_speed_kmh,' km/h')}`,`Wave ${metric(e.wave_height_m,' m')}`,`Period ${metric(e.wave_period_s,' s')}`,`Sea temperature ${metric(e.water_temperature_c,' °C')}`,`Sea level ${metric(e.sea_level_height_m,' m',3)}`,`Current ${metric(e.ocean_current_velocity_kmh,' km/h')}`,source].filter(Boolean);
  $('environment').textContent=parts.join(' · ');
}
function handleAuthenticationResponse(response){
  if(ADMIN_MODE&&response.status===401) window.location.replace(`${ADMIN_BASE}/login`);
  return response;
}
async function fetchJson(url){ const r=handleAuthenticationResponse(await fetch(url,{cache:'no-store'})); if(!r.ok) throw new Error(`${url} returned ${r.status}`); return r.json(); }
async function fetchOptionalJson(url){
  const response=handleAuthenticationResponse(await fetch(url,{cache:'no-store'}));
  if(response.status===404) return null;
  if(!response.ok) throw new Error(`${url} returned ${response.status}`);
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
    try { const body=await response.json(); const value=typeof body.detail==='string'?body.detail:''; detail=value&&!/[\u3400-\u9fff]/u.test(value)?`: ${value}`:''; } catch(_error) {}
    throw new Error(`${url} returned ${response.status}${detail}`);
  }
  return response.status===204?null:response.json();
}
async function loadAdminSession(){
  if(!ADMIN_MODE) return;
  const response=handleAuthenticationResponse(await fetch(`${ADMIN_BASE}/api/auth/session`,{cache:'no-store'}));
  if(!response.ok) throw new Error('Administrator session expired');
  const session=await response.json(); adminCsrfToken=session.csrf_token;
  $('adminIdentity').textContent=session.username; $('adminControls').style.display='flex';
}
async function logoutAdmin(){
  if(!ADMIN_MODE) return;
  const response=await fetch(`${ADMIN_BASE}/api/auth/logout`,{method:'POST',headers:{'X-CSRF-Token':adminCsrfToken}});
  if(response.ok||response.status===401){ window.location.replace(`${ADMIN_BASE}/login`); return; }
  $('error').textContent=`Sign-out failed: ${response.status}`;
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
function isGreatYarmouthSite(site){
  const text=JSON.stringify(site||'').toLowerCase();
  if(text.includes('great yarmouth')) return true;
  const latitude=asNumber(site?.latitude??site?.lat); const longitude=asNumber(site?.longitude??site?.lon);
  return latitude!==null&&longitude!==null&&Math.abs(latitude-52.60831)<0.2&&Math.abs(longitude-1.73052)<0.3;
}
function officialDatasetIdentity(dataset){
  return String(dataset?.dataset_id||dataset?.id||dataset?.version_id||'');
}
function selectedOfficialSiteIds(){
  return Array.from($('officialSites').selectedOptions).map(option=>option.value).filter(Boolean);
}
function renderOfficialEvidenceScope(){
  const selected=Array.from($('officialSites').selectedOptions);
  const count=selected.length;
  const includesGreatYarmouth=selected.some(option=>option.dataset.greatYarmouth==='1');
  if(!count) $('officialEvidenceScope').textContent='No site selected.';
  else if(count===1) $('officialEvidenceScope').textContent=`${selected[0].textContent} · SINGLE-COAST exploratory scope; cross-coast activation is unavailable.`;
  else if(count<3) $('officialEvidenceScope').textContent=`${count} UK sites${includesGreatYarmouth?' including Great Yarmouth':''} · multi-coast exploration; activation still requires at least 3 sites.`;
  else $('officialEvidenceScope').textContent=`${count} UK sites${includesGreatYarmouth?' including Great Yarmouth':''} · MULTI-COAST scope; server readiness remains authoritative.`;
}
function splitBoundary(dataset,splitName,boundary){
  const split=dataset?.splits?.[splitName]||dataset?.split?.[splitName]||dataset?.[`${splitName}_split`]||{};
  return split?.[boundary]||split?.[`${boundary}_at`]||dataset?.[`${splitName}_${boundary}`]||'';
}
function renderOfficialDataset(dataset){
  selectedOfficialDataset=dataset;
  const sites=objectArray(dataset?.sites||dataset?.site_ids,'items','stations').map(site=>({...siteIdentity(site),greatYarmouth:isGreatYarmouthSite(site)})).filter(site=>site.id)
    .sort((left,right)=>Number(right.greatYarmouth)-Number(left.greatYarmouth)||left.label.localeCompare(right.label,'en-GB'));
  const sensorSites=sites.filter(site=>site.greatYarmouth);
  const previousSites=new Set(selectedOfficialSiteIds());
  $('officialSites').replaceChildren(...sites.map(site=>{
    const option=document.createElement('option'); option.value=site.id; option.textContent=site.label;
    option.dataset.greatYarmouth=site.greatYarmouth?'1':'0'; option.selected=previousSites.size?previousSites.has(site.id):true; return option;
  }));
  $('sensorStation').replaceChildren(...sensorSites.map(site=>{
    const option=document.createElement('option'); option.value=site.id; option.textContent=site.label; return option;
  }));
  if(!sites.length){
    const option=document.createElement('option'); option.value=''; option.textContent='No UK sites are present in this manifest';
    $('officialSites').replaceChildren(option);
  }
  if(!sensorSites.length){ const option=document.createElement('option'); option.value=''; option.textContent='Great Yarmouth is not present in this manifest'; $('sensorStation').replaceChildren(option); }
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
  $('officialCoverage').textContent=`${sites.length} registered UK sites · ${valueText(start)} → ${valueText(end)} · ${formatCount(rows)} rows`;
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
  $('officialDatasetStatus').textContent=`${dataset?.display_name||dataset?.name||officialDatasetIdentity(dataset)} · manifest time splits are read-only.`;
  if(activeOfficialModel) populateFrozenSensorContexts();
  updateSensorProfileControls();
}
async function loadOfficialDatasets({preserveSelection=true}={}){
  const previous=preserveSelection?$('officialDataset').value:'';
  $('officialDatasetStatus').textContent='Loading registered UK official datasets…';
  try {
    const payload=await fetchJson('/api/v1/official-datasets');
    officialDatasets=objectArray(payload,'datasets','items','results');
    const options=officialDatasets.map(dataset=>{
      const option=document.createElement('option'); option.value=officialDatasetIdentity(dataset);
      option.textContent=dataset.display_name||dataset.name||option.value; return option;
    });
    $('officialDataset').replaceChildren(...options);
    if(!officialDatasets.length){
      const option=document.createElement('option'); option.value=''; option.textContent='No registered official dataset found'; $('officialDataset').appendChild(option);
      selectedOfficialDataset=null; renderOfficialReadiness(null);
      $('officialDatasetStatus').textContent='No official manifest in the protected data directory has passed integrity checks. No local simulated data will be substituted.';
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
  const button=$('rescanOfficialDatasets'); button.disabled=true; button.textContent='Scanning…';
  const previousSelection=$('officialDataset').value;
  statusBox('officialRescanStatus',false,'Scanning','The server is validating each manifest, raw file, and immutable dataset ID in the protected directory.');
  try {
    const scan=await sendJson('/api/v1/official-datasets/rescan','POST',undefined);
    const errors=objectArray(scan,'errors');
    const errorCount=asNumber(scan?.error_count)??errors.length;
    const registeredCount=asNumber(scan?.registered_count);
    await loadOfficialDatasets({preserveSelection:true});
    const retained=Boolean(previousSelection&&$('officialDataset').value===previousSelection);
    const selectionNote=previousSelection?(retained?`The previous selection ${previousSelection} was retained.`:`The previous selection ${previousSelection} is no longer registered.`):'No dataset was selected before the scan.';
    const bundleErrors=errors.map(item=>`${valueText(item?.bundle,'UNKNOWN BUNDLE')}: ${valueText(item?.detail||item?.message,'No details provided')}`).join(' | ');
    const fullyAccepted=errorCount===0;
    statusBox('officialRescanStatus',fullyAccepted,fullyAccepted?'Scan complete · all discovered bundles accepted':`Scan complete with ${formatCount(errorCount)} rejected bundles`,`${formatCount(registeredCount)} bundles were registered in this scan. ${selectionNote}${bundleErrors?` Bundle errors: ${bundleErrors}`:''}`);
  }
  catch(error){ statusBox('officialRescanStatus',false,'Scan request failed',String(error)); $('officialDatasetStatus').textContent=String(error); }
  finally { button.disabled=false; button.textContent='Rescan protected data directory'; }
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
  const verifiedBoundary=`${assurance} · Server verified raw bytes/SHA-256, manifest structure, binary-label shape, temporal splits, and leakage gap. Official ownership, licensing, harmonisation, and label derivation are operator-attested. deterministic_importer_replay_verified=${replay?'true':'false'}.`;
  const readinessTitle=ready?(activationReady?'Official training and activation evidence passed':'Training is available, but the result cannot be activated'):'Training blocked';
  const readinessDetail=ready?`${verifiedBoundary}${activationReady?'':' Activation remains blocked by server policy: at least three sites, 200 rows per split, and both frozen-test classes for every selected site.'}`:(blockers[0]||'Select a valid official dataset and at least one UK site.');
  statusBox('officialReadiness',ready,readinessTitle,readinessDetail);
  const provenanceWarning=`Provenance：${verifiedBoundary}`;
  const listItems=[...blockers.map(text=>`Blocker: ${text}`),...warnings.map(text=>`Notice: ${text}`),provenanceWarning].map(text=>{ const item=document.createElement('li'); item.textContent=text; return item; });
  $('officialBlockers').replaceChildren(...listItems);
  $('trainOfficialModel').disabled=!ready;
  const contract=readiness?.data_contract||{};
  $('officialLeakageInvariant').textContent=`SENSOR ROWS USED FOR FIT = ${contract.sensor_rows_used_for_fit??readiness?.sensor_rows_used_for_fit??0} · SCALER = ${contract.sensor_rows_used_for_scaler??readiness?.sensor_rows_used_for_scaler??0} · THRESHOLD = ${contract.sensor_rows_used_for_threshold??readiness?.sensor_rows_used_for_threshold??0}`;
}
async function loadOfficialTrainingReadiness(){
  if(!$('officialDataset').value||!selectedOfficialSiteIds().length){ renderOfficialReadiness(null); return; }
  statusBox('officialReadiness',false,'Checking','The server is validating the official manifest and leakage safeguards.');
  try { renderOfficialReadiness(await fetchJson(officialReadinessUrl())); }
  catch(error){ renderOfficialReadiness({ready:false,blockers:[String(error)]}); }
}
function officialTrainingPayload(){
  const payload={dataset_id:$('officialDataset').value}; const sites=selectedOfficialSiteIds();
  if(sites.length) payload.selected_site_ids=sites; return payload;
}
async function trainOfficialModel(){
  if(!officialReadiness?.ready) return;
  const button=$('trainOfficialModel'); button.disabled=true; button.textContent='Training on server…';
  statusBox('officialRunStatus',false,'Training run submitted','Fitting the official training split, selecting the threshold on validation data, then calculating frozen-test metrics once.');
  try {
    const run=await sendJson('/api/v1/official-training/runs','POST',officialTrainingPayload());
    renderOfficialRun(run?.run||run); await loadOfficialTrainingRuns();
  } catch(error){ statusBox('officialRunStatus',false,'Training failed',String(error)); }
  finally { button.textContent='Train official model'; button.disabled=!officialReadiness?.ready; }
}
function officialRunId(run){ return String(run?.run_id||run?.id||''); }
function officialRunMetrics(run){
  return run?.frozen_test_metrics||run?.metrics?.frozen_test||run?.metrics?.test||run?.metrics||{};
}
function renderOfficialBaselineVerdict(run){
  const comparison=run?.metrics?.delta_vs_water_level_threshold||run?.delta_vs_water_level_threshold;
  const box=$('officialBaselineVerdict');
  if(!comparison){
    box.textContent='The server has not returned delta_vs_water_level_threshold. This interface does not invent a claim that machine learning is better. A hard threshold has no probability output, so Brier, ROC AUC, and PR AUC are not compared.'; return;
  }
  if(comparison.available===false||!comparison.verdict){
    box.textContent=`Server conclusion: N/A — no fair site-macro model-versus-threshold result covers every selected site. ${comparison.professor_summary||''} Row-level or eligible-subset metrics are not substituted for the primary result. A hard threshold has no probability output, so Brier, ROC AUC, and PR AUC are not compared.`; return;
  }
  const rawVerdict=String(comparison.verdict||run?.metrics?.baseline_verdict||'').toLowerCase();
  const improves=['ml_improves_baseline','improves_baseline','ml_improves','outperforms_threshold_on_comparable_frozen_test_metrics'].includes(rawVerdict);
  const verdict=improves?'ML improves baseline':'No demonstrated improvement; prefer simple rule';
  const labels={balanced_accuracy:'balanced accuracy',precision:'precision',recall:'recall',f1:'F1',specificity:'specificity',false_positive_rows_per_day:'false-positive rows/day'};
  const comparable=comparison.comparable_metric_deltas_model_minus_threshold||comparison;
  const deltas=Object.entries(labels).filter(([key])=>asNumber(comparable[key])!==null).map(([key,label])=>`${label} ${Number(comparable[key])>=0?'+':''}${Number(comparable[key]).toFixed(3)}`);
  box.textContent=`Server conclusion: ${verdict}. ${comparison.professor_summary||''}${deltas.length?` Classification-only differences on the same frozen test set: ${deltas.join(' · ')}.`:''} Brier, ROC AUC, and PR AUC are not compared with the hard threshold.`;
}
function renderOfficialRun(run){
  selectedOfficialRun=run||null;
  if(!run){
    renderOfficialBaselineVerdict(null);
    statusBox('officialRunStatus',false,'Not trained','Only a successful run that passes the official frozen test can be activated in Shadow mode.');
    $('activateOfficialRun').disabled=true;
    ['officialMetricPRAuc','officialMetricRecall','officialMetricPrecision','officialMetricF1','officialMetricRocAuc','officialMetricBrier','officialMetricFalsePositiveRows','officialMetricThreshold','officialMetricSiteCoverage'].forEach(id=>$(id).textContent='—');
    $('officialModelSummary').textContent='Waiting for frozen-test metrics.';
    $('officialRunProvenance').textContent='No training run available.';
    $('officialArtifactProvenance').textContent='No artifact available.';
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
    ?(activatable?(run.message||run.detail||'A hashed official-model artifact with complete site-macro coverage was created.'):`Training completed, but the evidence level is insufficient for Shadow activation. ${activationBlockers.join(' · ')}`)
    :(run.message||run.detail||'The run is incomplete or blocked.');
  statusBox('officialRunStatus',succeeded&&activatable,`${officialRunId(run)||'Run'} · ${status.toUpperCase()}`,runDetail);
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
  $('officialThresholdBaseline').textContent=Object.keys(threshold).length&&threshold.available!==false?`per-site hard classifier · threshold_selection_split=${thresholdSelectionSplit} · thresholds ${thresholdList||'NOT REPORTED'} · coverage ${thresholdCoverage} · site-macro recall ${thresholdMacro?metricText(thresholdMacro.recall):'N/A'} · F1 ${thresholdMacro?metricText(thresholdMacro.f1):'N/A'} · row-level companion false-positive rows/day ${metricText(thresholdMetrics.false_positive_rows_per_day)}`:`Baseline unavailable · ${threshold.reason||'The server returned no comparable threshold result.'}`;
  $('officialPersistenceBaseline').textContent=Object.keys(persistence).length&&persistence.available!==false?`hard classifier · recall ${metricText(persistenceMetrics.danger_recall??persistenceMetrics.recall)} · F1 ${metricText(persistenceMetrics.danger_f1??persistenceMetrics.f1)} · false-positive rows/day ${metricText(persistenceMetrics.false_positive_rows_per_day)}`:`Baseline unavailable · ${persistence.reason||'The server returned no observable persistence baseline.'}`;
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
  catch(error){ statusBox('officialRunStatus',false,'Unable to load run',String(error)); }
}
function renderOfficialRunHistory(){
  const nodes=officialTrainingRuns.map(run=>{
    const button=document.createElement('button'); button.type='button'; button.className='run-button'; button.dataset.runId=officialRunId(run);
    const status=String(run.status||run.state||'unknown').toUpperCase();
    button.textContent=`${officialRunId(run)||'Unnamed run'} · ${status} · ${run.dataset_id||'—'} · ${run.created_at?new Date(run.created_at).toLocaleString():'—'}`;
    button.addEventListener('click',()=>selectOfficialRun(officialRunId(run))); return button;
  });
  if(!nodes.length){ const empty=document.createElement('div'); empty.className='muted'; empty.textContent='No official training runs.'; nodes.push(empty); }
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
  const button=$('activateOfficialRun'); button.disabled=true; button.textContent='Activating…';
  try {
    const run=await sendJson(`/api/v1/official-training/runs/${encodeURIComponent(runId)}/activate`,'POST',undefined);
    renderOfficialRun(run?.run||run); await Promise.all([loadOfficialModel(),loadOfficialTrainingRuns(),loadModels()]);
  } catch(error){ statusBox('officialRunStatus',false,'Activation failed',String(error)); }
  finally { button.textContent='Activate in Shadow mode'; }
}
async function loadOfficialModel(){
  try {
    const response=await fetchOptionalJson('/api/v1/official-model');
    activeOfficialModel=response?.artifact?{...response.artifact,active_run:response.active_run}:response;
    $('sensorOfficialModel').value=activeOfficialModel?`${activeOfficialModel.model_id||'uk-official-coast-logreg-v2'} · ${shortHash(activeOfficialModel.artifact_sha256||activeOfficialModel.artifact_hash||activeOfficialModel.hash)} · ${activeOfficialModel.deployment_mode||activeOfficialModel.mode||'SHADOW'}`:'No active official model';
    populateFrozenSensorContexts(); updateSensorProfileControls();
  } catch(error){ activeOfficialModel=null; $('sensorOfficialModel').value=String(error); updateSensorProfileControls(); }
}
function frozenSensorContexts(){
  const contexts=objectArray(activeOfficialModel?.sensor_test_contexts||activeOfficialModel?.source_manifest?.frozen_sensor_contexts||activeOfficialModel?.frozen_sensor_contexts||activeOfficialModel?.sensor_contexts,'items','contexts');
  const greatYarmouthIds=new Set(objectArray(selectedOfficialDataset?.sites||selectedOfficialDataset?.site_ids,'items','stations').filter(isGreatYarmouthSite).map(site=>siteIdentity(site).id));
  return contexts.filter(context=>isGreatYarmouthSite(context)||greatYarmouthIds.has(String(context.station_id||context.site_id||'')));
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
    option.textContent=`${context.station_id||context.site_id||'Great Yarmouth'} · ${context.timestamp||context.observed_at||option.value} · ${context.source_split||'FROZEN TEST'}`; return option;
  });
  if(!options.length){ const option=document.createElement('option'); option.value=''; option.textContent=activeOfficialModel?'The active artifact has no Great Yarmouth frozen context':'Activate an official model first'; options.push(option); }
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
    return [first,...sessions.map(session=>{ const option=document.createElement('option'); option.value=session.session_id; option.textContent=`${session.name||'ESP32 ULTRASONIC COLLECTION'} · ${session.session_id.slice(-8)} · ${formatCount(session.sample_count)} samples`; return option; })];
  };
  const calibrationPrevious=$('sensorCalibrationSession').value; const testPrevious=$('sensorTestSession').value;
  $('sensorCalibrationSession').replaceChildren(...makeOptions('Select a completed independent calibration session'));
  $('sensorTestSession').replaceChildren(...makeOptions('Select a completed external-test session'));
  if(sessions.some(session=>session.session_id===calibrationPrevious)) $('sensorCalibrationSession').value=calibrationPrevious;
  if(sessions.some(session=>session.session_id===testPrevious)) $('sensorTestSession').value=testPrevious;
  updateSensorProfileControls(); updateSensorTestControls();
}
function updateSensorProfileControls(){
  const mode=$('sensorProfileMode').value; const formal=mode==='formal'; const context=selectedFrozenSensorContext(); const frozen=Boolean(frozenSensorProfile);
  $('sensorProfileMode').disabled=frozen; $('sensorContextId').disabled=frozen; $('sensorStation').disabled=frozen;
  $('sensorGain').readOnly=formal||frozen; $('sensorReferenceLevel').readOnly=formal||frozen;
  $('sensorGain').placeholder=formal?'Derived from official TRAIN Q05/Q95 and calibration session':'Required: manual exploratory gain';
  $('sensorReferenceLevel').placeholder=formal?'Derived from the official TRAIN reference':'Required: manual exploratory reference';
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
    $('sensorProfileStatus').textContent='No frozen mapping profile. A formal external test requires the profile, model hash, and official context to be registered before collection.';
    $('sensorProfileProvenance').textContent='No frozen profile. FORMAL mode records official TRAIN Q05/Q95, independent calibration-session Q05/Q95, gain_m_per_m, and reference_level_m.';
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
  const button=$('freezeSensorProfile'); button.disabled=true; button.textContent='Freezing…';
  try { renderSensorProfile(await sendJson('/api/v1/sensor-test/device-profile','PUT',payload)); }
  catch(error){ $('sensorProfileStatus').textContent=String(error); }
  finally { button.textContent='Freeze mapping profile'; updateSensorProfileControls(); }
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
    statusBox('sensorTestStatus',false,'Not run','The test must use a profile frozen before collection. Results remain separate from official frozen-test metrics.');
    ['sensorMetricInputSamples','sensorMetricValidSamples','sensorMetricSamples','sensorMetricInvalidSamples','sensorMetricOod','sensorMetricMin','sensorMetricMax','sensorMetricMeanRisk','sensorMetricLatency','sensorMetricResultRows','sensorMetricPreviewRows'].forEach(id=>$(id).textContent='—');
    $('sensorTestEvaluationPolicy').textContent='Full metrics aggregate all evaluated rows. The API stores and returns only a limited, evenly sampled preview to keep the browser responsive.';
    $('sensorTestProvenance').textContent='No external-test run.';
    return;
  }
  const status=String(run.status||run.state||'completed').toLowerCase(); const complete=['completed','succeeded','ready'].includes(status);
  statusBox('sensorTestStatus',complete,`${sensorRunId(run)||'External test'} · ${status.toUpperCase()}`,run.message||run.detail||(complete?'Linear mapping and frozen-model inference completed.':'The run is incomplete or blocked.'));
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
  $('sensorTestEvaluationPolicy').textContent=`input ${formatCount(inputSampleCount)} · valid ${formatCount(validInputSampleCount)} · invalid excluded ${formatCount(invalidInputSampleCount)} · evaluated ${formatCount(sampleCount)} / limit ${formatCount(result.evaluation_sample_limit)} using ${valueText(result.sampling_policy)} · truncated valid ${formatCount(truncatedValidSampleCount)} (${evaluationTruncated?'YES':'NO'}) · result_row_count ${formatCount(resultRowCount)} · rows preview ${formatCount(previewRowCount)} / limit ${formatCount(result.preview_row_limit)} using ${valueText(result.preview_sampling_policy)}. OOD, min/max, and mean risk are aggregated from all evaluated rows, not inferred from the preview.`;
  const unchanged=(result.model_artifact_unchanged??metrics.model_artifact_unchanged)===true?'· ARTIFACT HASH UNCHANGED':'';
  $('sensorTestProvenance').textContent=`session ${run.session_id||metrics.session_id||'—'} · evaluated samples sha ${shortHash(result.evaluated_samples_sha256)} · profile ${shortHash(run.profile_sha256||metrics.profile_sha256||run.profile_hash)} · model ${shortHash(run.artifact_sha256||run.artifact_sha256_before||result.official_model_artifact_sha256_before||run.artifact_hash)} · context ${shortHash(run.context_id||metrics.context_id)} · SENSOR_PROXY_EXTERNAL_TEST ${unchanged} · ${(run.formal_metrics_eligible??metrics.formal_metrics_eligible)===false?'EXCLUDED FROM FORMAL METRICS':'FORMAL PROFILE'}`;
  Array.from($('sensorTestRunHistory').querySelectorAll('button')).forEach(button=>button.setAttribute('aria-current',String(button.dataset.runId===sensorRunId(run))));
}
async function selectSensorTestRun(runId){
  try { renderSensorTestRun(await fetchJson(`/api/v1/sensor-test/runs/${encodeURIComponent(runId)}`)); }
  catch(error){ statusBox('sensorTestStatus',false,'Unable to load external test',String(error)); }
}
function renderSensorTestRunHistory(){
  const nodes=sensorTestRuns.map(run=>{
    const button=document.createElement('button'); button.type='button'; button.className='run-button'; button.dataset.runId=sensorRunId(run);
    button.textContent=`${sensorRunId(run)||'Unnamed test'} · ${String(run.status||run.state||'completed').toUpperCase()} · session ${String(run.session_id||'—').slice(-8)} · ${run.created_at?new Date(run.created_at).toLocaleString():'—'}`;
    button.addEventListener('click',()=>selectSensorTestRun(sensorRunId(run))); return button;
  });
  if(!nodes.length){ const empty=document.createElement('div'); empty.className='muted'; empty.textContent='No external-test runs.'; nodes.push(empty); }
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
  const button=$('runSensorExternalTest'); button.disabled=true; button.textContent='Running external test…';
  statusBox('sensorTestStatus',false,'Running','Applying the frozen profile for linear mapping. Model parameters, scaler, and threshold remain unchanged.');
  try {
    const run=await sendJson('/api/v1/sensor-test/runs','POST',{device_id:DEVICE,session_id:sessionId});
    renderSensorTestRun(run?.run||run); await loadSensorTestRuns();
  } catch(error){ statusBox('sensorTestStatus',false,'External test failed',String(error)); }
  finally { button.textContent='Run external test'; updateSensorTestControls(); }
}
function statusLabel(status){ return ({ready:'READY',unavailable:'UNAVAILABLE',not_trained:'NOT TRAINED'})[status]||String(status||'UNKNOWN').toUpperCase(); }
function renderModelCatalog(catalog){
  const cards=(catalog.models||[]).filter(model=>model.model_id!=='custom-water-logreg-v1').map(model=>{
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
    return card;
  });
  $('modelCatalog').replaceChildren(...cards);
}
async function loadModels(){
  try {
    const catalog=await fetchJson(`/api/v1/models?device_id=${DEVICE}`);
    renderModelCatalog(catalog);
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
    svg.appendChild(svgNode('text',{x:500,y:190,'text-anchor':'middle',fill:'#87a8b3','font-size':16},'Waiting for session samples'));
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
    {key:'distance_mm',name:'Distance',unit:'mm',color:'#4bd6ff'},
    {key:'water_rise_mm',name:'Water rise',unit:'mm',color:'#29d391'},
    {key:'rise_rate_mm_s',name:'Rise rate',unit:'mm/s',color:'#ffb84d'}
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
  if(!samples.length){ $('simulationSamples').innerHTML='<tr><td colspan="8" class="muted">This session has no samples</td></tr>'; return; }
  const selection=currentSelection(); const visible=samples.length>300?samples.slice(-300):samples;
  const rows=visible.map(sample=>{
    const row=document.createElement('tr');
    if(selection.complete&&sample.seq>=selection.start&&sample.seq<=selection.end) row.className='selected-sample';
    addTextCell(row,sample.seq); addTextCell(row,new Date(sample.received_at).toLocaleTimeString());
    addTextCell(row,`${sample.distance_mm} mm`); addTextCell(row,`${sample.water_rise_mm} mm`);
    addTextCell(row,`${sample.rise_rate_mm_s} mm/s`);
    addTextCell(row,sample.valid_ultrasonic?'VALID':'EXCLUDED',sample.valid_ultrasonic?'online':'offline');
    const labelCell=document.createElement('td'); labelCell.appendChild(labelPill(sample.label)); row.appendChild(labelCell);
    const controls=document.createElement('td');
    const begin=document.createElement('button'); begin.type='button'; begin.className='secondary compact'; begin.textContent='Set start';
    begin.addEventListener('click',()=>{ $('labelStartSeq').value=sample.seq; $('labelEndSeq').value=''; updateSelectionVisuals(); });
    const finish=document.createElement('button'); finish.type='button'; finish.className='secondary compact'; finish.textContent='Set end';
    finish.style.marginLeft='6px'; finish.addEventListener('click',()=>{
      const start=asNumber($('labelStartSeq').value); $('labelStartSeq').value=start===null?sample.seq:Math.min(start,sample.seq); $('labelEndSeq').value=start===null?sample.seq:Math.max(start,sample.seq); updateSelectionVisuals();
    });
    controls.append(begin,finish); row.appendChild(controls); return row;
  });
  $('simulationSamples').replaceChildren(...rows);
}
function renderSimulationLabels(labels){
  if(!labels.length){ $('simulationLabels').innerHTML='<tr><td colspan="6" class="muted">No manual annotations. Uncovered samples remain UNKNOWN.</td></tr>'; return; }
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
  $('summarySessionStates').textContent=`${formatCount(totals.active_session_count)} collecting · ${formatCount(totals.completed_session_count)} completed`;
  $('summarySamples').textContent=formatCount(totals.sample_count); $('summaryValid').textContent=formatCount(totals.valid_ultrasonic_samples);
  const validRate=count?Number(totals.valid_ultrasonic_samples||0)/count:0; $('summaryValidRate').textContent=`Valid data ${formatPercent(validRate)}`;
  const labels=totals.label_counts||{}; $('summarySafe').textContent=formatCount(labels.safe); $('summaryDanger').textContent=formatCount(labels.danger);
  $('summaryCoverage').textContent=formatPercent(totals.label_coverage); $('summaryUnknown').textContent=`UNKNOWN ${formatCount(labels.unknown)}`;
}
function clearPendingSessionDeletion(){
  if(pendingSessionDeletionTimer!==null) window.clearTimeout(pendingSessionDeletionTimer);
  pendingSessionDeletionTimer=null; pendingSessionDeletionId=null; pendingSessionDeletionExpiresAt=0;
}
function sessionDeletionDescriptor(session){
  return `${session.name} (session ID ${session.session_id}, ${formatCount(session.sample_count)} samples)`;
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
    $('sessionDeletionStatus').textContent=`Deletion cancelled for ${sessionDeletionDescriptor(session)} because the five-second confirmation window expired.`;
    renderSessionList();
  },5000);
  $('sessionDeletionStatus').textContent=`Safety confirmation: click the red button again within five seconds to permanently delete ${sessionDeletionDescriptor(session)} and its samples, labels, and stored metadata.`;
  renderSessionList(); focusSessionDeleteButton(session.session_id);
}
function describeSessionDeletionFailure(error){
  const message=String(error);
  if(message.includes('is referenced by training artifact')) return `Deletion rejected: this session is referenced by a model artifact and must remain for traceability. ${message}`;
  if(message.includes('cannot be verified; session deletion is blocked')) return `Deletion rejected: the server cannot verify existing artifacts, so source data remains protected. ${message}`;
  if(message.includes('active simulation session')) return `Deletion rejected: this session is still collecting. Stop it first. ${message}`;
  if(message.includes('not found')) return `Deletion failed: the server could not find this session; another administrator may already have removed it. ${message}`;
  return `Deletion failed: ${message}`;
}
async function deleteCompletedSimulationSession(session){
  if(session.state!=='completed'){
    $('sessionDeletionStatus').textContent='An active collection session cannot be deleted. Stop it first.'; return;
  }
  if(pendingSessionDeletionId!==session.session_id||Date.now()>pendingSessionDeletionExpiresAt){
    armSessionDeletion(session); return;
  }
  clearPendingSessionDeletion(); deletingSimulationSessionId=session.session_id;
  $('sessionDeletionStatus').textContent=`Permanently deleting ${sessionDeletionDescriptor(session)}…`;
  renderSessionList();
  let result;
  try {
    result=await sendJson(`/api/v1/simulations/sessions/${encodeURIComponent(session.session_id)}?device_id=${encodeURIComponent(DEVICE)}`,'DELETE',undefined);
  } catch(error){
    deletingSimulationSessionId=null; $('sessionDeletionStatus').textContent=describeSessionDeletionFailure(error);
    renderSessionList(); return;
  }
  simulationSessions=simulationSessions.filter(item=>item.session_id!==session.session_id);
  if(selectedSimulationSession?.session_id===session.session_id){
    simulationRequestSerial+=1; selectedSimulationSession=null; emptyTimeline();
  }
  deletingSimulationSessionId=null; renderSessionList();
  const counts=result?.deleted_counts||{};
  const successMessage=`Deleted ${result?.session_id||session.session_id}: ${formatCount(counts.samples)} samples, ${formatCount(counts.labels)} labels, and ${formatCount(counts.scenario_snapshots)} stored metadata records; detached ${formatCount(result?.detached_telemetry_count)} telemetry links.`;
  $('sessionDeletionStatus').textContent=`${successMessage} Refreshing the overview and timeline…`;
  try {
    await loadSimulationSessions({throwOnError:true});
    $('sessionDeletionStatus').textContent=`${successMessage} The overview and timeline are refreshed.`;
  } catch(refreshError){
    $('sessionDeletionStatus').textContent=`${successMessage} Deletion succeeded, but refresh failed and will retry automatically: ${String(refreshError)}`;
  }
}
function renderSessionList(){
  if(!simulationSessions.length){
    $('simulationSessionList').innerHTML='<div class="muted">Waiting for the ESP32 to start collection from its COLLECTION screen.</div>';
    return;
  }
  const selectedId=$('simulationSession').value;
  const cards=simulationSessions.map(session=>{
    const card=document.createElement('article');
    card.className='session-card'+(session.session_id===selectedId?' selected':'');
    const button=document.createElement('button'); button.type='button'; button.className='session-button';
    const top=document.createElement('div'); top.className='session-top'; const name=document.createElement('span'); name.textContent=session.name;
    const state=document.createElement('span'); state.className=`badge ${session.state==='active'?'active':''}`; state.textContent=session.state==='active'?'COLLECTING':'COMPLETED'; top.append(name,state);
    const counts=session.label_counts||{}; const meta=document.createElement('div'); meta.className='session-meta';
    meta.textContent=`${formatCount(session.sample_count)} samples · ${formatCount(session.valid_ultrasonic_samples)} valid · SAFE ${formatCount(counts.safe)} / DANGER ${formatCount(counts.danger)} · ${new Date(session.started_at).toLocaleString('en-GB')}`;
    button.append(top,meta); button.addEventListener('click',async()=>{ $('simulationSession').value=session.session_id; await loadSimulationDetails(); renderSessionList(); });
    card.appendChild(button);
    const actions=document.createElement('div'); actions.className='session-actions';
    const deleteButton=document.createElement('button'); deleteButton.type='button';
    deleteButton.className='danger compact session-delete-button'; deleteButton.dataset.sessionId=session.session_id;
    deleteButton.setAttribute('aria-describedby','sessionDeletionHelp sessionDeletionStatus');
    if(session.state!=='completed'){
      deleteButton.disabled=true; deleteButton.textContent='Collecting — cannot delete';
      deleteButton.setAttribute('aria-label',`${session.name}, session ID ${session.session_id}, ${formatCount(session.sample_count)} samples, actively collecting and cannot be deleted`);
    } else {
      const pending=pendingSessionDeletionId===session.session_id&&Date.now()<=pendingSessionDeletionExpiresAt;
      const deleting=deletingSimulationSessionId===session.session_id;
      const shortSessionId=String(session.session_id).slice(-8);
      deleteButton.disabled=deleting; deleteButton.classList.toggle('confirm-delete',pending);
      deleteButton.setAttribute('aria-pressed',String(pending));
      deleteButton.setAttribute('aria-label',pending
        ?`Confirm permanent deletion of ${session.name}, session ID ${session.session_id}, ${formatCount(session.sample_count)} samples`
        :`Delete completed session ${session.name}, session ID ${session.session_id}, ${formatCount(session.sample_count)} samples`);
      deleteButton.textContent=deleting?'Deleting…':(pending?`Confirm delete …${shortSessionId} · ${formatCount(session.sample_count)} samples (within 5 seconds)`:'Delete this unused session');
      deleteButton.addEventListener('click',()=>deleteCompletedSimulationSession(session));
    }
    actions.appendChild(deleteButton); card.appendChild(actions);
    return card;
  });
  $('simulationSessionList').replaceChildren(...cards);
}
function renderSessionQuality(summary){
  const count=asNumber(summary?.sample_count)||0; const valid=asNumber(summary?.valid_ultrasonic_samples)||0;
  const invalid=asNumber(summary?.invalid_ultrasonic_samples)||0; const labels=summary?.label_counts||{};
  const safe=asNumber(labels.safe)||0,danger=asNumber(labels.danger)||0,unknown=asNumber(labels.unknown)||0;
  const validRate=count?valid/count:0; const invalidRate=count?invalid/count:0; const coverage=asNumber(summary?.label_coverage)??(count?(safe+danger)/count:0);
  setBar('qualityValidBar',validRate); setBar('qualityInvalidBar',invalidRate); setBar('qualityCoverageBar',coverage);
  $('qualityValidText').textContent=`${formatCount(valid)} / ${formatCount(count)}`; $('qualityInvalidText').textContent=formatCount(invalid); $('qualityCoverageText').textContent=formatPercent(coverage);
  $('qualityDetails').textContent=`Server statistics · ultrasonic validity ${formatPercent(validRate)} · distance ${formatCount(summary?.distance_min_mm)}–${formatCount(summary?.distance_max_mm)} mm · water rise ${formatCount(summary?.water_rise_min_mm)}–${formatCount(summary?.water_rise_max_mm)} mm`;
  const divisor=Math.max(1,safe+danger+unknown); $('coverageSafe').style.width=`${safe/divisor*100}%`; $('coverageDanger').style.width=`${danger/divisor*100}%`; $('coverageUnknown').style.width=`${unknown/divisor*100}%`;
  $('coverageSafeCount').textContent=formatCount(safe); $('coverageDangerCount').textContent=formatCount(danger); $('coverageUnknownCount').textContent=formatCount(unknown);
  $('coverageNote').textContent=`Label version ${$('labelVersion').value} · unlabelled or cleared samples remain UNKNOWN.`;
}
function emptyTimeline(){
  currentTimeline={session:null,points:[],labels:[]}; renderSimulationChart(); renderSimulationSamples([]); renderSimulationLabels([]);
  renderSessionQuality({}); $('chartCaption').textContent='Select a session to view measured samples';
  $('saveSimulationLabel').disabled=true;
}
async function loadSimulationDetails(){
  const id=$('simulationSession').value;
  selectedSimulationSession=simulationSessions.find(session=>session.session_id===id)||null;
  $('stopSimulation').disabled=!selectedSimulationSession||selectedSimulationSession.state!=='active';
  $('saveSimulationLabel').disabled=true;
  renderSessionList();
  if(!selectedSimulationSession){ emptyTimeline(); return; }
  $('saveSimulationLabel').disabled=selectedSimulationSession.state!=='completed';
  $('simulationStatus').textContent=`${selectedSimulationSession.name} · ${selectedSimulationSession.state.toUpperCase()} · ${formatCount(selectedSimulationSession.sample_count)} samples · baseline ${selectedSimulationSession.baseline_distance_mm??'--'} mm · DEVICE-MEASURED`;
  const serial=++simulationRequestSerial;
  try {
    const version=Math.max(1,Number($('labelVersion').value)||1);
    const timeline=await fetchJson(`/api/v1/simulations/sessions/${encodeURIComponent(id)}/timeline?device_id=${DEVICE}&label_version=${version}&limit=5000`);
    if(serial!==simulationRequestSerial||$('simulationSession').value!==id) return;
    currentTimeline={session:normaliseSessionSummary(timeline.session),points:timeline.points||[],labels:timeline.labels||[]};
    selectedSimulationSession={...selectedSimulationSession,...currentTimeline.session};
    renderSimulationSamples(currentTimeline.points); renderSimulationLabels(currentTimeline.labels); renderSimulationChart(); renderSessionQuality(currentTimeline.session);
    const first=currentTimeline.session?.first_seq, last=currentTimeline.session?.last_seq;
    $('chartCaption').textContent=`${formatCount(currentTimeline.points.length)} time points · SEQ ${first??'--'}–${last??'--'} · purple crosses and line gaps mark ultrasonic samples rejected by the server`;
    $('labelStatus').textContent=selectedSimulationSession.state==='completed'?'Click the chart twice to select an interval, then save a SAFE, DANGER, or UNKNOWN annotation.':'Collection is active. Samples continue to refresh; annotations are available after the session stops.';
  } catch(error){ $('labelStatus').textContent=String(error); emptyTimeline(); }
}
async function loadSimulationSessions({throwOnError=false}={}){
  const previous=$('simulationSession').value;
  try {
    const version=Math.max(1,Number($('labelVersion').value)||1);
    const overview=await fetchJson(`/api/v1/simulations/overview?device_id=${DEVICE}&label_version=${version}`);
    simulationOverview=overview; renderOverview(overview);
    simulationSessions=(overview.sessions||[]).map(normaliseSessionSummary);
    populateSensorSessionSelectors();
    const options=simulationSessions.map(session=>{
      const option=document.createElement('option'); option.value=session.session_id;
      option.textContent=`${session.name} · ${session.state} · ${formatCount(session.sample_count)} samples`; return option;
    });
    $('simulationSession').replaceChildren(...options);
    if(simulationSessions.some(session=>session.session_id===previous)) $('simulationSession').value=previous;
    if(!simulationSessions.length){
      selectedSimulationSession=null; $('simulationStatus').textContent='No sessions yet. Start collection from the ESP32 COLLECTION screen.';
      renderSessionList(); emptyTimeline(); populateSensorSessionSelectors(); return;
    }
    await loadSimulationDetails();
  } catch(error){
    $('simulationStatus').textContent=String(error);
    if(throwOnError) throw error;
  }
}
async function stopSelectedSimulation(){
  if(!selectedSimulationSession||selectedSimulationSession.state!=='active') return;
  try {
    await sendJson(`/api/v1/simulations/sessions/${encodeURIComponent(selectedSimulationSession.session_id)}/stop`,'POST',{device_id:DEVICE});
    $('labelStatus').textContent='The collection session has stopped. You can now select an interval and add an annotation.'; await loadSimulationSessions();
  } catch(error){ $('labelStatus').textContent=String(error); }
}
async function saveSimulationLabel(){
  if(!selectedSimulationSession||selectedSimulationSession.state!=='completed'){
    $('labelStatus').textContent='Stop the collection session before adding an annotation.'; return;
  }
  const startText=$('labelStartSeq').value; const endText=$('labelEndSeq').value;
  const start=Number(startText); const end=Number(endText); const version=Number($('labelVersion').value);
  if(!startText||!endText||!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<start||!Number.isInteger(version)||version<1){
    $('labelStatus').textContent='Enter a valid start sequence, end sequence, and label version.'; return;
  }
  try {
    const saved=await sendJson('/api/v1/simulations/labels','PUT',{session_id:selectedSimulationSession.session_id,
      device_id:DEVICE,start_seq:start,end_seq:end,label:$('simulationLabel').value,note:$('labelNote').value.trim(),version});
    $('labelStatus').textContent=`Annotation saved: ${saved.label.toUpperCase()} · #${saved.start_seq}–#${saved.end_seq} · version ${saved.version}`;
    await loadSimulationSessions();
  } catch(error){ $('labelStatus').textContent=String(error); }
}
async function refreshEnvironment(){
  const environment=await fetchJson(`/api/v1/environment?device_id=${DEVICE}`);
  setEnvironment(environment); lastEnvironmentFetch=Date.now();
}
async function refresh(){
  try {
    const [latest,history]=await Promise.all([fetchJson(`/api/v1/telemetry/latest?device_id=${DEVICE}`),fetchJson(`/api/v1/telemetry?device_id=${DEVICE}&limit=20`)]);
    setLatest(latest); setHistory(history); $('error').textContent='';
  } catch(e) { $('online').textContent='Waiting for telemetry'; $('online').className='status offline'; if(!String(e).includes('404')) $('error').textContent=e; }
  if(Date.now()-lastEnvironmentFetch>60000){ try { await refreshEnvironment(); } catch(e){ $('error').textContent=e; } }
}
$('simulationSession').addEventListener('change',loadSimulationDetails);
$('reloadSimulations').addEventListener('click',loadSimulationSessions);
$('stopSimulation').addEventListener('click',stopSelectedSimulation);
$('saveSimulationLabel').addEventListener('click',saveSimulationLabel);
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
  loadModels(); loadSimulationSessions(); refresh();
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
