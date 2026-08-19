# CoastWatch 海岸智能预警系统

> **2026-08-18 架构更新：** 主控已切换为 OpenMV + ESP32-S3，STM32 进入
> 回滚/对照状态。ESP32 直接完成超声、基线/滤波、本地规则、LCD/触摸和联网。
> HC-SR04 ECHO 已通过 5 个 220Ω 电阻分压后接入 GPIO40；单板固件已烧录并验证
> 超声、OpenMV、LCD、Wi-Fi、服务器上传和研究风险返回。权威迁移说明见
> `docs/ESP32_SINGLE_BOARD_MIGRATION.md`；本文后续 STM32 章节保留为原始设计与
> 可回滚实现记录。

> 英文标题：**An STM32-Based Intelligent Coastal Safety Warning System Using OpenMV and Ultrasonic Sensing**

## 0. 给 Codex 的执行要求

请按本 README 建立一个可运行的多端工程，目标是在 **5 天内完成可演示原型**。

核心原则：

- **STM32 负责本地判断和声光报警。**
- **OpenMV 负责识别游客是否进入危险区。**
- **ESP32 只负责 Wi-Fi 与服务器通信。**
- 网络断开、ESP32 故障或服务器离线时，STM32 仍必须正常报警。
- 优先完成 MVP，不要先做人脸身份识别、云端 AI、手机 App 或复杂多节点系统。
- 所有硬件型号、引脚、触发电平和阈值必须集中配置，不能散落在代码中。

建议技术栈：

- STM32：STM32CubeIDE + HAL，裸机非阻塞状态机
- OpenMV：MicroPython
- ESP32：PlatformIO + Arduino Framework
- 服务器：Python 3.11+、FastAPI、SQLite
- 页面：简单 HTML/CSS/JavaScript

---

## 1. 项目目标

系统用于海滩、湖边、河岸或水上景区的危险区域监测。

主要功能：

1. OpenMV 判断游客是否进入预先划定的危险区域。
2. 超声波传感器检测水面距离、水位变化量和上升速度。
3. STM32 融合视觉与水位信息，决定报警等级。
4. 蜂鸣器或警号发出声音报警。
5. 彩灯带显示安全、警告、严重危险和设备故障状态。
6. ESP32 通过 Wi-Fi 将设备状态发送到服务器。
7. 服务器保存遥测数据，并在网页上显示水位、人员、报警等级和设备健康状态。
8. ESP32 可从服务器获取天气、水温或潮汐信息；没有可靠数据源时必须标记为手动值或模拟值。
9. 本地报警不依赖网络。

### 非目标

MVP 暂不实现：

- 人脸身份识别；
- 海啸、台风或风暴潮的专业预测；
- 直接上传 OpenMV 视频流；
- 依赖云端计算才能报警；
- 手机原生 App；
- 真实公共安全机构自动报警。

本项目是低成本课程原型，不能声称可替代专业救生和海洋监测系统。

---

## 2. 总体架构

```text
OpenMV ──UART──┐
               │
超声波 ────────┤
               ▼
          STM32F103
       本地融合与状态机
          │         │
          │         └──UART──▶ ESP32 ──Wi-Fi──▶ FastAPI服务器
          │                                      │
          ├──▶ 蜂鸣器/警号                       └──▶ Web仪表盘
          └──▶ 彩色灯带
```

分工：

```text
OpenMV：看
STM32：判断与现场控制
ESP32：联网
服务器：记录和展示
```

---

## 3. 当前硬件和待确认项目

| 模块 | 当前方案 |
|---|---|
| 主控 | STM32F103ZET6 |
| 视觉 | OpenMV MV4 H7 Plus |
| 联网 | 资料对应 ESP32-S3-WROOM-1-N16R8（实物丝印待复核） |
| 水位检测 | 防水超声波优先，具体型号待确认 |
| 声音报警 | 5V 蜂鸣器或 12V 警号 |
| 驱动 | 5V 光耦隔离机械继电器模块，需支持 3.3V 触发 |
| 灯光 | WS2812B 优先；实际型号待确认 |
| 电池 | 已有 12.6V 电池，容量和 BMS 状态待确认 |
| 降压 | 12.6V 转 5V，建议额定 5A |
| 调试 | ST-Link V2、USB 转 TTL |

