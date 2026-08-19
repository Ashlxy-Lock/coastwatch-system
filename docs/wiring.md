# 接线：ESP32-S3 + OpenMV + HC-SR04

更新时间：2026-08-19

所有改线必须在 ESP32、OpenMV 和超声波全部断电时进行。UART 使用
`115200, 8N1`、3.3 V TTL；所有模块必须共地。

## 完整接线

```text
OpenMV P4 / UART3 TX ─────────────▶ ESP32 GPIO8  / UART1 RX   (VIS)
OpenMV P5 / UART3 RX ◀───────────── ESP32 GPIO14 / UART1 TX   (CTL)
OpenMV GND           ─────────────── ESP32 GND

HC-SR04 VCC          ─────────────── 稳定 5V
HC-SR04 GND          ─────────────── 公共 GND
HC-SR04 TRIG         ◀────────────── ESP32 GPIO10
HC-SR04 ECHO ──220Ω──220Ω──┬──────▶ ESP32 GPIO40
                           │
                         220Ω
                           │
                         220Ω
                           │
                         220Ω
                           │
                          GND
```

OpenMV 的 P4/GPIO8 上行和 P5/GPIO14 下行都是现行运行路径的必接线。只接 P4
虽然仍可能上传 `VIS`，但 OpenMV 无法接收 `CTL`，因而不能按模型/本地警戒切换
检测频率或正确显示绿、黄、红状态灯。

## ECHO 分压

HC-SR04 ECHO 约为 5 V，而 ESP32-S3 没有任何耐 5 V GPIO。现行分压由：

- ECHO 到 GPIO40 的上臂：两只 220Ω 串联，共 440Ω；
- GPIO40 到 GND 的下臂：三只 220Ω 串联，共 660Ω。

组成。理想输出约为 `5V × 660 / (440 + 660) = 3.0V`。上电前应使用万用表确认
分压节点不超过 3.3 V，确认后才允许把节点接到 GPIO40。单独串联一个电阻、
改用 ADC attenuation、换到其他 GPIO 或使用继电器都不能代替正确的电平转换。

## 已占用或禁止复用的 ESP32 引脚

- GPIO42：LCD D/C；
- GPIO12：触摸 INT；
- GPIO20 / GPIO13：触摸 I2C SDA / SCL；
- GPIO35 / GPIO36 / GPIO37：N16R8 Octal PSRAM；
- GPIO38：板载 WS2812B；
- GPIO43 / GPIO44：下载与调试串口。

不得把 ECHO、OpenMV UART 或临时跳线移到这些引脚。

## 供电

- ESP32 与 OpenMV 使用各自正常 USB/电源入口；HC-SR04 使用稳定 5 V。
- ESP32、OpenMV、HC-SR04 和外部 5 V 电源负极必须共地。
- 不得从 GPIO 给其他模块供电，不得把 5 V 接到任何 ESP32 或 OpenMV GPIO。
- 不要并接两个独立电源的 5 V 正极；只连接经过确认的公共地。

## 上电与联调顺序

1. 全部断电，先连接所有 GND。
2. 连接 OpenMV P4→GPIO8、GPIO14→OpenMV P5，两根信号线均不要省略。
3. 按图搭好超声分压；先不接 GPIO40，用万用表测量分压节点。
4. 确认节点不超过 3.3 V 后断电，再连接 GPIO40，并连接 TRIG→GPIO10。
5. 使用 `esp32s3-n16r8-singleboard` 环境烧录 ESP32；普通环境不会启用超声引脚。
6. 把 `main.py`、`config.py`、`control.py`、`protocol.py`、
   `vision_detector.py` 放到 OpenMV 存储根目录并启动 `main.py`。
7. 检查 ESP32 日志出现新鲜 OpenMV 帧、超声 `armed=1/healthy=1` 和本地遥测；
   再确认服务器 POST 成功、LCD 数值随目标距离变化。
8. 分别验证：完整安全时绿闪；警戒且无人时黄闪；警戒且检测到人时红闪；
   断网时本地超声、视觉健康检查和报警规则仍持续运行。
