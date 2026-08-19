# ESP32-S3 单板海岸控制器

本工程将传感器、本地规则、LCD/触摸和联网集中到 ESP32-S3。ESP32 直接接收
OpenMV VIS、驱动超声波、建立稳定基准、计算水位变化/变化率和本地报警等级，
再把同一份 `TelemetryFrame` 同时交给 LCD 与服务器。服务器模型可以切换
OpenMV 的人体检测采样模式，但不能降低、覆盖或控制设备本地报警规则。

`firmware/stm32` 保留为已验证的回滚版本，但正常单板路径不再通过 STM32
`TEL/NET` 串口。2026-08-18 已完成 ECHO 分压、单板烧录和端到端实机验证。

## 硬件目标

- 模组：ESP32-S3-WROOM-1-N16R8（16 MB Flash、8 MB PSRAM）
- 开发板：ESP32-S3-DevKitC-1 类
- 框架：PlatformIO + Arduino
- OpenMV 链路：115200、8N1、全双工校验帧（VIS 上行、CTL 下行）

接线：

| 信号 | ESP32-S3 |
|---|---|
| OpenMV P4 / TX | GPIO8（UART1 RX） |
| OpenMV P5 / RX | GPIO14（UART1 TX） |
| 超声 TRIG | GPIO10 |
| 超声 ECHO | **经 5V→3.3V 分压后**接 GPIO40（GPIO42 已由 LCD D/C 占用） |
| 公共地 | GND |

ESP32-S3 没有任何 5 V-tolerant GPIO。HC-SR04 ECHO 必须经过核验的分压或
经过核验的电平转换器；改接其他 GPIO、ADC 或下载串口都不能绕过这一限制。
没有转换器时不得接 ECHO、不得启用单板超声。GPIO12 已由触摸 INT 占用，
GPIO35/36/37 被 N16R8 Octal PSRAM 使用，也不得复用。完整接线和迁移顺序见
`docs/ESP32_SINGLE_BOARD_MIGRATION.md`。

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

`include/secrets.h` 已加入 `.gitignore`。默认环境保持超声引脚未认领；只有完成
ECHO 降压的实物才使用下方显式单板环境。

## 编译与烧录

在本目录运行：

```powershell
pio run -e esp32s3-n16r8-singleboard
pio run -e esp32s3-n16r8-singleboard -t upload --upload-port COM8
pio device monitor -b 115200
```

`esp32s3-n16r8-singleboard` 会显式定义
`ULTRASONIC_ECHO_LEVEL_SHIFT_VERIFIED=1`；普通 `esp32s3-n16r8` 环境继续保持
fail-closed。中文工程路径下旧链接器无法生成 map 时，可把
`PLATFORMIO_BUILD_DIR` 指向纯英文临时目录。

首轮请把电脑接到开发板标注 **UART** 或 **USB-to-UART** 的 Type-C 口，调试日志
固定走 UART0/板载 USB 转串口；不要接仅用于原生 USB 的 Type-C 口来找 `Serial`
日志。若板上只有一个 USB 口，则直接使用该口。

若上传速度 921600 在所用 USB 线或串口芯片上不稳定，可临时改为 460800 或
115200。首次烧录若不能自动进入下载模式，按住 BOOT、点一下 RESET，再松开
BOOT。

## OpenMV 串口独立联调

没有 OpenMV 时，可用 3.3 V USB-TTL 测试：

1. TTL TX 接 GPIO8、TTL RX 接 GPIO14、GND 共地；
2. 串口设置 115200、8N1；
3. 发送下面一整行，末尾必须带 `\n`：

```text
$VIS,17,1,90,123,77,1*73
```

校验正确时 ESP32 更新人员状态；错误、超长或伪造的 VIS 会被丢弃。没有新的
合法 VIS 超过一秒，本地报警进入 `FAULT`，不会静默假设无人。

ESP32 每 500 ms 向 GPIO14 发送严格控制心跳：

```text
$CTL,<seq>,<danger>,<person_enable>,<environmental_level>*<XOR>\r\n
```