开工前在 `docs/hardware_inventory.md` 填写：

```text
OpenMV 型号：
ESP32 型号：
超声波型号：
灯带类型与长度：
蜂鸣器/警号额定电压和电流：
继电器触发方式：高电平 / 低电平
电池容量：
降压模块最大输出：
```

未确认信息必须做成配置项，禁止静默假设。

---

## 4. 建议引脚分配

以下按当前 STM32F103ZET6 扩展板和已加入的 ESP32-S3 资料分配，最终仍以实物接线为准。

> 当前 F103ZET6 扩展板的排针未引出 PA9/PA10 和 PB6/PB7，OpenMV 首轮联调
> 改用 USART3：`PB10=TX`、`PB11=RX`。因此实际接线为
> `OpenMV P4/TX → STM32 PB11/RX`。
>
> USART3 已被 OpenMV 占用，ESP32 不再复用 USART3。ESP32 网关改用
> STM32 USART2：`PA2=TX`、`PA3=RX`，对接 ESP32-S3 UART1：
> `GPIO8=RX`、`GPIO14=TX`。GPIO12 已由当前屏幕转接板用作触摸中断，
> 禁止再接 STM32 TX。

| 功能 | 外设 | 建议引脚 |
|---|---|---|
| OpenMV 通信 | USART3 | PB10 TX、PB11 RX |
| ESP32 通信 | USART2 | PA2 TX、PA3 RX |
| 超声波测距（当前 TRIG/ECHO） | GPIO + EXTI/TIM2 | PC10 TRIG、PC11 ECHO |
| UART 防水超声波（未来替代） | UART4 | PC10 TX、PC11 RX；与当前方案二选一 |
| 继电器控制 | GPIO | PB0 |
| WS2812B 数据 | TIM1_CH1/GPIO | PA8 |
| 状态 LED | GPIO | PC13 |
| ST-Link | SWD | PA13、PA14 |

UART 交叉连接：

```text
STM32 TX → 对方 RX
STM32 RX ← 对方 TX
STM32 GND ↔ 对方 GND
```

STM32 和 ESP32 都是 3.3V 逻辑，UART 通常可直连。禁止向 ESP32 GPIO 输入 5V。

---

## 5. 供电设计

```text
12.6V 三串锂电池
        │
     保险丝
        │
      总开关
        │
        ├────────▶ 12V警号（若使用）
        │
        └▶ 12.6V→5V / 5A DC-DC
                    ├▶ STM32 5V输入
                    ├▶ ESP32 5V/VIN
                    ├▶ OpenMV 5V/VIN
                    ├▶ 超声波
                    ├▶ 5V继电器模块
                    └▶ 5V灯带
```

要求：

- 所有低压模块共地。
- 使用星形供电，灯带和 ESP32 不要经过 STM32 板供电。
- 不得从 STM32 的 3.3V 引脚给 ESP32 供电。
- 灯带入口并联约 1000µF/10V 电解电容。
- 每块数字板附近放置 0.1µF 去耦电容。
- 锂电池必须带 BMS，并使用匹配的 12.6V 充电器。
- 首次联调优先使用稳定 5V 电源。

---

## 6. 报警输出

### 6.1 光耦继电器

继电器用于控制蜂鸣器或警号的供电。

```text
控制侧：
VCC → 5V
GND → GND
IN  → STM32 GPIO

负载侧：
电源正极 → COM
NO       → 蜂鸣器/警号正极
负载负极 → 电源负极
```

使用 NO 常开端。

注意：

- 机械继电器只适合慢速通断。
- 报警节拍最短建议不低于 200ms。
- 不能用继电器做 PWM 调光。
- 需确认模块支持 3.3V 输入。
- 高/低电平触发方式放在配置文件中。

### 6.2 灯带

WS2812B 方案：

- 独立 5V 供电；
- STM32 只输出数据；
- 建议加 74AHCT125 电平转换；
- 数据线串联 330–470Ω；
- 继电器不要接在 WS2812B 数据线上。

