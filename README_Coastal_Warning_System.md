# CoastWatch 海岸风险研究与现场采集系统

> 当前架构：**ESP32-S3 + OpenMV + HC-SR04 + FastAPI**
> 更新时间：2026-08-19

CoastWatch 是面向机器学习课程展示的海岸风险研究平台。OpenMV 负责人员检测，
ESP32-S3 直接采集超声波、运行确定性本地规则、驱动触屏并上传遥测；FastAPI
保存数据、提供管理员操作台并运行研究模型。旧的外置传感桥接板已退出当前架构，
其桥接固件和串口回滚协议不再维护。

## 1. 系统边界

- ESP32 的本地超声和人员融合规则不依赖 Wi-Fi 或服务器。
- 服务器模型处于研究/旁路模式，不能降低或覆盖本地报警。
- OpenMV 的灯光仅是状态提示，不是公共安全认证：完整安全时绿灯慢闪；有效警戒
  且尚未检测到人时黄灯闪；警戒状态检测到人时红灯快闪；未知、故障和超时熄灯。
- 官方数据模型和桌面超声实验都必须保留来源、切分和测试证据，不能把人工标签
  的分类置信度称为真实灾害概率。

## 2. 当前数据流

```text
OpenMV person detection -- VIS/UART --> ESP32-S3
ESP32 model/local mode  -- CTL/UART --> OpenMV status LED
HC-SR04 -- divided ECHO/TRIG -------> ESP32-S3
ESP32 local rules + LCD + Collection
                 |
                 +-- HTTPS telemetry --> FastAPI gateway --> SQLite
                                                    |
                                                    +--> admin console
                                                    +--> model training
                                                    +--> shadow risk API
```

ESP32 每 500 ms 生成一份 `TelemetryFrame`，LCD 和服务器使用同一份数据：

- `seq`、`uptime_ms`
- `distance_mm`：探头到反射面的距离
- `water_rise_mm`：相对本次稳定基准的水位变化
- `rise_rate_mm_s`
- `person_detected`
- `alarm_level`：`0 safe / 1 advisory / 2 warning / 3 critical / 4 fault`
- `health_flags`：bit0 超声、bit1 OpenMV、bit2 电源监测、bit3 网络

bit2 当前未配置，后台必须显示 `NOT CONFIGURED`，不能误报为电源故障。

## 3. 硬件与接线

权威接线只有当前双板方案：

```text
OpenMV P4 / UART3 TX  ---> ESP32 GPIO8  / UART1 RX  (VIS)
OpenMV P5 / UART3 RX  <--- ESP32 GPIO14 / UART1 TX  (CTL)
OpenMV GND             --- ESP32 GND

HC-SR04 VCC            --- stable 5 V
HC-SR04 GND            --- common GND
HC-SR04 TRIG           <--- ESP32 GPIO10
HC-SR04 ECHO -- divider ---> ESP32 GPIO40
```

ESP32-S3 GPIO 不耐 5 V。ECHO 必须通过已核验的 5 V→3.3 V 分压或单向电平
转换器；当前实物使用 2 个 220Ω 作为上臂、3 个 220Ω 作为下臂，名义输出约
3.0 V。禁止把 ECHO 直接接入任何 ESP32 GPIO。

详细占用和供电要求见 [docs/wiring.md](docs/wiring.md) 与
[docs/hardware_inventory.md](docs/hardware_inventory.md)。

## 4. OpenMV 协议

OpenMV → ESP32：

```text
$VIS,<seq>,<person>,<score>,<cx>,<cy>,<in_zone>*<XOR>\n
```

ESP32 → OpenMV：

```text
$CTL,<seq>,<danger>,<person_enable>,<environmental_level>*<XOR>\r\n
```

两种帧都对 `$` 后到 `*` 前的 ASCII 字节做 XOR。坏校验、超长帧、重放或控制
超时都 fail-safe；OpenMV 继续检测人员，但不会点亮“安全”或“危险”指示灯。

## 5. ESP32 本地逻辑

