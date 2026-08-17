# STM32F103ZET6 clock audit

The new firmware copies the clock assumptions from the board vendor's CubeMX
project instead of guessing them.

## Audited source

Vendor project:

```text
STM32F103ZET6核心板带串口 客户资料/
  5、库函数例程/例1 流水灯实验/ZET6.ioc
```

Relevant facts in that project:

| Item | Vendor setting |
|---|---|
| MCU | `STM32F103ZET6` |
| Package | `LQFP144` |
| Clock input | HSE external oscillator |
| HSE value | `8,000,000 Hz` in `Core/Inc/stm32f1xx_hal_conf.h` |
| PLL | HSE × 9 |
| SYSCLK / HCLK | 72 MHz |
| APB1 | 36 MHz, timer clock 72 MHz |
| APB2 | 72 MHz |
| Debug | SWD on PA13/PA14 |

## Firmware implementation

`src/main.cpp` reproduces HSE 8 MHz → PLL ×9 → 72 MHz, with APB1 divided by
two and APB2 undivided. TIM2 therefore receives 72 MHz and uses prescaler 71
for a 1 MHz free-running counter. That counter measures the HC-SR04 ECHO pulse
in microseconds.

The project also passes `HSE_VALUE=8000000UL` in `platformio.ini`. A failed HSE
or PLL setup enters `error_handler()` and does not continue with falsely timed
sensor values.

This is a configuration audit, not an oscilloscope measurement of the physical
crystal. The first hardware run should still compare a known target distance
with the reported value before collecting labelled data.

