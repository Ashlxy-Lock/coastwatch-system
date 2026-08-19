# 海岸预警本地服务器

这个 FastAPI 服务接收 ESP32 的遥测、保存到 SQLite，并在浏览器显示中文监控页。
它只处理设备状态，不需要、不会接收或保存 Wi-Fi 密码。

## Windows 安装与启动

需要 Python 3.11 或更高版本。在 PowerShell 中运行：

```powershell
Set-Location 'F:\海岸预警系统\server'
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`app.main` 包含标注、训练和删除等内部管理接口，必须只监听回环地址
`127.0.0.1`，不要用 `0.0.0.0` 将它暴露到局域网或公网。设备与管理员的远程访问
应通过带设备令牌或管理员会话/CSRF 保护的 `app.gateway`。

如果系统没有 `py` 命令，把第二行改成 `python -m venv .venv`。启动后可访问：

如果电脑尚未把 Python 加入 PATH，临时验证时也可以用任意现有的 Python 3.11+
可执行文件完整路径执行 `-m venv .venv`；虚拟环境建好后统一使用
`.\.venv\Scripts\python.exe`，项目中不需要写死工具自带 Python 的安装路径。
长期使用仍建议安装官方 Python 3.11+ 并勾选“Add Python to PATH”。

- 监控页面：`http://127.0.0.1:8000/`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/api/v1/health`

默认数据库为 `data/coastal_warning.db`。若需改变位置，可在启动前设置环境变量：

```powershell
$env:COASTAL_DB_PATH='D:\coastal-data\telemetry.db'
```

## 天气与海况配置

服务器使用 Open-Meteo 官方 Weather API 和 Marine API 获取当前位置的天气、风、
浪高、浪周期、海表温度、海面高度及海流信息，不需要 API 密钥。启动服务器前必须
显式配置海滩坐标和显示名称，例如：

```powershell
$env:COAST_LATITUDE='36.0671'
$env:COAST_LONGITUDE='120.3826'
$env:COAST_LOCATION_NAME='青岛海滨'
$env:COAST_DISPLAY_LOCATION='QINGDAO COAST'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

三个变量任意一个缺失或坐标超出范围时，`/api/v1/environment` 会明确返回
`source: "demo"`、`provider: "built-in-demo"`，页面也会标注“演示数据”，不会把
内置示例伪装成实时数据。

真实数据在服务器进程内缓存 10 分钟。缓存过期后若 Open-Meteo 暂时不可访问，
服务器会返回最后一次成功的数据，并设置 `source: "stale"`、`stale: true`；若启动
后尚未成功获取过数据，则返回所有实时测量值为空的“环境数据暂不可用”状态。

Open-Meteo 的潮汐、海面高度和海流来自数值模型，近岸精度有限。本系统只把它们
作为公众提示和演示信息，不得用于航海、救援调度或替代当地海事部门通告。

## 网页选择 ESP32 显示地区

打开本地监控页 `http://127.0.0.1:8000/`，在“屏幕显示地区”中选择地区并点击
“保存到设备”。内置目录固定提供 16 个已经验证能返回海况的全球海岸，英国 6 个
并以 Brighton 为首项。配置按 `device_id` 保存到同一个 SQLite 数据库；仅含 ASCII
的 `display_location` 供当前 ESP32 点阵字体显示。

页面提供两种选择方式：

- 内置全球海岸目录，标记为 `kind: "coast"`，天气页同时显示海况；
- 输入中文或英文地名搜索全球普通地区，标记为 `kind: "place"`，只显示陆地天气。

London 可以作为普通天气地点，但不属于海岸目录，也不会被命名为 “London Coast”。

保存后 ESP32 无需更改接口，继续请求
`GET /api/v1/environment?device_id=COAST_01` 即会收到该设备所选地区的数据。
在线搜索失败时仍可使用内置列表。对应本地管理接口为：

```text
GET /api/v1/locations/presets
GET /api/v1/locations/search?q=青岛&count=8
GET /api/v1/device-location?device_id=COAST_01
PUT /api/v1/device-location
```

保存请求示例：

```json
{
  "device_id": "COAST_01",
  "kind": "coast",
  "location": "Brighton, England, United Kingdom",
  "display_location": "BRIGHTON ENGLAND GB",
  "latitude": 50.82838,
  "longitude": -0.13947
}
```

## ESP32 与电脑处于同一局域网

ESP32 不能使用 `127.0.0.1`，因为那代表 ESP32 自己。请运行 `ipconfig` 找到电脑
连接当前 Wi-Fi 的 IPv4 地址，例如 `192.168.1.100`，然后在 ESP32 的
`include/secrets.h` 中只配置服务器地址：

```cpp
#define SERVER_BASE_URL "http://192.168.1.100:8001"
```

