# CoastWatch 海岸危险情况预警系统：最新项目总览

> **历史快照（已被 2026-08-17 策略替代）：** 本文关于
> `custom-water-logreg-v1`、人工场景/人工 SAFE-DANGER 标签训练第三模型的章节仅供
> 追溯，不能再当作当前实现说明。当前第三模型是
> `uk-official-coast-logreg-v2`：只用版本化英国官方/权威海岸 bundle 训练；超声波
> 经过预注册线性映射后只做模型冻结后的外部测试。请以
> [UK_OFFICIAL_TRAINING_STRATEGY.md](docs/UK_OFFICIAL_TRAINING_STRATEGY.md) 和
> [OFFICIAL_DATASET_BUNDLE.md](server/docs/OFFICIAL_DATASET_BUNDLE.md) 为准。

> 文档版本：2026-08-14  
> 项目性质：课程研究原型 / Machine Learning Research Prototype  
> 当前设备：OpenMV MV4 H7 Plus + STM32F103ZET6 + ESP32-S3-WROOM-1-N16R8  
> 安全声明：本系统不是官方公共预警系统，不能替代英国官方机构、救生员或专业海洋监测设备。

## 1. 项目一句话说明

CoastWatch 是一个“本地安全控制 + 联网展示 + 机器学习实验”的海岸危险情况研究平台：OpenMV 检测人员，STM32 负责传感器和本地报警，ESP32 负责触屏、联网、数据采集会话与模型选择，服务器保存数据、支持人工标注和训练，并提供研究风险结果。

项目不再把目标限定为“预测海啸”，而是研究更一般的危险情况，例如：

- 水位异常升高；
- 水位快速上涨；
- 大浪和强风组合；
- 潮位、浪高和海况共同恶化；
- 人员进入危险区域；
- 传感器失效或通信故障。

## 2. 最重要的设计原则

1. **STM32 是本地安全核心。** 网络、服务器或 ESP32 故障时，本地检测和报警仍应继续工作。
2. **服务器模型只能提供研究结果。** 模型不能降低 STM32 已经判定的风险等级，也不能直接触发真实公共报警。
3. **ESP32 选择的是服务器模型。** ESP32 不下载模型、不训练模型，也不在芯片上执行这些 Python 模型。
4. **模型置信度不是灾害发生概率。** `environmental_probability` 表示模型对所选类别的置信度；`risk_score` 还可能被本地报警融合逻辑抬高，二者都不能称为官方灾害概率。
5. **合成数据必须明确标记。** 桌面超声波实验和 ImpactNet 合成数据都不能冒充真实英国海岸灾害数据。

## 3. 当前完成度摘要

| 子系统 | 当前状态 | 可以演示 | 尚未完成或未验证 |
|---|---|---|---|
| OpenMV 人员检测 | 已实物验证 | 全画面人员存在检测、VIS 串口上报 | 不是身份识别，也没有人员坐标框 |
| OpenMV → STM32 | 已实物验证 | USART3 单向通信 | 长时间稳定性仍可继续测试 |
| STM32 本地应用 | 旧固件已双份备份；两阶段启动版已烧录并 verify OK | VIS/NET 校验、500 ms TEL、本地状态计算、异常接线保护 | 声光输出仍为 no-op |
| ESP32 触屏与联网 | 已实现并有实物记录 | 800×480 屏幕、触摸、Wi-Fi、天气、地区搜索、风险页、模型页、采集页 | 当前服务器版本下的完整选模/采集真机闭环仍需再走一遍 |
| STM32 → ESP32 | 实物 UART 与有效遥测已打通 | ESP32 每 500 ms 持续解析 `$TEL`、返回 `$NET` 并上传 | 活动采集会话中仍应避免重启 STM32 造成 seq 归零 |
| 超声波 | TRIG=PC10、ECHO=PC11、5V/GND 共地 | 约 1995 mm 稳定测距、滤波/baseline/rise/rate、health bit0=1 | 仍需做手动改变距离与 Collection 会话实验 |
| FastAPI 主服务 | 当前在线 | 本地后台、遥测、环境、风险、会话、标注、训练、模型目录 | 本地后台依赖仅回环地址作为访问边界，没有用户登录系统 |
| 设备网关 | 当前监听且鉴权生效 | ESP32 可通过 Token 访问缩减 API | 当前权限下未完成带 Token 的公网健康验证 |
| Cloudflare Tunnel | 服务已安装并正在运行 | 域名转发到设备网关 | 两个 SYSTEM 任务的实时状态需管理员运行 `ops/status.ps1` 最终确认 |
| Coastal Risk v1 | `ready / shadow` | 真实环境数据训练、在线推理、指标对比 | 标签是阈值弱标签，不是真实灾害真值 |
| ImpactNet v2 | `unavailable / synthetic-only` | 完整研究工程管线和合成 E2E | 没有真实英国事件训练，禁止实时选择 |
| Custom Water Model | `not_trained / simulation-shadow` | 采集、标注、训练、选模的软件闭环 | 当前 0 个会话、无训练工件、无真实性能指标 |

