# 接线：当前单板与三板回滚

本文记录当前已启用的 ESP32-S3 单主控接线，以及可回滚的历史三板接线。
所有 UART 均为 `115200, 8N1` 和 3.3 V TTL。

## 2026-08-18 迁移状态

当前单板接线为 `OpenMV P4/TX → GPIO8`、`TRIG → GPIO10`、
`ECHO → 5V-to-3.3V 分压 → GPIO40`。GPIO42 已由 LCD D/C 占用，不能复用。
ESP32-S3 没有耐 5 V GPIO；ECHO 现已通过 2×220Ω 上臂和 3×220Ω 下臂分压，
禁止绕过该分压直接接 ESP32。
完整切换步骤见 `docs/ESP32_SINGLE_BOARD_MIGRATION.md`。

以下章节是可运行的历史回滚接线，不代表当前实物路径。

## OpenMV 到 STM32

```text
OpenMV P4 / UART3 TX  ──▶  STM32 PB11 / USART3 RX
OpenMV P5 / UART3 RX  ◀──  STM32 PB10 / USART3 TX（当前可不接）
OpenMV GND             ───  STM32 GND
```

## STM32 到 ESP32

```text
STM32 PA2 / USART2 TX  ──▶  ESP32 GPIO8 / UART1 RX
STM32 PA3 / USART2 RX  ◀──  ESP32 GPIO14 / UART1 TX
STM32 GND              ───  ESP32 GND
```

ESP32 的 UART 通过 GPIO Matrix 映射到 GPIO8/14。这两个脚避开了：

- GPIO43/44 的下载和调试串口；
- GPIO19/20 的原生 USB；
- GPIO35/36/37 的 Octal PSRAM；
- GPIO38 的板载 WS2812B；
- 当前 TK043F1509 800×480 并口屏和 FT5x06 I2C 触摸的现有占用。

GPIO12 已由屏幕转接板硬接为触摸中断，禁止再接 STM32 TX。当前固件和
实物统一使用 GPIO8 作为 ESP32 UART1 RX。

## TRIG/ECHO 超声波到 STM32

```text
超声波 VCC       ───  STM32 5V
超声波 GND       ───  STM32 GND
超声波 TRI/TRIG  ──▶  STM32 PC10 / GPIO 输出
超声波 ECH/ECHO  ──▶  STM32 PC11 / EXTI 输入
```

PC10/PC11 不占用 OpenMV 的 PB10/PB11，也不占用 ESP32 链路的 PA2/PA3。
STM32F103ZE 数据手册把 PC11 标为 5 V tolerant；当前直接接线仅适用于常规
ECHO 不超过 5 V 的模块，并要求 STM32 已正常供电和共地。永久安装仍建议增加
电阻分压或电平转换。不要把 ECHO 直接移动到未经核对的其他 GPIO。

对应回滚驱动位于 `firmware/stm32`：PC10 产生 10 us 触发脉冲，PC11 双沿中断
配合 1 MHz TIM2 计算脉宽。该历史固件已构建并烧录验证，但当前正常路径不使用它。

## 供电

- 首次联调让三块板分别从各自 USB 或稳定 5 V 输入供电，只连接信号和公共地。
- 不要并接多个模块的 5 V 输出，也不要从 STM32 3.3 V 引脚给 ESP32 供电。
- 除明确核对为 5 V tolerant 的 PC11 ECHO 输入外，不得向 STM32、OpenMV 或
  ESP32 GPIO 输入 5 V；即使是 PC11，永久安装也优先使用分压或电平转换。
- ESP32 首次烧录和看日志使用开发板上标注 `UART` 或 `USB-to-UART` 的 USB-C 口。

## 首次联调顺序

1. 保持已验证的 OpenMV 到 STM32 接线不动。
2. 只用 USB 给 ESP32 供电并烧录网关程序，确认启动日志。
3. 断电后连接 PA2、PA3、PC10、PC11 和公共 GND，再给各板正确供电。
4. 用 ST-Link 读回并保存原 STM32 Flash、option bytes 和 SHA-256；读回失败时停止，
   不得解除读保护。
5. 烧录并复位后，ESP32 日志应每 500 ms 收到一帧 `TEL`；物体靠近探头时
   `distance_mm` 下降、`water_rise_mm` 上升，人员进出画面时 `person` 随之变化。
6. ESP32 每秒返回一帧 `NET`；没有配置 Wi-Fi 时应稳定返回离线状态，而不是阻塞。
7. 拔掉 ESP32 或关闭 Wi-Fi，STM32 的传感器处理和本地状态计算必须继续运行。
