# ESP32-S3 单主控迁移

更新时间：2026-08-18

## 结论与当前状态

目标架构把 STM32 的职责迁入现有 ESP32-S3：超声波触发/回波捕获、稳定基准、
滤波、相对水位、变化率、OpenMV 状态、健康位和确定性本地报警均由 ESP32
生成。LCD、触摸、Wi-Fi、Collection、服务器上传和模型选择继续使用现有实现。
服务器模型仍在服务器运行，模型结果只用于研究展示，不反向控制本地报警。

**实物已切换并完成单板烧录。** HC-SR04 ECHO 使用 5 个 220Ω 电阻组成约
3.0 V 分压后接 GPIO40；OpenMV P4/TX 接 GPIO8。实机日志确认超声
`armed=1/healthy=1`、距离约 2002 mm、综合健康位 `0xB`、HTTP POST=201，
服务器数据库与风险接口均收到同一份遥测。普通构建环境仍保持超声禁用，只有
`esp32s3-n16r8-singleboard` 显式启用已核验接线。

## 目标接线

所有改线必须在全部断电时进行：

```text
OpenMV P4 / UART3 TX ─────────────▶ ESP32 GPIO8 / UART1 RX
OpenMV GND           ────────────── ESP32 GND

HC-SR04 VCC          ────────────── 稳定 5V
HC-SR04 GND          ────────────── 公共 GND
HC-SR04 TRIG         ◀───────────── ESP32 GPIO10
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

两只 220Ω 位于 ECHO 与 GPIO40 之间，三只 220Ω 位于 GPIO40 与 GND 之间，
名义输出约 3.0 V；第六只留作备用。也可以用经过核验的单向 5 V→3.3 V
电平转换器代替该分压。单独串一个
电阻、改用另一个 ESP32 GPIO、ADC attenuation 或下载串口都不能让 5 V ECHO
变安全。没有分压/转换器时禁止连接 ECHO。

GPIO14 保留为可选的 ESP32 TX→OpenMV P5/RX；当前 VIS 是单向协议，可以不接。
GPIO21 保留给未来经过晶体管/MOSFET 驱动的蜂鸣器。当前实物没有蜂鸣器、继电器
或灯带，不能宣称已有声音报警。

## 软件边界

- 超声每 100 ms 调度，30 ms 回波超时；ISR 只记录边沿时间。
- 三个稳定样本（跨度不超过 20 mm）建立基准。
- 五点中值后使用 Q8 `0.8 × old + 0.2 × median` 平滑。
- `water_rise_mm = baseline_distance_mm - filtered_distance_mm`。
- 一秒没有有效回波清除超声健康位，三秒后清除基准并自动重建。
- OpenMV 一秒没有合法 VIS 帧时进入 `FAULT`；不得伪造视觉健康。
- 网络离线不阻塞测距或本地规则。
- 每 500 ms 仍生成现有 `TelemetryFrame`，后端 JSON schema 不变。
- 遥测 `seq` 由 NVS 预留号段，避免活动会话重启后重复序号导致 409。
- 旧 `$TEL/$NET` 解析器保留为回归/回滚兼容路径，正常单板运行不再经过它。

## 已执行切换与后续复验清单

1. 停止并结束任何 active Collection 会话，避免旧 STM32 序号与新序号混用。
2. 保存当前 ESP32 `firmware.bin`，不要擦除 NVS。
3. 拆除 STM32 PA2/PA3、PB10/PB11、PC10/PC11 的外部信号连接。
4. 安装并万用表核对 ECHO 电平转换；5 V 输入时 ESP32 侧必须不超过 3.3 V。
5. 使用 `esp32s3-n16r8-singleboard` 环境构建和烧录，普通环境不启用超声引脚。
6. 已验证 LCD、Wi-Fi、OpenMV UART、超声健康和服务器上传；触摸操作继续现场检查。
7. 后续用 10/20/50 cm 大平面目标复验线性测距，并验证断网、OpenMV 断线和回波恢复。

本次固件大小为 1,090,112 bytes，SHA-256 为
`E61FFBBB67E6BCADBBDEF93D4B02971393E0F8D977E261FB5A52780358736C8A`；COM8
烧录各分区均返回 `Hash of data verified`。

本迁移不删除 `firmware/stm32`：它保留为已经验证的回滚固件与算法对照。