临时局域网联调只能把带设备令牌鉴权的 `app.gateway` 绑定到 `0.0.0.0:8001`；
`app.main` 必须继续只监听 `127.0.0.1`。Windows 首次询问防火墙权限时，仅允许
专用网络。健康检查也必须携带 `X-Device-Token`。正式运行仍建议使用现有
Cloudflare Tunnel，让 gateway 只监听回环地址。

## API

ESP32 上传：

```http
POST /api/v1/telemetry
Content-Type: application/json
```

```json
{
  "device_id": "COAST_01",
  "seq": 42,
  "uptime_ms": 123456,
  "distance_mm": 815,
  "water_rise_mm": 126,
  "rise_rate_mm_s": 21,
  "person_detected": true,
  "alarm_level": 3,
  "health_flags": 7,
  "wifi_rssi": -55
}
```

查询接口：

```text
GET /api/v1/telemetry/latest?device_id=COAST_01
GET /api/v1/telemetry?device_id=COAST_01&limit=50
GET /api/v1/environment?device_id=COAST_01
GET /api/v1/risk?device_id=COAST_01
GET /api/v1/models?device_id=COAST_01
PUT /api/v1/device-model
GET /api/v1/locations/presets
GET /api/v1/locations/search?q=青岛
GET /api/v1/device-location?device_id=COAST_01
PUT /api/v1/device-location
GET /api/v1/health
```

环境接口示例：

```json
{
  "location": "青岛海滨",
  "display_location": "QINGDAO COAST",
  "kind": "coast",
  "weather": "局部多云",
  "weather_code": 2,
  "air_temperature_c": 28.6,
  "humidity_percent": 78.0,
  "wind_speed_kmh": 16.2,
  "wind_direction_deg": 135.0,
  "water_temperature_c": 25.8,
  "wave_height_m": 0.7,
  "wave_period_s": 5.4,
  "sea_level_height_m": 0.31,
  "tide_status": "涨潮",
  "ocean_current_velocity_kmh": 0.9,
  "ocean_current_direction_deg": 82.0,
  "source": "open-meteo",
  "provider": "open-meteo",
  "stale": false,
  "updated_at": "2026-08-02T08:30:00Z"
}
```

第三方没有提供的单项会返回 `null`。`source` 可能为 `open-meteo`、`demo`、
`stale` 或预留的 `manual`。

## UK 官方数据第三模型与超声波外部测试

第三模型现为 `uk-official-coast-logreg-v2`。它只从受保护目录中经过契约校验、
原始文件哈希复核的英国官方/权威海岸数据 bundle 训练。ESP32 超声波数据
用于模型冻结后的硬件外部测试，绝不参与 scaler、逻辑回归系数、模型决策阈值或
水位规则基线的拟合。旧的人工场景/人工标签训练流程只保留为折叠的 legacy 审计资料，
生产默认拒绝再次训练。

管理员操作顺序：

1. 按 [官方数据 bundle 说明](docs/OFFICIAL_DATASET_BUNDLE.md) 准备
   `manifest.json`、`harmonized.csv` 与 `raw/` 原始归档；
2. 在后台点击“扫描受保护目录”，选择已注册 dataset 和站点并查看 readiness；
3. 手动启动训练。预处理和系数只拟合 train；逻辑回归及逐站水位规则的操作阈值
   都只在 validation 选择；frozen test 只评估一次。激活至少要求 3 个站点、每个
   split 200 条可用记录，并且每个所选站点的 frozen test 都含两类；
4. 检查逐站/site-macro 指标、简单水位规则基线、来源 assurance 和全部哈希后，
   再手动激活成功工件；
5. 用一个独立、已结束的超声波会话建立正式线性映射，采集另一个外部测试会话；
6. 后台执行外部测试。映射公式固定为
   `proxy_level_m = reference_level_m + gain * (water_rise_mm / 1000)`，不裁剪
   超出训练范围的值，而是明确标记 OOD。

最终模型有 18 个输入：线性映射后的相对水位，加上冻结官方测试行中的潮汐、浪高、
浪周期、风速、阵风、气压、降雨、气温、湿度、水温、海流、小时/年周期和经纬度。
超声波只替换第一项。后台必须始终显示
`SENSOR ROWS USED FOR FIT = 0 · SCALER = 0 · THRESHOLD = 0`。

当前来源保障为 `operator_attested_raw_hash_verified`：服务器会重算 CSV 与 `raw/`
文件哈希并验证结构，但不会替操作者向数据所有者核验许可，也没有独立重放数据清洗和
标签派生。因此 `deterministic_importer_replay_verified=false`，模型只能用于课程演示
和 shadow 研究，输出是未来高水位条件的模型概率，不是海啸或真实灾害概率，也不会
反向控制 ESP32 本地报警。

设备可用接口：