- HC-SR04 每 100 ms 非阻塞调度，30 ms 无回波超时。
- 三个稳定样本建立基准；五点中值和 Q8 EMA 生成平滑距离。
- `water_rise_mm = baseline_distance_mm - filtered_distance_mm`。
- 1 秒没有有效回波清除超声健康位，3 秒后清基准并自动重建。
- OpenMV VIS 超过 1 秒不新鲜时进入 fault，不伪造“无人”。
- 网络离线不停止测距、视觉解析或本地状态计算。
- NVS 预留遥测序号，避免重启恢复活动采集会话时重复 `seq`。

## 6. LCD 与采集会话

LCD 提供天气、风险、模型、Wi-Fi、地点选择和 Collection 页面。传感器主值明确
显示 `LEVEL CHANGE`，原始距离显示 `SENSOR GAP`；二者都不是绝对海平面。

采集流程：

1. 管理员后台配置实验/官方数据所需上下文。
2. LCD 打开 `MODELS -> COLLECTION`，点击 `START`。
3. ESP32 以 500 ms 间隔上传并附带 `simulation_session_id`。
4. 二次确认 `STOP`，服务器将会话置为 completed。
5. 管理员在时间线上标注或选择合法数据，然后手动训练/测试研究模型。

服务器上的样本计数、上传 ACK 和本地收到的帧数分开显示，不能把 LCD 本地计数
冒充数据库已存数量。

## 7. 后端与网站

- `server/app/main.py`：内部 FastAPI 应用、数据库、模型和管理数据接口。
- `server/app/gateway.py`：设备令牌网关与 `/admin` 登录/CSRF 边界。
- `server/app/dashboard.py`：会话、曲线、标注、官方训练、模型指标和外部测试台。
- `ops/`：Windows SYSTEM 启动任务、ACL、回滚部署与 Cloudflare Tunnel。
- 公共网站源：[Ashlxy-Lock/coastwatch-website](https://github.com/Ashlxy-Lock/coastwatch-website)。

设备接口只接受设备令牌；场景编辑、标注、训练、激活和删除只能通过管理员会话，
写操作还要求 CSRF。数据库、凭据、原始许可数据和模型运行工件不会提交到 Git。

## 8. 模型研究

仓库包含：

- 历史环境逻辑回归基线；
- UK official archive 数据注册、严格 train/validation/frozen-test 隔离和手动训练；
- 超声波线性映射的外部测试；
- 同测试拆分上的阈值基线与按站点指标。

模型激活仍受来源、原始文件哈希、站点数、每个 split 行数、站点级指标覆盖和公平
基线约束。当前来源保证是“operator-attested + raw hash verified”，不是确定性 importer
重放验证；答辩中必须如实说明。

## 9. 源码目录

```text
firmware/esp32/     ESP32 单主控固件、测试和 PlatformIO 配置
firmware/openmv/    OpenMV 检测、协议、控制与板载 LED
server/             FastAPI、数据库、管理后台和模型服务
ml/                 历史模型与 ImpactNet 研究流水线
ops/                部署、启动、ACL、回滚和状态脚本
docs/               当前架构、接线、硬件与训练策略
tests/              跨组件 OpenMV/协议测试
website/            公共网站源码子模块（独立 Sites 发布历史）
```

## 10. 构建与验证

ESP32：

```powershell
Set-Location firmware\esp32
pio run -e esp32s3-n16r8-singleboard
pio test -e esp32s3-test -f test_protocol --without-uploading --without-testing
pio test -e esp32s3-test -f test_sensor_logic --without-uploading --without-testing
pio test -e esp32s3-test -f test_openmv_control --without-uploading --without-testing
```

OpenMV 主机测试：

```powershell
server\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_openmv*.py'
```

后端：

```powershell
Set-Location server
.\.venv\Scripts\python.exe -m pytest -q --basetemp=tmp\pytest-current
.\.venv\Scripts\python.exe -m ruff check app tests
```

烧录、改线和管理员部署是独立操作；测试通过不等于已经对硬件执行这些动作。

> 核心原则：**现场检测在 ESP32/OpenMV 本地继续运行；联网只增加记录、可视化和研究模型能力。**
