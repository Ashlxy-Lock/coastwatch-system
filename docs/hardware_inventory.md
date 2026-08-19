# 硬件清单

更新时间：2026-08-19

| 模块 | 当前信息 | 状态 |
|---|---|---|
| OpenMV | OpenMV4P-H7 / H7 Plus | 已确认并连接 |
| OpenMV 固件 | OpenMV v4.8.1 / MicroPython v1.26.0-77 | 已确认；使用 ROM 内置人员模型 |
| 摄像头 | QVGA RGB565，全画面人员存在分类 | 实物验证通过；不是身份识别或人体定位 |
| OpenMV UART | UART3：P4/TX → ESP32 GPIO8/RX；P5/RX ← ESP32 GPIO14/TX；115200 8N1 | VIS 与 CTL 双向链路；两根信号线均为现行必接 |
| ESP32 | ESP32-S3-WROOM-1-N16R8，16 MB Flash / 8 MB PSRAM，CH343 | 芯片、Flash、PSRAM、烧录均已实测 |
| LCD / 触摸 | TK043F1509 800×480；触摸 I2C SDA=GPIO20、SCL=GPIO13；GPIO12 为触摸 INT | 显示、触摸与板上操作已接入 |
| 超声波 | HC-SR04：TRIG=GPIO10；ECHO 经 2×220Ω 上臂、3×220Ω 下臂分压后接 GPIO40 | 5 V 供电；约 3.0 V ECHO；单板测距与上传已验证 |
| ESP32 板载 RGB LED | WS2812B，GPIO38 | 资料已确认；当前业务状态灯位于 OpenMV |
| OpenMV 板载 LED | LED(1) 红、LED(2) 绿；组合为黄 | 安全绿闪、警戒黄闪、警戒且有人红闪 |
| 蜂鸣器、继电器、外接灯带 | 当前没有 | 不得描述为已完成现场声光报警 |

## 必须遵守的硬件边界

- ESP32-S3 GPIO 不耐受 5 V。HC-SR04 ECHO 必须经过已核验的分压或电平转换，
  禁止直接接 GPIO40 或其他 ESP32 引脚。
- OpenMV P4 与 ESP32 GPIO8、OpenMV P5 与 ESP32 GPIO14 均为 3.3 V UART，
  不需要分压，但两块板必须共地。
- GPIO42 已由 LCD D/C 占用，GPIO12 已由触摸 INT 占用；GPIO35/36/37 用于
  N16R8 的 Octal PSRAM，均不得作为替代 ECHO 或 UART 引脚。
- 超声波使用稳定 5 V，ESP32 与 OpenMV 使用各自正常供电入口；不得通过 GPIO
  互相供电，也不得并接多个电源的 5 V 输出。
- Wi-Fi、设备令牌和隧道凭据只保存在本地忽略文件中，不写入仓库。

## 当前完成状态

ESP32 直接接收 OpenMV `VIS`、采集超声、建立稳定基准、计算相对水位与变化率、
执行本地规则并生成遥测。它同时通过 GPIO14 返回 `CTL`，使 OpenMV 在完整安全、
警戒、警戒且有人以及 fail-safe 状态之间切换检测频率和板载 LED。服务器上传、
LCD/触摸、采集会话和模型研究功能均使用同一份 ESP32 本地遥测。
