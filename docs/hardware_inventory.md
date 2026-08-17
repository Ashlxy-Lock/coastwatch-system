# 硬件清单

更新时间：2026-08-14

| 模块 | 当前信息 | 状态 |
|---|---|---|
| OpenMV | MV4 H7 Plus | 用户已确认板型名称 |
| OpenMV 固件 | OpenMV v4.8.1 / MicroPython v1.26.0-77 | 已确认，不升级 |
| 摄像头实时画面 | QVGA，可正常识别画面内人员 | 已验证 |
| OpenMV UART | OpenMV UART3 P4/P5 ↔ STM32 USART3 PB11/PB10，115200 8N1 | 实物联调已通过 |
| ESP32 | ESP32-S3-WROOM-1-N16R8（16 MB Flash / 8 MB PSRAM），COM8 / CH343 | 芯片、Flash、PSRAM、烧录均已实测 |
| ESP32 板载 RGB LED | WS2812B，GPIO38 | 资料已确认，尚未实测 |
| ESP32 UART | ESP32 GPIO8/RX、GPIO14/TX ↔ STM32 USART2 PA2/TX、PA3/RX，115200 8N1；GPIO12 为触摸 INT | 实物已通过；ESP32 持续收到每 500 ms 的 STM32 TEL |
| STM32 | STM32F103ZET6；`firmware/stm32` STM32Cube/HAL 桥接工程 | 旧 Flash/option bytes 已双份备份并哈希一致；两阶段启动版固件已烧录并 verify OK |
| 超声波 | HC-SR04 风格 TRIG/ECHO 模块：TRIG=PC10、ECHO=PC11、5V/GND 共地 | 实物已通过；约 1995 mm 稳定距离、health bit0=1，ESP32 与服务器均收到 |
| 灯带与报警器 | 暂无 | 未采购/未确认 |

## 当前工作边界

OpenMV 到 STM32F103ZET6 的单向通信已验证。当前阶段增加 ESP32 联网网关：

1. STM32 保持通过 USART3 接收 OpenMV 的 VIS 帧并独立执行本地状态判断；
2. STM32 通过 USART2 每 500 ms 向 ESP32 发送一帧 TEL；
3. ESP32 通过 UART1 接收并校验 TEL，连接 Wi-Fi 后上传服务器；
4. ESP32 每秒向 STM32 返回 NET 状态；
5. 断开 ESP32、Wi-Fi 或服务器不得影响 OpenMV 人员灯和后续本地报警。

旧 STM32 Flash 与 option bytes 已分别独立读回两次且哈希一致，两阶段启动版桥接
固件也已烧录并通过片上 verify。纠正传感器 GND 后，STM32 已稳定测得约 1995 mm，
`health=0x9`（超声 bit0 与网络 bit3），ESP32 每 500 ms 收到 TEL，服务器 POST 持续
返回 201 且最新遥测已落库。Wi-Fi 密钥使用本地忽略文件，不写入仓库。

新 STM32 工程的声光报警输出目前只是 no-op 集成接口，不得描述为继电器、蜂鸣器
或灯带已经完成。可恢复备份位于 `firmware/stm32/backups/pre_sensor_bridge_20260814`；
未来若目标开启读保护，仍禁止解除保护或执行会触发 mass erase 的操作。