- `/risk` 为 `Ready`、`stale=false`、`model_source=model`、结果年龄不超过
  25 秒，且 `environmental_level` 为 warning/critical（2/3）时，发送
  `danger=1,person_enable=1`；ESP32 实时超声健康，且相对水位增量达到 100 mm
  或上升速率达到 25 mm/s 时，也发送同样的 danger，即使模型诊断仍为 0/1；
- 本地 danger 不使用综合 `alarm_level=2/3` 判定，因为 `person_in_zone` 本身就会
  把综合报警提高到 2，直接复用会形成“检测到人→制造危险→继续检测人”的反馈环；
  water rise 50 mm advisory、person-only、`alarm_level=4` 故障和超声异常均不置
  danger；
- 只有可信 `environmental_level=0`、`data_quality=ok`、`degraded=false`，且
  ESP32 刚生成的本地帧 `alarm_level=0`、超声/OpenMV 健康时，才发送
  `danger=0,person_enable=0,level=0`，OpenMV
  进入 baseline 低频检测并允许显示绿灯；
- level 0 但存在本地 advisory、质量异常或 degraded，以及 level 1 advisory，
  均发送 `danger=0,person_enable=1`，保持全速检测且禁止绿灯；只有上条独立的
  水位/速率阈值可以产生本地 danger；
- waiting、unavailable、stale、rule-fallback、超过 25 秒或非法等级在没有健康
  本地水位/速率危险时发送 `danger=0,person_enable=1`，要求全速 fail-safe 检测；
- `risk_level` 是组合展示值，RiskSnapshot 的 `local_alarm_level` 是服务器延迟
  回显，两者都不能伪装成“模型危险”或授权绿灯。CTL 只切换相机采样模式，
  ESP32 本地报警链路继续独立运行；
- `environmental_level` 仅供模型诊断，不能单独控制 LED；OpenMV 收到新鲜 danger
  先黄闪并全速检测，稳定检测到人后才转红。绿灯只能由严格的新鲜 `0,0,0`
  控制帧授权。

## 运行机制

- 主循环轮询 OpenMV UART 和超声状态机，回波边沿由短 ISR 捕获；
- 每 500 ms 发送一次 CTL 心跳；风险结果未知/过期时自动要求 fail-safe 全速监测；
- 三点稳定基准、五点中值、Q8 EMA、水位变化/变化率和本地报警均在设备计算；
- 每 500 ms 生成一帧本地遥测，即使传感器故障也持续生成诚实的故障帧；
- NVS 预留遥测序号段，设备重启后跳过未用尾段，避免 Collection 序号重复 409；
- Wi-Fi 重连与 HTTP POST 在 Core 0 的独立 FreeRTOS 任务中执行；
- 遥测队列满时丢弃最旧项、保留最新项，绝不等待网络；
- 普通遥测最多每 2 秒上传一次，报警等级变化立即尝试上传；仿真采集会话
  活动时改为 500 ms，始终只保留并上传最新本地帧；
- HTTP 失败按 5 s、10 s、30 s、60 s 退避；
- 服务器只有成功接收一次 2xx POST 后才标记为在线。

POST 地址为 `<SERVER_BASE_URL>/api/v1/telemetry`，JSON 字段与根 README 一致。

## 触控海岸概览

设备默认显示 800x480 `COASTWATCH` 风险概览，并保留原有天气、地点选择、
全球搜索和板上 Wi-Fi 配置：

- 左侧显示未来 6 小时研究风险等级与 `MODEL CONFIDENCE`；
- 右侧显示浪高/浪周期、风速、ESP32 实时超声距离/相对水位变化与本地报警等级；
- 超声卡直接使用最新本地帧：有效距离为 20--4000 mm；暂时没有有效回波时
  显示 `SEARCHING ECHO`，超过 2.5 秒没有本地发布才显示
  `SENSOR RUNTIME OFFLINE`。两种状态都不会退回网络海况或伪造实时数值；
- `WEATHER` 进入天气详情，天气页右上角 `RISK` 返回风险概览；
- `MODELS` 进入服务器模型库；风险页底栏继续显示 `/risk` 返回的实际
  `model_version`，模型库显示服务器选中的 `display_name`；
