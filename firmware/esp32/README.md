# ESP32-S3 联网网关

本工程是海岸预警系统的联网侧固件。ESP32-S3 只负责接收 STM32 遥测、
连接 Wi-Fi、上传服务器并返回网络状态；它不判断危险等级，也不控制本地
声光报警。断网、服务器离线或 ESP32 重启时，STM32 的本地报警必须继续工作。

## 硬件目标

- 模组：ESP32-S3-WROOM-1-N16R8（16 MB Flash、8 MB PSRAM）
- 开发板：ESP32-S3-DevKitC-1 类
- 框架：PlatformIO + Arduino
- STM32 链路：115200、8N1、换行结尾、3.3 V TTL

接线：

| STM32 专用 ESP32 串口 | ESP32-S3 |
|---|---|
| TX | GPIO8（RX） |
| RX | GPIO14（TX） |
| GND | GND |

必须交叉 TX/RX 并共地，不得向 ESP32 GPIO 输入 5 V。STM32 端应使用为 ESP32
单独分配的 UART；不要让 ESP32 与 OpenMV 同时并接在一组 UART 信号线上。
ESP32 请从稳定的 5 V/VIN 或 USB 供电，不要从 STM32 的 3.3 V 引脚取电。
GPIO8/14 不与当前 8 位并口屏和 I2C 触摸冲突。GPIO12 已由屏幕转接板硬接为
TOUCH_INT，禁止再接 STM32 TX；GPIO17/18 也已被并口屏幕占用。若实际硬件不同，
只改 `include/app_config.h` 中的两个引脚常量。

## 配置

所有可调项在 `include/app_config.h`。Wi-Fi 密钥单独保存在不会提交的文件中：

```powershell
Copy-Item include/secrets.example.h include/secrets.h
```

然后编辑 `include/secrets.h`：

```cpp
#define WIFI_SSID "你的2.4GHz Wi-Fi"
#define WIFI_PASSWORD "你的密码"
#define SERVER_BASE_URL "http://电脑局域网IP:8000"
```

`include/secrets.h` 已加入 `.gitignore`。不创建该文件也能编译和运行，设备会进入
UART-only 模式：照常接收/校验 `$TEL`，每秒返回离线 `$NET`，但不会连接网络。

## 编译与烧录

在本目录运行：

```powershell
pio run -e esp32s3-n16r8
pio run -e esp32s3-n16r8 -t upload
pio device monitor -b 115200
```

首轮请把电脑接到开发板标注 **UART** 或 **USB-to-UART** 的 Type-C 口，调试日志
固定走 UART0/板载 USB 转串口；不要接仅用于原生 USB 的 Type-C 口来找 `Serial`
日志。若板上只有一个 USB 口，则直接使用该口。

若上传速度 921600 在所用 USB 线或串口芯片上不稳定，可临时改为 460800 或
115200。首次烧录若不能自动进入下载模式，按住 BOOT、点一下 RESET，再松开
BOOT。

## 串口独立联调

没有 Wi-Fi、服务器或 STM32 时，可用 3.3 V USB-TTL 测试：

1. TTL TX 接 GPIO8，TTL RX 接 GPIO14，GND 共地；
2. 串口设置 115200、8N1；
3. 发送下面一整行，末尾必须带 `\n`：

```text
$TEL,42,123456,815,126,21,1,3,7*63
```

USB 调试口应打印 `[UART] TEL seq=42 ...`。GPIO14 每秒返回类似：

```text
$NET,0,0,-127,0*76
```

网络字段变化时校验也会动态变化。协议限制每帧最多 160 字节；超长帧、字段
错误和校验错误会丢弃，不会覆盖上一条有效数据。

## 运行机制

- 主 Arduino 循环只轮询 UART 环形缓冲、按换行拆帧、校验 `$TEL` 并发送 `$NET`；
- Wi-Fi 重连与 HTTP POST 在 Core 0 的独立 FreeRTOS 任务中执行；
- 遥测队列满时丢弃最旧项、保留最新项，绝不等待网络；
- 普通遥测最多每 2 秒上传一次，报警等级变化立即尝试上传；仿真采集会话
  活动时改为 500 ms，始终只保留并上传最新完整 `$TEL`；
- HTTP 失败按 5 s、10 s、30 s、60 s 退避；
- 每秒向 STM32 返回 `$NET,<wifi>,<server>,<rssi>,<unix_time>*<checksum>`；
- 服务器只有成功接收一次 2xx POST 后才标记为在线。

POST 地址为 `<SERVER_BASE_URL>/api/v1/telemetry`，JSON 字段与根 README 一致。

## 触控海岸概览

设备默认显示 800x480 `COASTWATCH` 风险概览，并保留原有天气、地点选择、
全球搜索和板上 Wi-Fi 配置：

