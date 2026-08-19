# 海岸环境风险模型（历史数据 V1）

这套流水线已经可以完整运行：下载历史天气/海况、对齐逐小时记录、生成可审计的弱标签、按时间切分、训练四级风险分类器、输出评估报告，并把纯 JSON 模型交给 FastAPI 推理。

它预测的是**未来 6 小时环境条件的最高风险等级**：

- `0 safe`
- `1 advisory`
- `2 warning`
- `3 critical`

这不是海啸、风暴潮或航海预报模型。标签来自项目内版本化的波高/风速演示规则，不是事故真值或官方安全阈值。ESP32 的本地确定性规则仍是最终报警路径；模型先以旁路（shadow）方式运行。

## 数据来源

- [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api)：首版默认使用的逐小时气温、湿度和风速重分析数据，批量导出较快且长期一致。
- [Open-Meteo Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api)：可选数据源；格式与实时 Forecast API 一致，更贴近近年实时输入，但免费端点批量导出明显更慢。
- [Open-Meteo Marine Weather API](https://open-meteo.com/en/docs/marine-weather-api)：逐小时浪高、浪周期、海表温度、模式海平面高度和海流速度。

Marine API 官方说明明确指出近岸精度有限，不适合航海；这里仅把它当环境上下文。真正的现场水位仍应来自完成标定后的本地传感器。

## 首轮训练结果

首轮数据为英国 6 个海岸地点在 `2024-01-01` 到 `2025-12-31` 的逐小时记录：

- 弱标签样本：105,228 条
- 切分方式：每个地点按时间 70% / 15% / 15%，边界清除 6 小时，禁止相邻小时随机泄漏
- 模型：带类别权重的多项逻辑回归
- 测试集 Macro-F1：`0.618`
- 测试集高风险合并召回率：`0.831`
- 测试集 critical 召回率：`0.813`
- 当前规则基线 Macro-F1：`0.716`
- 当前规则基线高风险合并召回率：`0.610`
- 当前规则基线 critical 召回率：`0.566`

模型明显提高了严重场景召回，但整体 Macro-F1 和误报控制还没超过规则，所以当前结论不是“模型取代规则”，而是“模型作为高召回旁路候选继续采集真值”。完整混淆矩阵和各类指标见 [coastal_risk_v1_metrics.json](reports/coastal_risk_v1_metrics.json)。

## 运行

项目已有的 `server/.venv` 可以复用：

```powershell
Set-Location 'F:\海岸预警系统'
server\.venv\Scripts\python.exe -m pip install -r ml\requirements.txt
Set-Location ml
```

下载默认的 6 个英国海岸并训练：

```powershell
..\server\.venv\Scripts\python.exe -m coastal_risk.cli run
```

需要严格对齐实时 Forecast 格式时，可以显式增加
`--weather-source historical-forecast`；默认 `archive` 更适合快速、可复现的首版实验。

扩大到全球 16 个预设海岸：

```powershell
..\server\.venv\Scripts\python.exe -m coastal_risk.cli run `
  --locations all `
  --start 2024-01-01 `
  --end 2025-12-31
```

只从现有数据重训，不重新下载：

```powershell
..\server\.venv\Scripts\python.exe -m coastal_risk.cli train
```

原始 API 响应会缓存到 `data/raw/`，对齐后的数据写入 `data/processed/`；两者都被忽略，不会误提交。部署产物是 [coastal_risk_v1.json](../server/models/coastal_risk_v1.json)，服务器只读取数字 JSON，不加载 pickle/joblib。

## 在线推理

本地服务与公网最小网关都提供：

```text
GET /api/v1/risk?device_id=COAST_01
```

公网网关仍要求 `X-Device-Token` 或 Bearer Token。返回值会同时给出环境模型等级、ESP32 本地等级、最终较高等级、数据质量、降级状态和原因码。模型文件缺失或损坏时，服务不会瘫痪，而是明确返回 `model_source: rule-fallback`。

## 下一轮怎样变成真实模型

1. 接入并标定超声波水位，当前占位水位绝不能用于训练。
2. 固化每次遥测对应的环境快照、固件版本、阈值版本与设备地点。
3. 对所有严重事件、误报事件和普通事件抽样人工复核，保存 `label_source`。
4. 以独立实验 session、日期和地点做留出测试；英国现场必须单独验收。
5. 只有模型在严重事件漏报率、每小时误报数和报警延迟上稳定优于规则，才考虑改变旁路状态。