- `WIFI` 进入原有板上联网流程，天气卡仍可打开地点选择；
- 风险接口尚无设备遥测时明确显示 `NO SENSOR DATA`，不会填充演示概率；
- `environmental_probability` 只标为模型分类置信度，不能解释为灾害发生概率；
- `SHADOW / RESEARCH` 与 `LOCAL FIRST` 提醒用户：服务器模型只读展示，
  不会反向控制 ESP32 本地规则，设备本地报警始终优先。

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

选择成功后服务器决定后续 `/api/v1/risk` 使用哪个模型。ESP32 不在本地执行
训练；模型输出只能选择 OpenMV 检测采样模式，不能写入或降低 ESP32 本地报警。
页面始终显示
`SIMULATION / RESEARCH` 和 `LOCAL FIRST`。

模型页点击 `COLLECTION` 进入超声波仿真采集页。这个页面只有 `START/STOP` 会话
控制，不允许打标签；安全/危险时间段必须在后台网站标记。`START` 在超声尚未
建立基准时也可用，此时明确显示 `WAITING SENSOR`：

```text
POST /api/v1/simulations/sessions
{"device_id":"COAST_01","name":"ESP32 WATER SIMULATION"}
```

启动与活动会话恢复响应必须包含 `session_id`、`state`、`started_at`和
`sample_count`。`sample_count` 仅保存为该次服务器响应确认的
`SERVER STORED@SYNC`，不会因本地产生帧或普通 2xx 上传回复而自行猜测增长。
活动会话期间，正常遥测
POST 额外携带：

```json
{"simulation_session_id":"<server-issued id>"}
```

屏幕把 ESP32 本地计算的 `water_rise_mm` 作为第一张 `LEVEL CHANGE` 卡突出显示；
`distance_mm` 明确标为 `SENSOR GAP`，表示探头到反射面的原始间距，并不表示
海平面或水位。风险总览也以相对变化为主值、以 `SENSOR GAP` 为辅助值。屏幕还
显示本次 ESP32 运行在
当前会话内观察到的 `LOCAL VALID/FRAMES`；这两个本地计数在恢复已有会话时从
`0/0` 开始，不与服务器已存样本数混合。按
`health_flags.bit0 + 20..4000 mm + 2.5 s 新鲜度` 判定的超声质量。无回波、
越界或本地发布过期均会 fail-closed 显示 `NO ECHO/STALE`，不会把旧距离伪装成
有效数据。底部同时显示 `rise_rate_mm_s`、最近一次服务器上传的 HTTP 状态、
本地帧序号和 `UPLOAD ACK` 成功/失败次数；超过 2.5 秒没有成功确认会显示
`ACK DELAYED`。HTTP 2xx 只更新 ACK，不把尚未由会话接口重新确认的本地帧
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
所有 HTTP 工作仍在 Core 0 网络任务，OpenMV UART、超声和本地报警不被阻塞。

触控区域（800x480，左上为原点）：

- 风险页：`MODELS=(450,15,110,38)`、`WEATHER=(574,15,92,38)`、
  `WIFI=(680,15,92,38)`；
- 模型页：`BACK=(28,15,110,38)`、`COLLECTION=(602,15,170,38)`；
- 模型卡：`(28,96,236,286)`、`(280,96,236,286)`、
  `(532,96,236,286)`；
- 采集页：`BACK=(28,15,110,38)`、`START/STOP=(602,15,170,38)`。

## 主机单元测试

协议、CTL 安全映射、校验、环形缓冲和超长帧丢弃可以不接硬件测试：

```powershell
pio test -e native
```

模型目录和会话响应的固定大小解析、未知状态/过量模型拒绝、选中模型一致性、
Start/Stop 二次确认动作、超声质量失效策略和触控区域边界在
`test/test_model_control`；模型/组合风险隔离、fallback/stale/25 秒超时的
fail-safe 映射在 `test/test_openmv_control`。没有主机 `gcc/g++` 时可至少用
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
the network task, so local sensing and deterministic alarm logic keep running.
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
local sensing and alarm logic continue without waiting for that request.

已在实际 ESP32-S3 N16R8 + 800x480 触摸屏上验证扫描、板上密码输入、
获取 IP 后保存、Wi-Fi/天气服务恢复。单板迁移仍需在安装 ECHO 电平转换器后完成
真机测距、OpenMV 直连、HTTP 超时期间持续采集，以及长时间稳定性验收。
