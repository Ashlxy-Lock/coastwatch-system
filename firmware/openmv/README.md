# OpenMV 视觉节点

这是海岸预警系统第一阶段面向上板验证的基线，目标是先验证：

1. OpenMV 能稳定获取图像；
2. 固件内置 `person_detect.tflite` 能判断画面内是否有人；
3. 整个摄像机画面作为警戒区域，有人时稳定输出 `in_zone=1`；
4. VIS 帧格式、序号和 XOR 校验正确；
5. 完整安全状态慢闪绿色板载 LED；模型或本地水位进入有效警戒后先闪黄灯，
   稳定检测到人后切换为快闪红灯。

当前已确认板型为 **OpenMV4P-H7 / STM32H743**（这里指 OpenMV 板载 MCU，
不是已退役的外置控制板），固件为 OpenMV v4.8.1
（MicroPython v1.26.0-77）。

## 重要边界

- 当前实现是全画面人体二分类，只回答摄像机范围内有没有人。
- 它不会识别这个人是谁，也不输出人体边界框或真实坐标。
- 整个画面被定义为警戒区域，因此检测稳定后 `person=1` 与
  `in_zone=1` 同时成立。
- VIS 中的 `score` 是模型的人体类别分数（0–100）；由于没有定位结果，
  `cx=0`、`cy=0` 明确表示坐标不可用。
- 模型来自固件 ROM：`/rom/person_detect.tflite`，无需复制额外模型文件。

## 文件

```text
main.py             摄像头、检测循环、调试显示和发送节流
config.py           模式、ROI、阈值、串口和去抖参数
vision_detector.py  全画面人体分类、Haar 和颜色标记后端
protocol.py         VIS/CTL 编解码、严格校验和序号回绕
control.py          有界串口接收、CTL状态机和非阻塞LED闪烁
```

## 第一次上板

1. 在 OpenMV IDE 中连接摄像头；如提示升级固件，先取消并记录当前版本。
2. 先单独打开并运行 `board_probe.py`，把终端中的
   `BOARD_PROBE_SYSTEM` 和 `BOARD_PROBE_FRAME` 两行发回来。
3. 单独验证摄像头时可保持 `UART_ENABLED = False`；接入 ESP32 联调时改为
   `UART_ENABLED = True`。当前资料包已切到 ESP32 全双工联调配置。
4. 将 `main.py`、`config.py`、`vision_detector.py`、`protocol.py`、`control.py`
   复制到 OpenMV 存储盘根目录。
5. 打开并运行 `main.py`。
6. IDE 帧缓冲区应显示：
   - 无人时红色全画面边框；
   - 检测到人后绿色全画面边框；
   - 左上角的 `PERSON` 和 `SCORE`。
7. 人员进入画面并连续确认 3 帧后，串行终端应出现类似：

```text
$VIS,17,1,90,0,0,1*43
```

没有检测到目标时，终端会持续输出规范化的全零帧：

```text
$VIS,18,0,0,0,0,0*75
```

## 标定

- 如果画面上下或左右颠倒，修改 `VERTICAL_FLIP`、`HORIZONTAL_MIRROR`。
- 当前整幅画面就是警戒区，不需要配置几何 ROI。
- 进入阈值为 `0.65`，退出阈值为 `0.58`；连续 3 帧确认有人或无人。
  较高的退出阈值用于减少空背景分数在 0.50 附近波动造成的状态粘连。
- 实测单次人体分类约 20 FPS。VIS 固定保持 10Hz；CTL 中
  `person_enable=0` 时，人体模型每 500ms 做一次真实推理，两次推理之间的
  VIS 复用最近一次真实结果，不伪造 `person=0`。
- 模型只判断“至少有一人”，多人仍统一输出 `person=1`。

## 颜色标记备选模式

如果 Haar 在板上性能不足，可先把：

```python
VISION_MODE = MODE_COLOR_MARKER_DEMO
```

再使用 OpenMV IDE 的 LAB 阈值编辑器校准 `COLOR_THRESHOLD`。这个模式只能称为“色卡演示目标”，不得对外描述为人员识别。

## UART

当前已确认 OpenMV4P-H7、固件 v4.8.1 和标准 UART3 引脚，联调配置为：

```python
UART_ENABLED = True
UART_ID = 3
```

UART 为 115200 8N1。调试日志只走 IDE/USB 控制台，VIS/CTL 使用硬件 UART。
MV4/OpenMV H7 Plus 的标准 UART3 映射为 `P4=TX`、`P5=RX`：

