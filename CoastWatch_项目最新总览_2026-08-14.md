# CoastWatch 项目最新总览

更新时间：2026-08-19
当前版本：ESP32-S3 + OpenMV 双板现场节点，FastAPI/Cloudflare 联网平台

## 一句话说明

CoastWatch 用 ESP32-S3 直接测量超声波距离和相对水位、执行本地确定性规则，
OpenMV 检测人员并显示绿/黄/红状态，服务器负责数据留存、管理可视化和机器学习
实验。旧的外置传感桥接板已退役，不再是构建、接线或运行依赖。

## 当前组成

| 组件 | 当前职责 | 状态 |
|---|---|---|
| ESP32-S3 N16R8 | HC-SR04、滤波/基准/变化率、本地报警、LCD/触摸、Wi-Fi、上传、Collection | 单板固件已构建并完成实物链路验证 |
| OpenMV H7 Plus | 全画面人体检测、VIS 上报、CTL 模式、板载状态灯 | VIS/CTL 双向 UART 与 LED 状态机已实现 |
| HC-SR04 | 探头到反射面的距离 | TRIG=GPIO10；ECHO 经分压到 GPIO40 |
| FastAPI main/gateway | 遥测、会话、标注、官方数据训练、模型选择、风险接口、管理员认证 | 本机 SYSTEM 任务 + Cloudflare Tunnel |
| SQLite/JSON artifacts | 审计数据与不可变模型工件 | 只在受保护运行目录，不提交 Git |
| 公共网站 | 项目展示及同源 `/admin` 代理 | 独立 `coastwatch-website` 仓库和 Sites 部署 |

## 现场接线

```text
OpenMV P4 / TX  ---> ESP32 GPIO8  / RX   VIS
OpenMV P5 / RX  <--- ESP32 GPIO14 / TX   CTL
OpenMV GND       --- ESP32 GND

HC-SR04 VCC      --- stable 5 V
HC-SR04 GND      --- common GND
HC-SR04 TRIG     <--- GPIO10
HC-SR04 ECHO     ---> 5V-to-3.3V divider ---> GPIO40
```

当前 ECHO 分压使用 2×220Ω 上臂和 3×220Ω 下臂。ESP32-S3 没有耐 5 V GPIO，
禁止绕过分压。GPIO42 是 LCD D/C，GPIO12 是触摸 INT，GPIO35/36/37 属于 Octal
PSRAM，均不能改作 ECHO。

## 本地安全与状态灯

ESP32 每 100 ms 调度超声、每 500 ms 发布本地遥测。三点稳定基准、五点中值和
EMA 生成 `distance_mm`、`water_rise_mm` 和 `rise_rate_mm_s`。无有效回波 1 秒
后超声健康失效，3 秒后重建基准；OpenMV VIS 断流 1 秒进入 fault。

OpenMV 接收 ESP32 的 CTL：

- 完整安全：绿灯慢闪；
- 模型或健康本地水位进入警戒、尚未检测到人：黄灯闪；
- 警戒且检测到人：红灯快闪；
- advisory、fallback、fault、坏帧、重放、超时：两灯熄灭并保持 fail-safe 检测。

服务器模型不能写回或降低 ESP32 的本地报警。网络、HTTP 或服务器故障不会停止
本地测距和视觉处理。

## LCD 功能

- 天气/海况和当前地点；
- Wi-Fi 扫描、屏上密码输入、保存/忘记配置；
- 风险总览、模型版本、数据质量和本地报警；
- 模型目录与选择；
- Collection START/STOP、上传 ACK、会话恢复、样本质量；
- 超声主值 `LEVEL CHANGE` 与辅助值 `SENSOR GAP`。

`SENSOR GAP` 是探头到物体的距离，不是绝对海平面；`LEVEL CHANGE` 是相对 ESP32
本次稳定基准的变化。

## 后端与管理员操作台

公网设备接口由 `X-Device-Token` 保护。管理员从 `/admin/login` 登录，Cookie 为
HttpOnly/Secure/SameSite，并对写操作检查 CSRF。后台当前可以：

- 查看实时遥测、健康位、风险和环境；
- 建立/结束采集会话，查看三轨时间线和审计表；
- 标记 SAFE/DANGER/UNKNOWN；
- 选择训练会话并安全删除未被工件引用的 completed 会话；
- 扫描 UK official dataset bundle、检查 readiness、手动训练和激活；
- 建立冻结的超声线性映射 profile 并运行外部测试；
- 查看 row-level、site-macro、混淆矩阵、概率指标和公平阈值基线。

管理写接口不会暴露给设备令牌。删除和训练使用跨进程 artifact lock，避免模型
发布与会话删除之间发生竞态。

## 机器学习策略

当前主策略是用受保护目录中的英国官方来源 bundle 训练逻辑回归，再把桌面
HC-SR04 的相对变化通过冻结线性映射用于外部测试。训练、validation 和 frozen-test
严格隔离；逻辑回归与按站点 validation 选择的简单水位阈值在同一 frozen-test 上
比较。

重要限制：

- 来源声明、许可和标签推导目前仍是 operator-attested；服务器验证原始文件字节和
  SHA-256，但没有确定性 importer 重放。
- 研究模型为 shadow，不是英国官方告警服务。
- 人工桌面实验不能证明真实海岸部署效果。
- 没有完整站点覆盖、至少三个站点和足够 split 行数时不得激活。

## 仓库内容

`Ashlxy-Lock/coastwatch-system` 是系统权威仓库，包含 ESP32、OpenMV、FastAPI、
机器学习、运维脚本、测试和当前文档。`website/` 以 Git submodule 固定到
`Ashlxy-Lock/coastwatch-website`，保留独立 Sites 发布历史，同时让完整项目可以
通过 `git clone --recurse-submodules` 一次取得。

仓库明确排除：Wi-Fi/设备/管理员/Cloudflare 凭据、生产数据库、许可原始数据、
训练工件、虚拟环境、构建输出和厂商资料包。

## 复现实验入口

```powershell
# ESP32 release build
Set-Location firmware\esp32
pio run -e esp32s3-n16r8-singleboard

# OpenMV host logic
Set-Location ..\..
server\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_openmv*.py'

# Backend
Set-Location server
.\.venv\Scripts\python.exe -m pytest -q --basetemp=tmp\pytest-current
.\.venv\Scripts\python.exe -m ruff check app tests
```

烧录和管理员部署需要单独授权；源代码测试不会自动改写硬件或生产数据。

## 当前待办

1. 完成更多距离标尺与长时间稳定性实测。
2. 准备带原始文件哈希、未来标签和足够站点/行数的 UK official bundle。
3. 通过后台手动训练，报告逻辑回归与公平阈值基线的 frozen-test 结果。
4. 轮换演示管理员口令，并为正式公网后台增加 Cloudflare Access。
5. 为 GitHub `main` 增加 PR、审查和自动测试规则。