若是普通单色灯带，继电器只能控制整体亮灭。  
若是普通 12V RGB 灯带，需要 3 路逻辑电平 MOS 管，不建议用机械继电器调色。

---

## 7. 报警状态机

```c
typedef enum {
    ALARM_SAFE = 0,
    ALARM_ADVISORY = 1,
    ALARM_WARNING = 2,
    ALARM_CRITICAL = 3,
    ALARM_FAULT = 4
} alarm_state_t;
```

| 状态 | 条件示例 | 灯光 | 声音 |
|---|---|---|---|
| SAFE | 水位正常、无人进入 | 绿色常亮 | 不响 |
| ADVISORY | 有人靠近或水位略高 | 黄色慢闪 | 单次短鸣 |
| WARNING | 人进入危险区或水位快速上升 | 橙色闪烁 | 慢速间歇 |
| CRITICAL | 人进入危险区且水位危险 | 红色快闪 | 连续/快速间歇 |
| FAULT | OpenMV 或超声波离线 | 紫色闪烁 | 三短一长 |

建议逻辑：

```c
if (openmv_fault || ultrasonic_fault) {
    state = ALARM_FAULT;
} else if (person_in_zone && water_danger) {
    state = ALARM_CRITICAL;
} else if (person_in_zone || water_rising_fast || water_warning) {
    state = ALARM_WARNING;
} else if (person_near_zone || water_advisory) {
    state = ALARM_ADVISORY;
} else {
    state = ALARM_SAFE;
}
```

网络离线不是安全传感器故障，应记录为独立通信状态，本地报警继续工作。

---

## 8. 超声波数据处理

统一接口：

```c
typedef struct {
    bool valid;
    uint16_t distance_mm;
    uint32_t timestamp_ms;
} ultrasonic_sample_t;

bool ultrasonic_init(void);
bool ultrasonic_poll(ultrasonic_sample_t *sample);
```

当前实物使用 TRIG/ECHO 驱动；UART 防水传感器仅保留为未来二选一替代：

```text
ultrasonic_a02yyuw.c   // UART 防水超声波
ultrasonic_hcsr04.c    // TRIG/ECHO 备选
```

基准与计算：

```text
baseline_distance_mm = 平静水面到传感器的距离
water_rise_mm = baseline_distance_mm - filtered_distance_mm
rise_rate_mm_s = 水位变化量 / 时间
```

滤波：

1. 5 点中值滤波；
2. 指数平滑；
3. 多次确认；
4. 阈值滞回。

```c
filtered = 0.8f * previous_filtered + 0.2f * median;
```

最近 5 次中至少 4 次超过阈值才触发。

桌面演示默认值，必须可配置：

```c
#define WATER_ADVISORY_MM        50
#define WATER_WARNING_MM        100
#define WATER_DANGER_MM         180
#define RISE_RATE_WARNING_MM_S  25
```

---

## 9. OpenMV 视觉方案

5 天 MVP 优先级：

1. 指定颜色目标进入危险区；
2. 帧差或运动区域检测；
3. 轻量化人员检测模型。

不要先做人脸身份识别。

OpenMV 中设置：

```text
MONITOR_ROI：监测区域
DANGER_ROI：危险区域
```

当前 H7 Plus MVP 将整个摄像机画面定义为危险区域，并使用固件内置
`person_detect.tflite` 做人体存在分类。检测到人即
`person=1, in_zone=1`；该模型不提供人体定位，因此 `cx=0, cy=0`
表示坐标不可用。后续若需要真实越界位置，再替换为 FOMO 等人体定位模型。

输出字段：

- 是否发现目标；
- 置信度或面积分数；
- 目标中心坐标；
- 是否进入危险区；
- 帧序号。

### OpenMV → STM32 协议

```text
$VIS,<seq>,<person>,<confidence>,<cx>,<cy>,<in_zone>*<checksum>

```

示例：

```text
$VIS,17,1,90,0,0,1*43

```

校验使用 `$` 后到 `*` 前字符的 XOR，输出两位十六进制。

建议发送 5–10Hz。STM32 超过 1000ms 未收到有效 VIS 帧，则判定 OpenMV 离线。

---

## 10. STM32 与 ESP32 协议