- 左侧显示未来 6 小时研究风险等级与 `MODEL CONFIDENCE`；
- 右侧显示浪高/浪周期、风速、STM32 实时超声距离/相对水位变化与本地报警等级；
- 超声卡直接使用最新 `$TEL`：有效距离为 20--4000 mm；有新鲜 TEL 但暂时没有
  有效回波时显示 `SEARCHING ECHO`，超过 2.5 秒没有 TEL 才显示
  `STM32 LINK OFFLINE`。两种状态都不会退回网络海况或伪造实时数值；
- `WEATHER` 进入天气详情，天气页右上角 `RISK` 返回风险概览；
- `MODELS` 进入服务器模型库；风险页底栏继续显示 `/risk` 返回的实际
  `model_version`，模型库显示服务器选中的 `display_name`；
- `WIFI` 进入原有板上联网流程，天气卡仍可打开地点选择；
- 风险接口尚无 STM32 遥测时明确显示 `NO SENSOR DATA`，不会填充演示概率；
- `environmental_probability` 只标为模型分类置信度，不能解释为灾害发生概率；
- `SHADOW / RESEARCH` 与 `LOCAL FIRST` 提醒用户：服务器模型只读展示，
  不会反向控制 STM32，本地规则报警始终优先。

ESP32 每 10 秒读取 `<SERVER_BASE_URL>/api/v1/risk?device_id=COAST_01`。
响应通过固定大小结构和严格字段/范围/枚举校验后才跨任务发布；HTTP 404 被视为
“等待首帧遥测”，其他错误保留失效标记并退避重试。

## 板上模型选择与仿真采集

风险页点击 `MODELS` 后，ESP32 异步读取：

```text
GET /api/v1/models?device_id=COAST_01
```

响应必须严格为对象 `{selected_model_id, models:[...]}`，且最多三项。每项必须有
`model_id`、`display_name`、`status`、`mode`、`description`；`status` 只接受
`ready`、`unavailable`、`not_trained`。只有 `ready` 卡片可以点击，选择请求为：

```text
PUT /api/v1/device-model
{"device_id":"COAST_01","model_id":"..."}
```

选择成功后服务器决定后续 `/api/v1/risk` 使用哪个模型。ESP32 只显示结果，不在
本地执行训练，也不把模型输出送回 STM32 报警链路。页面始终显示
`SIMULATION / RESEARCH` 和 `LOCAL FIRST`。

模型页点击 `COLLECTION` 进入超声波仿真采集页。这个页面只有 `START/STOP` 会话
控制，不允许打标签；安全/危险时间段必须在后台网站标记。`START` 在还没有收到
STM32 `$TEL` 时也可用，此时明确显示 `WAITING STM32`：

```text
POST /api/v1/simulations/sessions
{"device_id":"COAST_01","name":"ESP32 WATER SIMULATION"}
```

启动与活动会话恢复响应必须包含 `session_id`、`state`、`started_at`和
`sample_count`。`sample_count` 仅保存为该次服务器响应确认的
`SERVER STORED@SYNC`，不会因本地收到 TEL 或普通 2xx 上传回复而自行猜测增长。
活动会话期间，正常遥测
POST 额外携带：

```json
{"simulation_session_id":"<server-issued id>"}
```

屏幕把 STM32 `water_rise_mm` 作为第一张 `LEVEL CHANGE` 卡突出显示；
`distance_mm` 明确标为 `SENSOR GAP`，表示探头到反射面的原始间距，并不表示
海平面或水位。风险总览也以相对变化为主值、以 `SENSOR GAP` 为辅助值。屏幕还
显示本次 ESP32 运行在
当前会话内观察到的 `LOCAL VALID/TEL`；这两个本地计数在恢复已有会话时从
`0/0` 开始，不与服务器已存样本数混合。按
`health_flags.bit0 + 20..4000 mm + 2.5 s 新鲜度` 判定的超声质量。无回波、
越界或 UART 过期均会 fail-closed 显示 `NO ECHO/STALE`，不会把旧距离伪装成
有效数据。底部同时显示 `rise_rate_mm_s`、最近一次服务器上传的 HTTP 状态、
TEL 序号和 `UPLOAD ACK` 成功/失败次数；超过 2.5 秒没有成功确认会显示
`ACK DELAYED`。HTTP 2xx 只更新 ACK，不把尚未由会话接口重新确认的本地 TEL
写成“服务器已存数”。
停止请求为：

```text
POST /api/v1/simulations/sessions/{session_id}/stop
{"device_id":"COAST_01"}
```

`STOP` 必须在 5 秒内二次点击 `CONFIRM STOP` 才会真正发送，避免误触结束采集；
所有会话按钮还有 250 ms 动作冷却。`STARTING/STOPPING` 期间按钮禁用并明确显示
正在等待服务器。若停止失败，页面显示 HTTP 错误并保持会话为 open，继续用原
session id 上传；用户可再次执行 `RETRY STOP -> CONFIRM STOP`，不会假装会话
已经关闭。ESP32 不把 session id 写入 NVS，
但每次启动或重新联网会查询服务器恢复尚未关闭的会话：