## 4. 总体架构

```text
OpenMV 人员检测 ──VIS──┐
                       │
超声波水位传感器 ──────┤
                       ▼
                 STM32F103ZET6
              本地状态机与安全底线
                │              │
                │              └──▶ 本地灯光/声音报警（规划）
                │  TEL / NET
                ▼
             ESP32-S3 触屏
      风险 / 天气 / 地区 / Wi-Fi
          模型选择 / 数据采集
                │ HTTPS + Device Token
                ▼
       Cloudflare Named Tunnel
                │
                ▼
       FastAPI Device Gateway :8001
                │
        ┌───────┴────────┐
        ▼                ▼
     SQLite           模型目录
        ▲                ▲
        └───────┬────────┘
                │
       FastAPI Local Admin :8000
     遥测 / 会话 / 标注 / 训练 / 评估摘要
```

另有一套独立的公开机器学习展示站 `website/`。它在浏览器中直接请求 Open-Meteo，并运行一份浏览器内 v1 模型；它目前不读取 ESP32 遥测，也不负责采集、标注或训练第三模型。

## 5. 当前硬件与接线

所有 UART 均使用 `115200, 8N1, 3.3 V TTL`，通信双方必须共地。禁止直接向 STM32、ESP32 或 OpenMV GPIO 输入未经确认的 5 V 信号。

### 5.1 OpenMV ↔ STM32

```text
OpenMV P4 / UART3 TX  ──▶ STM32 PB11 / USART3 RX
OpenMV P5 / UART3 RX  ◀── STM32 PB10 / USART3 TX（当前可不接）
OpenMV GND             ─── STM32 GND
```

这一段已经完成实物联调。OpenMV 当前使用 ROM 内置 `person_detect.tflite` 做全画面人员存在检测，不进行身份识别。

### 5.2 STM32 ↔ ESP32

```text
STM32 PA2 / USART2 TX  ──▶ ESP32 GPIO8  / UART1 RX
STM32 PA3 / USART2 RX  ◀── ESP32 GPIO14 / UART1 TX
STM32 GND               ─── ESP32 GND
```

注意：根目录旧规格中出现过 ESP32 `GPIO12`，该信息已经过期。GPIO12 被屏幕转接板用作触摸中断，禁止再接 STM32。当前权威配置是 ESP32 `GPIO8 RX / GPIO14 TX`。

### 5.3 超声波 ↔ STM32

系统设计要求超声波连接 STM32，不直接连接 ESP32。用户已经把 TRIG/ECHO 型模块接好；确切商品型号仍未确认。

当前实物接线：

```text
传感器 VCC       ─── STM32 5V
传感器 GND       ─── STM32 GND
传感器 TRI/TRIG  ──▶ STM32 PC10 / GPIO 输出
传感器 ECH/ECHO  ──▶ STM32 PC11 / EXTI 输入
```

STM32F103ZE 数据手册把 PC11 标为 5 V tolerant，因此常规不超过 5 V 的 ECHO
可在该指定引脚上直接接入。STM32 必须正常供电并共地；永久安装仍建议增加分压
或电平转换，且不得把 ECHO 直接换到未经核对的其他 GPIO。

`firmware/stm32` 已实现非阻塞驱动：PC10 产生 10 us 脉冲，PC11 双沿中断配合
1 MHz TIM2 测量回波；包含稳定 baseline、中值+指数滤波、水位上升/速度、30 ms
超时和 fail-closed 健康位。当前两阶段启动版会先在 ECHO 低时接管 PC10 并验证
TRIG 可拉低，再等待 ECHO 连续安静 50 ms 后启用测距；驱动回读不匹配会锁存故障。
当前已烧录并通过 verify，实测稳定距离约 1995 mm。

UART 防水超声波仍可作为未来替代方案，但当前这块 TRIG/ECHO 模块不使用 UART4。

