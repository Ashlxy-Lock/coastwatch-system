# CoastWatch 当前系统架构

更新时间：2026-08-19

## 系统边界

CoastWatch 当前硬件由 **ESP32-S3 + OpenMV H7 Plus + HC-SR04** 组成，服务端为
FastAPI。ESP32-S3 是唯一的现场控制器：它直接采集超声波、接收 OpenMV 人员检测
结果、执行确定性本地规则、驱动 LCD/触摸界面，并通过 Wi-Fi 与服务器交换数据。

服务器模型属于研究与演示通道。它可以改变 OpenMV 的检测频率和状态灯提示，但
不能覆盖、降低或替代 ESP32 的本地报警等级。网络或服务器不可用时，超声采集、
视觉链路健康检查和本地规则仍持续运行。

## 组件职责

| 组件 | 职责 | 关键边界 |
|---|---|---|
| OpenMV H7 Plus | 全画面人员存在检测；发送 `VIS`；接收 `CTL`；显示绿/黄/红状态灯 | 不做身份识别，不负责最终报警决策 |
| ESP32-S3 | 超声采集、基准/滤波、水位变化与变化率、本地报警、LCD/触摸、Wi-Fi、数据上传 | 本地规则不等待网络，不接受服务器降级 |
| FastAPI server | 保存遥测与采集会话、提供环境数据和研究风险、管理数据标注与模型训练 | 模型输出仅作研究结果与检测模式输入 |
| Admin website | 身份认证、会话整理、标注、训练、评估与可视化 | 不直接控制传感器电气状态 |

## 数据流

```text
OpenMV camera
  └─ VIS (person/score/in_zone, 115200 8N1) ──▶ ESP32 UART1 RX / GPIO8

HC-SR04
  ├─ TRIG ◀─────────────────────────────────── ESP32 GPIO10
  └─ ECHO ── 5V→约3.0V电阻分压 ──────────────▶ ESP32 GPIO40

ESP32 local runtime
  ├─ 生成 TelemetryFrame ── HTTPS/HTTP POST ──▶ FastAPI
  ├─ 读取 environment/risk/models/session ────▶ FastAPI
  ├─ LCD/触摸显示与采集控制
  └─ CTL (danger/person_enable/model level) ──▶ OpenMV P5 / GPIO14
```

OpenMV 的 `VIS` 以 10 Hz 心跳发送。ESP32 超过 1 秒未收到合法帧时把视觉链路判为
故障。ESP32 每 500 ms 发送一次严格校验的 `CTL`；OpenMV 超过 3 秒未收到新鲜、
合法且序号递增的控制帧时进入全速 fail-safe 检测。

## 本地传感与报警

- 超声每 100 ms 调度一次，30 ms 未完成回波即超时；中断只记录回波边沿。
- 三个稳定样本建立基准，五点中值后使用 Q8 EMA 平滑。
- `water_rise_mm = baseline_distance_mm - filtered_distance_mm`。
- 1 秒没有有效回波会清除超声健康位；3 秒后清除基准并自动重建。
- 本地报警融合超声健康、视觉健康、人员状态、水位增量与上升速率。
- 服务器环境模型不会写入本地报警状态；网络健康位只用于遥测与显示。

## OpenMV 控制与状态灯

ESP32 与 OpenMV 使用校验帧双向通信：

```text
$VIS,<seq>,<person>,<score>,<cx>,<cy>,<in_zone>*<XOR>\r\n
$CTL,<seq>,<danger>,<person_enable>,<environmental_level>*<XOR>\r\n
```

- 完整、可信且新鲜的安全状态允许 OpenMV 低频检测并慢闪绿灯。
- 有效 warning/critical 或健康的本地水位警戒会启用全速检测并闪黄灯。
- 有效警戒期间稳定检测到人员后改为快闪红灯。
- 启动、坏帧、超时、模型降级或传感器故障均不会伪装成绿色安全状态。

## 源码与构建入口

| 范围 | 路径 / 入口 |
|---|---|
| ESP32 固件 | `firmware/esp32/`；`pio run -e esp32s3-n16r8-singleboard` |
| OpenMV 固件 | `firmware/openmv/`；将 `main.py`、`config.py`、`control.py`、`protocol.py`、`vision_detector.py` 复制到 OpenMV 存储根目录 |
| OpenMV 主机测试 | `python -m unittest discover -s tests -p "test_openmv_*.py"` |
| FastAPI 服务 | `server/` |
| 机器学习工程 | `ml/` |
| Windows 部署与检查 | `ops/` |
| 公共网站 | `website/` Git submodule；独立 Sites 部署 |

OpenMV 人员模型来自设备固件 ROM 的 `/rom/person_detect.tflite`，仓库无需额外提交
模型二进制文件。设备密钥、Cloudflare 凭据、运行数据库、训练产物和构建缓存继续
由 `.gitignore` 排除。