```text
GET  /api/v1/models?device_id=COAST_01
PUT  /api/v1/device-model
POST /api/v1/simulations/sessions
GET  /api/v1/simulations/sessions/active?device_id=COAST_01
POST /api/v1/simulations/sessions/{session_id}/stop
```

仅管理员后台提供的 UK 官方训练/外部测试接口（设备根网关故意不暴露）：

```text
GET  /api/v1/official-datasets
POST /api/v1/official-datasets/rescan
GET  /api/v1/official-training/readiness
GET  /api/v1/official-training/runs
POST /api/v1/official-training/runs
POST /api/v1/official-training/runs/{run_id}/activate
GET  /api/v1/official-model
GET|PUT|DELETE /api/v1/sensor-test/device-profile
GET|POST /api/v1/sensor-test/runs
```

模型目录保留三项：`coastal-risk-logreg-v1` 默认可用；ImpactNet v2 当前只有合成研究
bundle 和无实时特征提供器，因此为 `unavailable`；第三项
`uk-official-coast-logreg-v2` 只有官方 bundle、训练、完整工件校验与手动激活均成功后
才为 `ready`。服务器拒绝新选择任何非 ready 模型；若已经选择的官方工件后来损坏，
风险/环境接口会 503 fail closed，而不会悄悄换回旧模型。

## Cloudflare Tunnel 公网设备网关

不要把 `app.main:app` 直接暴露到公网，因为它包含监控页面、API 文档和遥测查询接口。
公网 Tunnel 必须使用独立的最小网关 `app.gateway:app`。该网关只提供设备运行所需接口：

```text
POST /api/v1/telemetry
GET  /api/v1/environment
GET  /api/v1/risk
GET  /api/v1/locations/presets
GET  /api/v1/locations/search
PUT  /api/v1/device-location
GET  /api/v1/models
PUT  /api/v1/device-model
POST /api/v1/simulations/sessions
GET  /api/v1/simulations/sessions/active
POST /api/v1/simulations/sessions/{session_id}/stop
GET  /api/v1/health
```

所有接口都要求设备令牌。现有固件可继续发送 `X-Device-Token`，也可使用标准
`Authorization: Bearer <token>`。网关使用环境变量
`COAST_DEVICE_TOKEN` 保存设备令牌；令牌不得提交到仓库、截图或日志。请使用足够长的随机值，
并在同一个 PowerShell 窗口内设置后启动网关：

```powershell
Set-Location 'F:\海岸预警系统\server'
$env:COAST_DEVICE_TOKEN='<replace-with-a-random-secret>'
.\.venv\Scripts\python.exe -m uvicorn app.gateway:app --host 127.0.0.1 --port 8001
```

未设置或只设置空白令牌时，网关会拒绝启动。网关故意关闭 `/`、`/docs`、`/redoc`、
`/openapi.json`、所有遥测查询、模拟样本、人工标签和训练接口。启动后，在另一个
PowerShell 窗口建立临时 Tunnel：

```powershell
cloudflared tunnel --url http://127.0.0.1:8001
```

把 Cloudflare 输出的 `https://*.trycloudflare.com` 地址作为设备服务器地址。ESP32 的每个网关请求
还必须发送：

```http
X-Device-Token: <the-same-secret>
```

屏幕端既可获取内置常用地区，也可通过 Open-Meteo 搜索全球地名。搜索接口每次最多返回
8 个紧凑结果；例如搜索英国 Brighton：

```http
GET /api/v1/locations/search?q=Brighton&count=8
```

```json
[
  {
    "id": "geo_2654710",
    "kind": "place",
    "name": "Brighton · England · United Kingdom",
    "display_location": "BRIGHTON ENGLAND GB",
    "lat": 50.82838,
    "lon": -0.13947
  }
]
```

选择请求始终只提交服务器签发的地点 ID。内置海岸使用固定 ID：

```json
{
  "device_id": "COAST_01",
  "location_id": "uk_brighton"
}
```

全球搜索结果则提交 `geo_<Open-Meteo id>`：

```json
{
  "device_id": "COAST_01",
  "location_id": "geo_2654710"
}
```

网关收到 `geo_` ID 后会通过 Open-Meteo `/v1/get` 重新解析英文地点、行政区、国家和坐标，
然后以普通 `place` 保存到 SQLite，并且不会请求海况；请求模型拒绝额外的 `lat`、`lon`
等字段，因此设备不能借公网接口写入任意坐标。所有搜索和选择请求与其他网关接口一样
必须携带设备令牌。

Quick Tunnel 地址会在进程重启后变化，且电脑、Uvicorn 和 `cloudflared` 都必须持续运行。
本地监控页面仍可单独用 `app.main:app` 在 8000 端口启动，不经过公网 Tunnel。

## 测试

激活虚拟环境并安装依赖后运行：

```powershell
python -m pytest
```

测试使用临时 SQLite 文件，不会修改正式数据库。