```text
OpenMV P4 / UART3 TX  -> ESP32 GPIO8  / UART1 RX   (VIS)
OpenMV P5 / UART3 RX  <- ESP32 GPIO14 / UART1 TX   (CTL)
OpenMV GND             -- ESP32 GND                (共地)
```

两端都是 3.3V UART，不需要分压。原有只连接 P4 的单向接线无法接收模型危险
状态；启用本功能前必须补接 `ESP32 GPIO14 -> OpenMV P5`。

## ESP32 到 OpenMV 的 CTL 帧

规范帧为：

```text
$CTL,<seq>,<danger>,<person_enable>,<environmental_level>*<XOR>\r\n
```

CTL 当前语义版本为 **1.1**，帧结构与旧版兼容。例如模型 warning、完整
safe、advisory 全速监测和“模型 safe、但本地水位 warning/critical”分别为：

```text
$CTL,17,1,1,2*6F
$CTL,18,0,0,0*62
$CTL,19,0,1,1*63
$CTL,20,1,1,0*69
```

- `seq`：0–65535，每帧递增，允许从 65535 回绕到 0；重复和倒退帧拒绝；
- `danger`：ESP32 计算出的有效警戒。满足以下任一条件时为 1：可信且 fresh
  的服务器模型 `environmental_level` 为 2/3；或者健康、实时的本地水位
  规则报警为 2/3。本地 `alarm=4` 是传感器故障，不得设置 `danger=1`；
- `person_enable`：1 表示全速检测。有效警戒时必须为 1，服务器结果未知、
  本地故障、降级或其他 fail-safe 状态也可在 `danger=0` 时设为 1；
- `environmental_level`：服务器环境模型等级 0–3；
- 校验：与 VIS 相同，对 `$` 与 `*` 之间的 ASCII 字符逐字节 XOR，使用两个
  大写十六进制字符。

CTL 接收是严格的：必须使用 CRLF、规范十进制、正确字段数和大写校验；
`danger=1` 必须同时满足 `person_enable=1`；任何
`environmental_level>=2` 的帧也必须设置 `person_enable=1`。允许
`danger=1,environmental_level=0/1`，它明确表示警戒来源是健康的本地水位
规则，而不是伪装成模型 warning。
UART 缓冲上限为 64 字节，超长行丢弃到下一处 LF 后重新同步。

版本 1.1 没有增加字段：旧 ESP32 只根据模型设置 `danger` 的帧仍全部合法；
新版把 `danger` 扩展成上述有效警戒。代价是 OpenMV 只能知道“已有有效警戒”，
不能从 CTL 还原本地报警究竟是 2 还是 3。`environmental_level` 只保留模型
诊断含义。ESP32 只有在模型 fresh/可信、数据质量正常、未降级、本地水位
`alarm=0` 时才可发送 `0,0,0`；这是绿灯完整安全门的生产端约束。

## 检测与板载 LED 状态机

| CTL状态 | 人体推理 | VIS | 显式灯态 |
|---|---:|---:|---|
| 刚启动、3秒超时、坏帧或重放帧 | 全速 fail-safe | 10Hz | OFF |
| fresh 完整安全 `0,0,0` | 每500ms一次 | 10Hz | 绿灯慢闪 |
| advisory、fallback、本地fault=4或其他fail-safe | 全速 | 10Hz | OFF |
| fresh 有效警戒，尚未稳定检测到人 | 全速 | 10Hz | 黄灯闪烁 |
| fresh 有效警戒，稳定检测到人 | 全速 | 10Hz | 红灯快闪 |

红灯使用 OpenMV H7 Plus 的 `pyb.LED(1)`，200ms 亮/200ms 灭；绿灯使用
`pyb.LED(2)`，800ms 亮/800ms 灭。黄灯是明确的第三状态，使用同一个
400ms 相位同时驱动 LED(1)+LED(2)，不是两个独立条件偶然叠加。任何灯态
切换都先强制两灯全灭，再开启新颜色。CTL 失效、fallback/fail-safe、脚本
致命错误以及本地 fault=4 都保持 OFF；LED 只是状态指示，不是独立判断器。

ESP32 端继续使用 VIS 超时判定视觉节点健康：

- 上电等待第一个 VIS 帧：建议 5 秒；
- 已正常通信后丢失 VIS：默认 1 秒进入传感器故障；若实测最差帧率不足 3FPS，需按最慢三个帧周期放宽。
