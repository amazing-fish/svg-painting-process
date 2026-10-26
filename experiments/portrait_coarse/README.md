# 显式人物粗层实验 · portrait-coarse-v8.1

这是给定开放轴后的受控画谱实验。它不下载模型、不自动提轴、不提供语义验收；根目录 `build_portrait.py` 的默认行为保持不变。所有候选在同一画布上完整落笔，没有人体区域裁切或原图显露。

在仓库根目录使用现有 `requirements.txt` 环境运行：

```powershell
python -m experiments.portrait_coarse --source image.png --guides inputs/guides.json --provenance inputs/provenance.json --output generated/coarse-v8.1-run --through 2
```

`--output` 必须不存在；`--through` 为 1 或 2。输入错误返回 2；任一未完成走势阻塞时保存部分结果并返回 1；请求阶段完成返回 0，也不代表人物或自然度验收通过。输出包括 `qa/painting-data.json`、各阶段画谱和 SVG、停止时画布、候选轨迹及摘要。画谱 `version=7.2` 表示现有播放器数据格式；真实实验标识与输入 SHA 在 `experiment` 中。打包源码时必须包含本目录及其根模块依赖，不可把默认生成器源码当作本实验实现。

输入契约：

- `guides.json` 包含 `metadata`、`candidates`、`gestures`。`metadata.sourceSHA256` 绑定原始图像文件，`sourceSize` 是 EXIF 方向修正后的原生尺寸，`manualGuidesUsed=false`，`guideOrder` 必须是全部候选的显式排列。每条 `gestures[i]` 必须精确等于 `[candidates[i].points,candidates[i].radii]`。
- `provenance.json` 包含 `inputSHA256`（整个 guides 文件 SHA256）和 `bindings` 对象。未准备的原有轴仍受整个 bundle 的 SHA 约束，按既有重采样和 `pixelCenterRefit` 元数据执行。
- `preparedGeometry` 只接受 `attention-measured-prepared-geometry-v1`：匹配源 SHA、`observationDomain=model-attention`、`sourceReady=false`、`noFurtherSmoothing=true`、匹配最终几何摘要与 `inputSourceFileSHA256`。`bindings[finalGeometrySHA256]` 必须提供原始候选文件的 `path`、`sha256`、`observationDomain=model-attention` 与 `sourceReady=false`。相对路径相对于 provenance 文件所在目录解析。
- 原始候选文件的源 SHA 必须相同，`name/person/attention` 唯一匹配，保留顶点的重采样与减压端帽必须精确重构冻结摘要。没有原始绑定时拒绝运行，不反推支持宽度。
- `geometryReady` 只接受 `continuous-source-band-final-geometry-v1`，并重新用当前源图验证输入带宽。实际候选在 C1 插值、载色偏移和收尖全部完成之后，再沿唯一最终笔带的法向逐站重测源支持；失败整笔拒绝，不复用原站点证书给新曲线背书。带有输入证书的半径仅作为保守上限，不反转端帽扩大支持域。

窄范围行为：只有 attention 契约的 stage0 初始主面先预演原始完整笔带。白区泄漏超过 `max(0.12, 当前画布泄漏)` 时，才尝试同模板 `fitLoad=True`；仍不安全则整笔拒绝。安全原始候选原样保留，原始候选不存在时不展开替代搜索。安全判断每次读取当前画布，只有源几何可以缓存。能量收益、就绪阈值及 14+5 笔预算未改。选择、实际 `fitLoad` 和拒绝原因保存在 construction reports 与 proposal trace 中。

`energy.py`、`physical.py`、`canonical.py` 与 `contracts.py` 归属此实验，保留验证过的独立物理几何和连续能量语义；`construction.py` 仅保留实验调度，剖面、采样、旧候选和基础绘画复用根模块。`runner.py` 用有界、可恢复的适配调用这些模块。没有运行时 `generated/` 源码依赖、硬编码样例 SHA 或人工轴后备。空白或无法测量的源信号记录为 unknown/unsupported 并阻塞，不能以零误差宣称空成功。

测试：

```powershell
python -m unittest discover -s tests -p test_portrait_coarse_experiment.py -v
python -m compileall experiments/portrait_coarse tests/test_portrait_coarse_experiment.py
```

合成测试覆盖整笔预演、同模板安全拟合、拒绝、当前画布改变后重评、原始 provenance、几何一致性、输入站点成立但插值跨过无源支持间隙的拒绝，以及严格停止；不替代真实图像、早期笔序或人工视觉验收。