### 5.4 ESP32 屏幕主要占用

- LCD 数据：GPIO `4, 5, 6, 7, 15, 16, 17, 18`
- LCD 控制：WR=2、CS=1、DC=42、RD=41、RESET=46
- 触摸 I2C：SCL=13、SDA=20，地址 `0x38`
- GPIO12：屏幕转接板触摸中断，禁止复用
- STM32 串口：RX=8、TX=14

## 6. 板间协议

### 6.1 OpenMV → STM32：VIS

OpenMV 定时发送人员检测结果。STM32 应校验帧并维护人员存在状态，视觉链路异常时进入相应故障状态，而不是静默认为安全。

### 6.2 STM32 → ESP32：TEL

```text
$TEL,<seq>,<uptime_ms>,<distance_mm>,<water_rise_mm>,<rise_rate_mm_s>,<person>,<alarm>,<health>*<checksum>
```

字段含义：

- `distance_mm`：传感器到水面的距离；
- `water_rise_mm`：相对会话基准的水位上升量；
- `rise_rate_mm_s`：水位变化速度；
- `person`：人员检测结果；
- `alarm`：STM32 本地报警等级 `0..4`；
- `health`：设备健康位，bit0 为超声波有效标志。

正常设计为 STM32 每 500 ms 发送一帧。最终现场日志连续收到 `seq=90...119`，
`distance=1994...1996 mm`、`health=0x9`，证明超声测距与网络健康位均已生效。

### 6.3 ESP32 → STM32：NET

ESP32 每秒返回网络状态。网络状态不得阻塞或替代 STM32 的本地安全逻辑。

## 7. ESP32 当前界面

ESP32 已实现以下页面：

1. **Risk Overview**：显示服务器研究风险、模型版本、分类置信度、数据质量、STM32 本地报警，以及最新 `$TEL` 的实时超声距离和相对水位变化。有新鲜 TEL 但回波暂时无效时显示 `SEARCHING ECHO`；超过 2.5 秒没有 TEL 才显示 `STM32 LINK OFFLINE`，不回退到网络海平面数据。
2. **Weather**：显示地点、气温、湿度、风、浪、海温、海平面、潮汐和海流等信息。
3. **Area**：常用地区与全球地点搜索。
4. **Wi-Fi**：扫描、输入密码、连接和忘记网络。
5. **Models**：显示服务器提供的三张模型卡，只允许选择 `ready` 模型。
6. **Collection**：开始/停止超声波模拟采集，并显示距离、水位上升、上升速度和样本数。

没有 STM32 数据时，风险页和采集页应显示 `WAITING STM32 / NO SENSOR DATA`，不能使用伪造传感器数据填充页面。

当前固件构建结果：

- 环境：`esp32s3-n16r8`
- RAM：63,552 bytes，19.4%
- Flash：1,069,041 bytes，16.3%
- `firmware.bin`：1,069,408 bytes
- SHA-256：`66B030CC4E95CF5C5775FC03B926D978710F43997634097C7F1AB1CB7B01B8AB`

设备曾完成成功烧录并验证屏幕、触摸、Wi-Fi 和环境页面；本文生成时重新编译成功，但没有再次改写设备。服务器升级后的模型选择、Start/Stop、TEL 上传和后台训练仍需完成一次真机闭环验收。

## 8. 自定义水位数据闭环

### 8.1 目标流程

```text
ESP32 点击 START
        │
        ▼
服务器创建 active session
        │
        ▼
STM32 超声波 TEL 每 500 ms 上传
        │
        ▼
ESP32 点击 STOP
        │
        ▼
后台选择已结束会话和时间段
        │
        ├── SAFE
        ├── DANGER
        └── UNKNOWN
        │
        ▼
按完整 session 切分并训练逻辑回归
        │
        ▼
服务器发布带 SHA-256 的模型工件
        │
        ▼
ESP32 Models 页面选择 Custom Water Model
        │
        ▼
服务器推理，ESP32 只显示 SHADOW 结果
```

### 8.2 会话规则

