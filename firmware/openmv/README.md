# OpenMV 视觉节点

这是海岸预警系统第一阶段面向上板验证的基线，目标是先验证：

1. OpenMV 能稳定获取图像；
2. 固件内置 `person_detect.tflite` 能判断画面内是否有人；
3. 整个摄像机画面作为警戒区域，有人时稳定输出 `in_zone=1`；
4. VIS 帧格式、序号和 XOR 校验正确。

当前已确认板型为 **OpenMV4P-H7 / STM32H743**，固件为 OpenMV v4.8.1
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
protocol.py         VIS 编码、校验和序号回绕
```

## 第一次上板

1. 在 OpenMV IDE 中连接摄像头；如提示升级固件，先取消并记录当前版本。
2. 先单独打开并运行 `board_probe.py`，把终端中的
   `BOARD_PROBE_SYSTEM` 和 `BOARD_PROBE_FRAME` 两行发回来。
3. 单独验证摄像头时可保持 `UART_ENABLED = False`；接入 STM32 联调时改为
   `UART_ENABLED = True`。当前资料包已切到 STM32 联调配置。
4. 将 `main.py`、`config.py`、`vision_detector.py`、`protocol.py`
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
- 实测单次人体分类约 20 FPS；默认每 100ms 最多输出一次 VIS，
  即发送上限为 10Hz。
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

UART 为 115200 8N1。调试日志只走 IDE/USB 控制台，VIS 才写硬件 UART。
MV4/OpenMV H7 Plus 的标准 UART3 映射为 `P4=TX`、`P5=RX`。

STM32 端以后应使用两段超时：

- 上电等待第一个 VIS 帧：建议 5 秒；
- 已正常通信后丢失 VIS：默认 1 秒进入传感器故障；若实测最差帧率不足 3FPS，需按最慢三个帧周期放宽。