STM32 每 500ms 发送：

```text
$TEL,<seq>,<uptime_ms>,<distance_mm>,<water_rise_mm>,<rise_rate_mm_s>,<person>,<alarm>,<health>*<checksum>

```

示例：

```text
$TEL,42,123456,815,126,21,1,3,7*63

```

健康位：

```c
#define HEALTH_ULTRASONIC_OK (1u << 0)
#define HEALTH_OPENMV_OK     (1u << 1)
#define HEALTH_POWER_OK      (1u << 2)
#define HEALTH_NETWORK_OK    (1u << 3)
```

ESP32 每秒返回：

```text
$NET,<wifi>,<server>,<rssi>,<unix_time>*<checksum>

```

示例：

```text
$NET,1,1,-55,1785398400*7F

```

协议要求：

- UART：115200、8N1；
- 单帧不超过 160 字节；
- 接收使用环形缓冲区；
- 按换行取完整帧；
- 校验失败直接丢弃；
- 禁止在 UART 回调中执行业务逻辑。

---

## 11. Wi-Fi 与服务器

MVP 路径：

```text
STM32 → UART → ESP32 → Wi-Fi → FastAPI → 浏览器
```

服务器可运行在同一 Wi-Fi 下的笔记本电脑上，不需要购买云服务器。

ESP32 应实现：

1. 连接 2.4GHz Wi-Fi；
2. 自动重连；
3. 解析 TEL；
4. HTTP POST 上传遥测；
5. 定期获取环境信息；
6. 返回 NET 状态；
7. 上传失败不影响 UART 接收和本地报警。

### API

#### 上传遥测

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

#### 获取环境信息

```http
GET /api/v1/environment?device_id=COAST_01
```

```json
{
  "weather": "Cloudy",
  "air_temperature_c": 29.2,
  "water_temperature_c": 26.4,
  "tide_status": "Rising",
  "source": "demo",
  "updated_at": "2026-07-30T08:00:00Z"
}
```

若无可靠实时来源，`source` 必须为 `demo` 或 `manual`。

#### 健康检查

```http
GET /api/v1/health
```

上传频率：

```text
普通遥测：2秒
报警变化：立即
设备心跳：30秒
环境信息：60秒
```

重试退避：

```text
5s → 10s → 30s → 60s
```

禁止无限阻塞式重连。

---

## 12. Web 仪表盘

MVP 显示：

- 设备 ID；
- 在线/离线；
- Wi-Fi RSSI；
- 水面距离；
- 水位上升量；
- 水位变化速度；
- 是否检测到游客；
- 报警等级；
- OpenMV、超声波状态；
- 最近更新时间；
- 最近 50 条报警事件；
- 简单水位折线图；
- 天气、水温和潮汐信息及其数据来源。

颜色：

```text
SAFE      绿色
ADVISORY  黄色
WARNING   橙色
CRITICAL  红色
FAULT     紫色
```

---

## 13. 推荐仓库结构

```text
coastal-warning-system/
├── README.md
├── .gitignore
├── docs/
│   ├── hardware_inventory.md
│   ├── wiring.md
│   ├── protocol.md
│   ├── calibration.md
│   └── test_report.md
├── firmware/
│   ├── stm32/
│   │   └── Core/
│   │       ├── Inc/
│   │       └── Src/
│   ├── esp32/
│   │   ├── platformio.ini
│   │   ├── include/
│   │   └── src/
│   └── openmv/
│       ├── main.py
│       ├── config.py
│       ├── vision_detector.py
│       └── protocol.py
├── server/
│   ├── requirements.txt
│   ├── .env.example
│   ├── app/
│   │   ├── main.py
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   └── static/
│   └── tests/
├── tools/
│   ├── serial_simulator.py
│   └── protocol_tester.py
└── tests/
    └── protocol_vectors.json
```

STM32 模块建议：

```text
app_config
app_scheduler
alarm_manager
protocol
ring_buffer
sensor_fusion
ultrasonic
openmv_link
esp32_link
ws2812
```

STM32 要求：

- 不使用长时间 `HAL_Delay()`；
- 启用 IWDG；
- 上电默认关闭继电器；
- 解析失败不覆盖上一条有效数据；
- 所有阈值集中在 `app_config.h`。