- 同一设备同时只能有一个活动会话；重复开始返回冲突，不会覆盖旧会话。
- ESP32 重启或重连后会查询 active session 并恢复。
- 第一条有效非零距离固定为该会话 baseline。
- 停止后的会话不能继续追加样本。
- 只有 completed 会话才能在后台人工标注。
- 未覆盖的时间段默认 `unknown`，绝不能自动当成 safe。
- 不同标签的重叠区间会被拒绝；标签带版本号和备注。
- 遥测与 simulation sample 在同一 SQLite 事务中写入。
- 训练会排除 `distance_mm=0` 或超声波健康位 bit0 无效的样本；OpenMV 缺失可能仍让
  本地状态保持 `alarm=FAULT`，但不会再误删独立有效的超声水位样本。

### 8.3 当前实际状态

截至 2026-08-14 本地服务现场查询：

- 当前采集会话数：`0`
- 最新设备遥测：无，接口返回 404
- 当前风险结果：无遥测，因此返回 404
- Custom Water Model：`not_trained`
- 当前所选模型：`coastal-risk-logreg-v1`

因此，目前不能展示第三模型的真实系数、混淆矩阵或性能指标。测试目录生成的 JSON 只是自动测试产物，不能当作实验结果。

## 9. 三套模型

| 模型 | 类型 | 当前状态 | 数据 | 作用 | 关键限制 |
|---|---|---|---|---|---|
| Coastal Risk v1 | 四分类多项逻辑回归 | `ready / shadow` | 6 个英国海岸的 Open-Meteo 历史环境数据 + 阈值弱标签 | 预测未来 6 小时最高环境弱风险 | 不是事故真值，模型输出不是灾害概率 |
| ImpactNet v2 | Causal TCN，obs-only / hybrid | `unavailable / synthetic-only` | 当前仅 3 个虚构站点、180 天合成数据 | 研究未来 24 小时事件风险和水位分位数 | 没有真实 UK 训练、实时特征或上线许可 |
| Custom Water Model | 二分类逻辑回归 | `not_trained / simulation-shadow` | 用户桌面超声波会话和人工标签 | 演示采集、标注、训练和选模闭环 | 只代表桌面模拟，当前还没有模型工件 |

### 9.1 Coastal Risk v1 如何训练

训练数据：

- 时间：2024-01-01 至 2025-12-31；
- 地点：Brighton、Portsmouth、Plymouth、Cardiff、Aberdeen、Bangor NI；
- 样本：105,228 条逐小时记录；
- 来源：Open-Meteo weather archive 和 marine history；
- 输入：14 项环境、时间和地点特征。

14 项特征包括：气温、湿度、风速、浪高、浪周期、海温、模式海平面、海流速度、小时周期、季节周期、经纬度。

标签不是事故记录，而是项目弱标签：根据浪高和风速阈值先得到每小时四级环境风险，再把目标定义为：

```text
y(t) = 接下来 t+1 到 t+6 小时中的最高弱风险等级
```

数据按每个地点的时间顺序做约 70% / 15% / 15% train-validation-test 切分，并在边界清除 6 小时。预处理只在训练集拟合：中位数补缺失值、标准化，再训练：

```text
LogisticRegression(
    C=0.8,
    class_weight="balanced",
    solver="lbfgs",
    max_iter=4000
)
```

### 9.2 v1 测试指标与规则基线

| 测试指标 | 逻辑回归 v1 | 当前时刻阈值规则 |
|---|---:|---:|
| Accuracy | 0.7137 | 0.8615 |
| Macro-F1 | 0.6178 | 0.7161 |
| 高风险 Recall | 0.8306 | 0.6104 |
| Critical Precision | 0.5931 | 0.8947 |
| Critical Recall | 0.8130 | 0.5658 |
| Critical F1 | 0.6858 | 0.6932 |
| Multiclass Brier | 0.3654 | 未计算 |

完整报告：`ml/reports/coastal_risk_v1_metrics.json`  
模型工件：`server/models/coastal_risk_v1.json`

这些指标说明：模型没有全面击败规则。它用更多误报换取更少漏报：高风险漏报从 789 降到 343，但误报从 116 增加到 1,041。当前最合理的定位是“更敏感的未来阈值越界研究基线”，不是“更准确的真实灾害模型”。

### 9.3 ImpactNet v2

ImpactNet v2 的工程设计包括：

- 72 小时历史观测；
- 24 小时未来预报；
- causal TCN；
- observation-only 和 forecast-hybrid 两种模型；
- 未来逐小时 hazard、累计事件概率和 P10/P50/P90 水位；
- 全局时间切分、目标窗口 purge、storm-group 隔离和 LOSO；
- validation-only 概率校准和运行阈值选择；
- frozen test、事件级指标、误报 episode、提前量和水位分位数指标；
- hash 验证的 `safetensors` bundle 和 Shadow API。