```text
GET /api/v1/simulations/sessions/active?device_id=COAST_01
```

HTTP 200 使用与启动响应相同的固定大小 SessionRecord parser 恢复 open 状态及
服务器样本数，404 表示没有活动会话。若 `START` 因已有会话返回 409，固件也会
立即执行同一查询并接管服务器上的原 session，避免设备永久卡在启动冲突。
所有 HTTP 工作仍在 Core 0 网络任务，UART 拆帧和本地报警不被阻塞。

触控区域（800x480，左上为原点）：

- 风险页：`MODELS=(450,15,110,38)`、`WEATHER=(574,15,92,38)`、
  `WIFI=(680,15,92,38)`；
- 模型页：`BACK=(28,15,110,38)`、`COLLECTION=(602,15,170,38)`；
- 模型卡：`(28,96,236,286)`、`(280,96,236,286)`、
  `(532,96,236,286)`；
- 采集页：`BACK=(28,15,110,38)`、`START/STOP=(602,15,170,38)`。

## 主机单元测试

协议、校验、环形缓冲和超长帧丢弃可以不接硬件测试：

```powershell
pio test -e native
```

模型目录和会话响应的固定大小解析、未知状态/过量模型拒绝、选中模型一致性、
Start/Stop 二次确认动作、超声质量失效策略和触控区域边界在
`test/test_model_control`。没有主机 `gcc/g++` 时可至少用
真实 ESP32 工具链做不烧录的编译/链接检查：

```powershell
pio test -e esp32s3-test -f test_model_control --without-uploading --without-testing
```

## On-device Wi-Fi setup

The compiled `WIFI_SSID` and `WIFI_PASSWORD` are first-boot fallback values.
The normal setup flow is entirely on the 800x480 touch display:

1. Tap the `WIFI` button in the weather header, or tap the Wi-Fi card while
   the network-status page is visible.
2. Wait for the radio scan, then tap a network.
3. Enter an 8-63 character password with the masked on-screen keyboard.
   `UP/LOW` changes case and `SYM/ABC` changes the character page. Open
   networks skip password entry.
4. Tap `CONNECT`. Credentials are written to the `coast-net/profile` NVS blob
   only after the ESP32 receives an IP address. A failed password or timeout
   does not replace the saved profile; the previous network reconnects after
   leaving the setup screen.
5. The picker shows `SAVED: <SSID>`. Tap the red `FORGET` button and confirm to
   remove that profile. The firmware writes a persistent empty tombstone before
   disconnecting, so a reboot cannot revive the build-time fallback Wi-Fi. A
   later successful `CONNECT` replaces the tombstone with the new profile.

Holding the board's `BOOT` button for 1.5 seconds while the firmware is running
also opens the Wi-Fi picker. The scan and connection state machine runs only on
the network task, so UART receive and the STM32's local alarm path keep running.
The password is never printed and the LCD shows only its length and mask.

## On-device global location search

The weather card opens the location picker. Its 16 static entries are verified
global coasts (six in the UK); `SEARCH` opens an English ASCII keyboard for any
ordinary place worldwide:

1. Enter at least two characters, for example `LONDON`, `BRIGHTON`,
   `BOURNEMOUTH`, or `ST IVES`.
2. Tap `SEARCH`. The authenticated gateway queries the global Open-Meteo
   geocoder and returns at most eight compact results.
3. Each result includes an administrative area and country code so namesakes
   can be distinguished, for example `LONDON ENGLAND GB` and
   `LONDON ONTARIO CA`.
4. Tap a result and then `APPLY`. The device submits only the server-issued
   `geo_<id>`; the server resolves the canonical coordinates again before
   saving them. Coordinates sent by a device are never trusted.

The distinction is deliberate: curated entries carry `kind=coast` and the LCD
shows `COAST WEATHER` with marine values. Search results carry `kind=place` and
the LCD shows `LOCAL WEATHER`; wave and sea-temperature cards read `COAST ONLY`.
For example, London remains searchable for local weather but is never presented
as a coast.

After TLS time synchronization, the network task fetches the 16 coast presets
once in the background and keeps them in a dedicated RAM cache. Opening the
picker then publishes that cache immediately; global search results never
overwrite it. A successful `APPLY` publishes the selected name with
`UPDATING` and empty metrics before the slower environment request completes,
so old measurements are never shown under a new location name. Interactive
screens poll UI state every 50 ms while the normal weather page remains at
250 ms.

An empty result list is a normal response. Global search uses a separate
12-second read timeout because geocoding may require an upstream round trip;
the UART receive loop and STM32 local alarm path continue on separate tasks.

已在实际 ESP32-S3 N16R8 + 800x480 触摸屏上验证扫描、板上密码输入、
获取 IP 后保存、Wi-Fi/天气服务恢复。整机联调仍需验证 STM32 UART 接线、
HTTP 超时期间 UART 持续收帧，以及连续运行稳定性。