ESP32 配置：

```cpp
#define WIFI_SSID "YOUR_WIFI"
#define WIFI_PASSWORD "YOUR_PASSWORD"
#define SERVER_BASE_URL "http://192.168.1.100:8000"
#define DEVICE_ID "COAST_01"
```

真实密钥文件必须加入 `.gitignore`。

---

## 14. 五天计划与人员分工

### Day 1：硬件确认与基础工程

- QIN YIQING：总体协调、接口确认、仓库管理。
- ZHANG SHOUTONG：STM32 工程和三路 UART。
- FU XUANYU：OpenMV ROI 和固定 VIS 测试帧。
- XIA ZIHAO：ESP32 连 Wi-Fi、FastAPI 空项目。
- SUN YINAN：电源、继电器、蜂鸣器、灯带接线。

### Day 2：模块独立运行

- STM32：超声波、滤波、状态机。
- OpenMV：目标检测和危险区判断。
- ESP32：TEL 帧解析。
- 服务器：遥测接口和 SQLite。
- 输出：五级灯光和声音状态。

### Day 3：联网闭环

- STM32 发 TEL；
- ESP32 上传服务器；
- 页面显示数据；
- ESP32 拉取环境信息；
- ESP32 返回 NET。

### Day 4：完整集成

- 全部模块同时运行；
- 调整阈值和 ROI；
- 测试断网、拔线、错误帧和重启；
- 完成水箱或模拟危险区装置。

### Day 5：验收与展示

- 完成测试报告；
- 录制演示；
- 保存日志和截图；
- 完成汇报材料；
- 展示“断网仍可本地报警”。

---

## 15. 验收测试

1. 水位正常且无人时显示 SAFE。
2. 人进入危险区后 1 秒内触发 WARNING。
3. 模拟水位快速上升后触发 WARNING。
4. 人员和危险水位同时存在时触发 CRITICAL。
5. 拔掉 OpenMV 后进入 FAULT。
6. 超声波持续无有效数据后进入 FAULT。
7. 关闭 Wi-Fi 或拔掉 ESP32，本地报警继续工作。
8. Wi-Fi 恢复后 ESP32 自动重连。
9. 服务器记录遥测和报警事件。
10. 页面显示最新设备状态。
11. 三块板连续运行 30 分钟不死机。
12. 重新上电时继电器不误触发。
13. 损坏串口帧不会改变报警状态。
14. 灯带高亮、ESP32 发射时系统不重启。
15. 天气和水温明确标记真实、手动或模拟来源。

---

## 16. Codex 第一阶段交付物

先生成可编译、可模拟的骨架：

1. `firmware/stm32`：三 UART、协议解析、状态机、继电器控制、模拟传感器模式。
2. `firmware/openmv`：可配置 VIS 测试帧和 ROI 检测框架。
3. `firmware/esp32`：接收 TEL、连接 Wi-Fi、HTTP POST、返回 NET。
4. `server`：FastAPI、SQLite、遥测接口、环境接口、简单网页。
5. `tools/serial_simulator.py`：模拟 STM32 遥测。
6. `docs/wiring.md`：接线说明。
7. 每个子工程提供独立运行步骤。
8. 未确认硬件通过配置项控制。
9. 先支持模拟模式，再接真实硬件。
10. 给出最小端到端演示流程。

---

## 17. 禁止事项

- 不得把网络上传写进 STM32 的阻塞主循环。
- 不得等待 Wi-Fi 成功后才执行本地报警。
- 不得把整张 OpenMV 图像传给 STM32。
- 不得用机械继电器做高速 PWM。
- 不得从 STM32 3.3V 引脚给 ESP32 供电。
- 不得把 Wi-Fi 密码提交到 Git。
- 不得把模拟天气或水温写成真实数据。
- 不得在硬件型号未确认时静默假设引脚。
- 不得用长时间 `delay()` 或 `HAL_Delay()` 阻塞核心任务。

---

## 18. 一句话设计原则

> **断网不失控，联网增功能；OpenMV 负责看，STM32 负责判断，ESP32 负责通信。**