当前持久运行只有确定性合成数据：3 个虚构站点、180 天、2 个 CPU epoch。合成测试确实产生了事件指标和图表，但报告明确包含 `synthetic_only=true`、`scientific_result=false` 和 `insufficient_evidence=true`。这些数字只能证明软件管线可运行，不能作为英国海岸真实性能。

权威状态文档：

- `ml/IMPLEMENTATION_STATUS.md`
- `ml/README_IMPACTNET.md`
- `ml/artifacts/runs/synthetic-e2e-20260813-final/`

### 9.4 Custom Water Model

第三模型计划使用 8 项窗口特征：

1. 当前距离；
2. 会话基准距离；
3. 当前水位上升量；
4. 当前上升速度；
5. 相邻水位差；
6. 窗口水位斜率；
7. 窗口水位均值；
8. 窗口水位标准差。

默认使用最近 5 个有效样本。训练采用完整会话切分，`StandardScaler` 只在训练集拟合，然后训练：

```text
LogisticRegression(
    class_weight="balanced",
    solver="lbfgs",
    max_iter=2000
)
```

默认概率阈值为 0.5。工件将保存：Accuracy、Balanced Accuracy、Danger Precision/Recall/F1、Brier、Log Loss、混淆矩阵、训练/测试会话和样本分布。

软件最低要求是至少两个独立会话并同时存在 safe/danger，但两次会话不足以支持研究结论。建议至少采集多组独立安全与危险会话，并改变水位速度、幅度、噪声和操作方式。

## 10. 教授提出的核心问题

### 问题

> 如果某些数据低于阈值就能判定低风险，那么训练逻辑回归有什么意义？

### 严谨答案

教授的质疑是成立的。如果输入变量和标签来自同一时刻、同一套阈值，那么逻辑回归通常只是在把硬阈值拟合成一条平滑边界，规则更透明，模型没有必要。

v1 与“当前状态分类”略有不同：

```text
输入 x(t) = 当前环境快照
目标 y(t) = 未来 1~6 小时内最高的阈值弱风险
```

因此 v1 的有限研究问题是：当前尚未越界时，其他环境、时间和地点信息能否更早识别未来越界。测试中它提高了高风险 Recall，但显著增加误报，并且目标仍然来自人工阈值。因此当前只能声称：

> 模型展示了未来阈值越界预测和召回率/误报率取舍的工程可行性；它没有证明机器学习已经优于规则，也没有学会真实灾害规律。

### 第三模型应如何避免重复这个问题

当前 Custom Water Model 代码把窗口特征与同一时间段的人工 safe/danger 标签配对，本质上仍是状态分类。更有研究价值的下一版本应该把标签改成未来事件：

```text
y(t) = 未来 H 秒内是否越过物理危险线、发生溢流或达到人工确认事件
```

模型只能使用时间 `t` 之前的距离、水位、速度、斜率和波动。然后在完全独立的会话上与以下规则基线比较：

- 固定水位阈值；
- 水位阈值 OR 上升速度阈值；
- 按当前速度计算预计到达危险线的时间；
- 历史发生率或多数类基线。

应优先报告：

- 在相同误报率下的 Danger Recall；
- Precision、F1 和混淆矩阵；
- 每个独立会话的误报次数；
- Brier/ECE 概率质量；
- 预警提前秒数；
- 多次会话或事件组 bootstrap 置信区间。

如果模型不能稳定优于规则，就应保留规则。拒绝无增益的复杂模型本身也是正确的研究结论。

## 11. 服务器与 API

### 11.1 两个服务

| 服务 | 地址 | 访问边界 | 作用 |
|---|---|---|---|
| Local Main | `http://127.0.0.1:8000` | 仅本机回环 | 管理后台、查询、标注、训练、完整 API |
| Device Gateway | `http://127.0.0.1:8001` | Token 鉴权 | ESP32 所需的缩减 API，经 Cloudflare 暴露 |

设备公网地址：`https://weather.ashlxylock.uk`。Token 不得写进网页 JavaScript、URL、日志或公开仓库。

### 11.2 API 能力

