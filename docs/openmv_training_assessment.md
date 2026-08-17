# OpenMV 资料包训练能力核验

## 结论

本地资料包没有可复现的训练工程：没有数据集、标注、训练脚本、Notebook、模型转换或量化脚本。

资料包实际包含：

- 一个已经训练好的 0-9 数字分类模型：
  `WT-OpenMV资料/2.源码例程/5.数字识别（Tensorflow只适用于openmv4 h7 plus）/trained.tflite`
- 一个只负责加载该模型并推理的 `Num_Detection.py`
- 两个调用固件内置 Haar cascade 的正面人脸检测脚本
- 若干指向在线教程的 TXT 链接
- `2.OpenMV使用手册.pdf` 第 7 页的一句功能介绍：IDE 的数据集编辑器可以采集训练数据，资料称其只适用于 OpenMV4 H7 Plus

因此，目录名虽然写着“人脸识别”，现有代码实际只做“人脸检测”，没有身份注册、特征库或姓名分类。

## 三个不同任务

1. 人脸检测：找到正面脸的位置。当前基线使用固件内置 Haar 模型，不需要我们重新训练。
2. 人体检测：找到完整的人，更符合危险区越界预警。资料包没有人体模型。
3. 人脸身份识别：判断具体是谁。需要采集授权样本、训练或注册特征库；这不是当前预警 MVP 的必要能力。

## 后续模型路线

确认 OpenMV 型号和固件后再选：

- 资源较弱或旧型号：先用 Haar/颜色目标完成视觉链路。部分型号理论上可用 LBP 描述子做受控环境下的实验性相似度匹配，但这不是训练好的人脸身份识别模型；本地资料也没有 LBP 示例、描述子或人员库，必须按板型和固件实测。
- 新版官方工具链提供按板型筛选的模型库，可优先评估预训练 person detection，避免从零训练。但该路线引用的是新版/开发版文档，本地只有 IDE 4.7.0 与 firmware 4.7-4.8.1，升级前必须核对板型、固件、模型算子和内存兼容性。
- 必须自定义场景时，可评估新版 OpenMV IDE 数据集编辑器：采集真实安装视角的数据，上传 Edge Impulse 或其他训练平台，导出量化 `.tflite`。这同样是外部/新版路线，部署前必须完成板型兼容、速度、RAM 和准确率实测。

官方参考：

- https://docs.openmv.io/v4.8.1/library/omv.image.html
- https://docs.openmv.io/dev/openmvcam/tutorial/tools/ide/dataset-editor.html
- https://docs.openmv.io/dev/openmvcam/tutorial/tools/ide/model-zoo.html
- https://docs.openmv.io/dev/library/omv.ml.html

## 当前决定

板卡实测确认固件 ROM 内置 `person_detect.tflite`，单次全画面推理约
20 FPS。第一阶段代码默认使用 `person_classifier`：

- 不训练即可验证摄像头、人体存在判断、去抖和 VIS 协议；
- 整个摄像机画面定义为警戒区域，有人即 `in_zone=1`；
- 模型不提供人体框，因此 VIS 坐标明确输出 `cx=0, cy=0`；
- 保留 Haar 和颜色标记后端作为诊断备选，但不再作为默认模式；
- 若后续必须获得真实人体位置，再采集现场数据训练 FOMO 人体定位模型。