| API | 8000 | 8001 | 作用 |
|---|---:|---:|---|
| `POST /api/v1/telemetry` | 是 | 是 | 上传普通或采集会话遥测 |
| `GET /api/v1/telemetry/latest` | 是 | 否 | 后台最新遥测 |
| `GET /api/v1/telemetry` | 是 | 否 | 后台遥测历史 |
| `GET /api/v1/models` | 是 | 是 | 模型目录和当前选择 |
| `PUT /api/v1/device-model` | 是 | 是 | 选择 ready 模型 |
| `POST /api/v1/simulations/sessions` | 是 | 是 | 开始会话 |
| `GET /api/v1/simulations/sessions/active` | 是 | 是 | 恢复活动会话 |
| `POST /api/v1/simulations/sessions/{id}/stop` | 是 | 是 | 停止会话 |
| `GET /api/v1/simulations/sessions` | 是 | 否 | 后台列出会话 |
| `GET .../{id}/samples` | 是 | 否 | 后台读取样本 |
| `GET .../{id}/labels` | 是 | 否 | 后台读取标签 |
| `PUT /api/v1/simulations/labels` | 是 | 否 | 保存人工区间标签 |
| `POST /api/v1/simulations/train` | 是 | 否 | 训练第三模型 |
| `GET /api/v1/environment` | 是 | 是 | 天气和海况 |
| `GET /api/v1/risk` | 是 | 是 | 按设备所选模型推理 |
| 地区和健康接口 | 是 | 是 | 地区配置与服务检查 |

公网网关故意不暴露样本查询、人工标注、模型训练、遥测历史、后台首页和 OpenAPI 文档。

### 11.3 本地后台

打开 `http://127.0.0.1:8000/` 可以：

- 查看最新遥测、环境和健康位；
- 查看并设置 ESP32 地区；
- 查看模型目录和当前选择；
- 查看、结束采集会话；
- 查看每个会话的样本；
- 选择序号区间并标注 `SAFE / DANGER / UNKNOWN`；
- 设置标签版本和备注；
- 训练第三模型并查看 Balanced Accuracy 摘要。

后台不能开始采集，START 必须由 ESP32 发起；后台也不提供选模按钮，模型由 ESP32 选择。

## 12. 部署与持久化

受保护部署目录：

```text
C:\ProgramData\CoastalWarning\
├── runtime\        # 只读部署运行时
├── data\           # SQLite 持久数据
├── models\         # Custom Water 模型工件
├── secrets\        # Device Token
└── logs\           # 运行日志
```

部署设计：

- `CoastalWarning-Main`：SYSTEM 开机任务；
- `CoastalWarning-Gateway`：SYSTEM 开机任务；
- `cloudflared`：LocalSystem 自动服务；
- 工作区代码不会被计划任务直接执行；
- 重新安装只替换生成的 runtime，保留数据库和模型；
- Token、Tunnel 凭据和目录使用受限 ACL。

本次现场确认：

- `127.0.0.1:8000/api/v1/health` 返回 200，数据库正常；
- 8000 和 8001 均在 `127.0.0.1` 监听；
- 8001 无 Token 返回 401，鉴权生效；
- `cloudflared` 为 `RUNNING / AUTO_START / LocalSystem`；
- 安装日志记录启动服务安装成功；
- 当前权限不能读取受保护的 SYSTEM 任务状态和 ProgramData 内容。

最终管理员核验命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\ops\status.ps1
```

## 13. 公开机器学习网站

`website/` 是双语研究展示站，当前功能包括：

- 六个英国海岸切换；
- 浏览器直接获取 Open-Meteo weather/marine；
- 请求失败时使用明确标注的研究快照；
- 浏览器运行 Coastal Risk v1；
- 展示四级分类、模型置信度、主要贡献因素、指标、混淆矩阵和限制；
- 明确标注 weak labels、shadow mode 和非官方预警。

它与本地管理后台是两套不同界面，目前没有 ESP32 遥测、会话、标签、训练或设备选模能力。如果未来要接入设备数据，应使用同源服务器端代理保存 Token，不能把 Device Token 暴露给浏览器。

## 14. 2026-08-14 验证结果

| 检查 | 本次结果 |
|---|---|
| Server pytest | `55 passed` |
| Website production build + render tests | 构建成功，`2 passed` |
| ESP32 release build / upload | 成功，RAM 19.4%，Flash 16.3%；COM8 烧录校验通过，风险页实时超声卡已现场验证 |
| STM32 protected sensor bridge build | 成功，RAM 3.0%，Flash 2.7%；烧录 verify OK |
| STM32 pure-logic host tests | 全部通过 |
| ML/ImpactNet pytest | `139 passed, 1 skipped, 2 failed` |

ST-Link 已识别 STM32F103ZE、512 KiB Flash、读保护关闭。原 Flash 和 option bytes
各独立读回两次，Flash SHA-256 均为 `2090BF...A587`，option bytes SHA-256 均为
`639F51...7F91`。保护版固件 SHA-256 为 `21EFF5...17A4`，烧录后片上 verify OK。

最终两阶段固件 SHA-256 为 `277E5E...DBE9`，烧录后片上 verify OK。COM8 现场监听
确认约 1995–1996 mm 稳定距离、`health=0x9`，ESP32 每 500 ms 持续收到 STM32 TEL；
服务器 POST 持续返回 201，后台最新记录为非零距离且超声健康位有效。`alarm=4`
来自当前 OpenMV/整体安全状态，不会再使有效超声样本被训练过滤。

ESP32 风险页原 `SEA LEVEL / TIDE` 卡已改为 `ULTRASONIC`：现场日志确认 LCD 连续以
`ultrasonic=1` 重绘，距离约 1989--1993 mm，水位变化约 -3--+1 mm；断流、健康位无效
或距离越界时不会伪造数值，并区分 `SEARCHING ECHO` 与真正的 STM32 链路离线。
对应 Unity 测试 9/9 通过。

随后现场出现连续无有效回波。诊断确认 ESP32、TEL 和上传链路均未离线，STM32 仍在
运行，但 PC11/ECHO 的回波脉冲未在有效窗口内结束。常驻恢复版已同时烧录到 STM32
和 ESP32：STM32 每 100 ms 自动调度探测，单次漏测有 1 秒宽限，ECHO 回低后无需
复位即可恢复；连续无有效回波仍诚实输出 `distance=0 / health bit0=0`。当前 STM32
固件 SHA-256 为 `3AE41B...CD60`，ESP32 固件为 `1C7E8D...6069`，两次烧录均校验成功。

两个 ML 失败来自同一个回归锚点：`server/app/risk_model.py` 因近期增加模型调度/融合逻辑而 SHA-256 已变化，但两处测试仍期待旧 SHA。其余 7 个受保护 v1 数据、指标、模型和网站锚点保持一致。

这不等于模型训练或推理测试失败，但也不能直接把旧哈希改掉。正确处理顺序是：

1. 审阅 `risk_model.py` 的近期改动；
2. 确认 v1 的特征、系数、公式、规则回退和输出语义没有意外变化；
3. 再更新 `test_v1_regression.py` 与 CLI legacy audit 中的 SHA；
4. 重新运行全部 ML 测试。

因此，旧文档中的 `141 passed, 1 skipped` 已不是今天的完整状态。

## 15. 推荐演示流程

### 演示前置条件

- 确认超声波具体型号；
- 完成 STM32 超声波驱动；
- STM32 每 500 ms 发送有效 TEL；
- `distance_mm > 0`；
- `health_flags` bit0 为 1；
- ESP32 串口能持续打印 `[UART] TEL ...`；
- 本地服务和设备网关正常。

### 演示步骤

1. 在 ESP32 打开 Risk 页面，展示天气、海况、本地状态和当前 v1。
2. 进入 Models，说明 v1 ready、ImpactNet synthetic-only、Custom not trained。
3. 进入 Collection，点击 START。
4. 用容器或目标板模拟水面距离，制造平稳、缓慢上升、快速上升和下降阶段。
5. 点击 STOP。
6. 打开 `http://127.0.0.1:8000/`，选择已完成会话。
7. 把明确时间段标为 SAFE、DANGER 或 UNKNOWN，并填写备注。
8. 重复多个独立会话。软件最低为 2 个，正式演示建议至少 4–10 个。
9. 点击训练，查看完整返回指标，不只看 Balanced Accuracy。
10. 回到 ESP32 Models，选择 ready 的 Custom Water Model。
11. 展示服务器按新模型推理，但页面仍标注 `SIMULATION / SHADOW / RESEARCH ONLY`。
12. 断网并说明 STM32 本地安全逻辑不应依赖服务器。

## 16. 当前最高优先级任务

### P0：完成真实水位链路

已完成：建立 `firmware/stm32`，实现驱动/滤波/baseline/rise/rate、VIS/TEL/NET 和
异常接线保护；完成交叉编译、主机测试、旧 Flash 双备份、烧录与片上 verify；
STM32→ESP32 TEL 实物串口已打通。

剩余步骤：

1. 手动移动硬平面，确认物体靠近时 distance 下降、water rise 上升；
2. 在 ESP32 开始 Collection，采集多个 safe/danger 独立会话；
3. 完成后台入库 → STOP → 标注 → 训练闭环；
4. 验证断网、HTTP 超时和拔掉 ESP32 时 STM32 本地计算不受影响。

### P1：做第三模型的第一个有效实验

1. 采集多个独立 session；
2. 预先定义标注规则和 `unknown` 区间；
3. 保证 safe/danger 都存在；
4. 按完整 session 切分；
5. 加入固定水位和水位+速度规则基线；
6. 报告 Precision、Recall、F1、Brier、混淆矩阵和每会话误报；
7. 下一版改为“未来 H 秒事件”标签，并报告提前量。

### P2：回答机器学习增量价值

在同一冻结测试集上比较：

1. 当前阈值规则；
2. 把阈值直接应用到未来天气/海况预报；
3. 只用浪高和风速的逻辑回归；
4. 去掉浪高和风速的消融模型；
5. 全特征逻辑回归；
6. ImpactNet 或其他时序模型。

只有模型在相同误报水平下稳定改善事件召回、提前量、概率校准和跨地点泛化，才有采用机器学习的依据。

### P3：修复工程审计项

- 审阅并更新 stale v1 SHA 回归锚点；
- 用管理员 `ops/status.ps1` 完成任务和公网健康核验；
- 更新根目录旧 GPIO12 文档，统一为 GPIO8；
- 让公开网站通过安全的同源服务端代理读取设备摘要，或继续明确保持独立；
- 将完整第三模型评估报告加入后台，而不是只显示 Balanced Accuracy。

## 17. 关键文件索引

| 内容 | 路径 |
|---|---|
| 当前三板接线 | `docs/wiring.md` |
| 当前硬件清单 | `docs/hardware_inventory.md` |
| 总体旧规格 | `README_Coastal_Warning_System.md` |
| OpenMV 固件 | `firmware/openmv/` |
| STM32 超声波桥接固件 | `firmware/stm32/` |
| ESP32 固件 | `firmware/esp32/` |
| ESP32 引脚/接口配置 | `firmware/esp32/include/app_config.h` |
| ESP32 页面与触控 | `firmware/esp32/src/main.cpp`、`display.cpp` |
| ESP32 网络任务 | `firmware/esp32/src/network_uplink.cpp` |
| FastAPI 主服务 | `server/app/main.py` |
| 设备鉴权网关 | `server/app/gateway.py` |
| 本地后台页面 | `server/app/dashboard.py` |
| 模型目录/选模 | `server/app/model_registry.py` |
| 会话和标注存储 | `server/app/simulation_store.py` |
| Custom 模型 | `server/app/simulation_model.py` |
| v1 训练代码 | `ml/coastal_risk/train.py` |
| v1 完整指标 | `ml/reports/coastal_risk_v1_metrics.json` |
| v1 服务器模型 | `server/models/coastal_risk_v1.json` |
| ImpactNet v2 | `ml/coastwatch_impact/` |
| ImpactNet 状态 | `ml/IMPLEMENTATION_STATUS.md` |
| 合成 E2E 工件 | `ml/artifacts/runs/synthetic-e2e-20260813-final/` |
| 公开研究网站 | `website/` |
| Windows 部署 | `ops/` |

## 18. 可直接用于答辩的结论

> Our current system is best described as a reproducible machine-learning research platform rather than a proven disaster-prediction product. The v1 logistic model predicts future threshold exceedance and improves high-risk recall, but it produces more false alarms and its labels are still rule-derived. ImpactNet v2 demonstrates a leakage-aware end-to-end engineering pipeline on synthetic data only. The custom ultrasonic model completes the planned collect-label-train-select workflow, but it has not yet been trained because no valid sensor sessions have been collected. Our next experiment will compare machine learning with explicit water-level and rise-rate rules on completely held-out sessions and will retain the simpler rule system if the model cannot show measurable incremental value.

中文简述：

> 当前成果不是“已经成功预测自然灾害”，而是搭建了一个可复现的机器学习研究平台。v1 在未来阈值越界任务上提高了高风险召回，但误报增加，而且标签仍来自规则；ImpactNet 目前只有合成工程证据；第三模型的软件闭环已经完成，但尚无真实超声波会话和训练指标。下一步必须用独立事件标签和规则基线做公平测试，如果机器学习没有稳定增益，就保留规则。
