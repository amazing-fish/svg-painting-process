# SVG Painting Process · v8.1 HTML 交付

[最新完整 HTML 入口](artifacts/v8.1/index.html) · [64 笔完整回放](artifacts/v8.1/replay-v8.1-64.html) · [历史 v7.2](artifacts/v7.2/index.html)

当前仅做人物。**以 v6 原参考图与指定走势为效果基线；v7.2 自动算法仍未达到它。** 不能把能生成文件、代理分数或最终 MAE 当作形体与绘画过程验收通过。

2026-10-27：[PR #5](https://github.com/amazing-fish/svg-painting-process/pull/5) 已由用户合并，源码固定提交为 `e393e3cd7473e607b6a6061d141b027b57d73f3d`。[开放主面实验](docs/portrait-coarse-experiment.md)保留前32笔并推进到64笔，仍在尾部阻塞；默认自动生成器与效果验收状态不因此改变。

## 本次交付

`artifacts/v8.1/` 提供完整的64笔/59.971秒回放、[旧41笔与64笔对照](artifacts/v8.1/old41-vs-new64/index.html)、[真正v6与64笔对照](artifacts/v8.1/true-v6-vs-new64/index.html)，以及[完整实验源码 ZIP](artifacts/v8.1/source-portrait-coarse-v8.1.zip)。独立回放 HTML 内嵌画谱、授权参考预览和相同源码 ZIP，单独下载也能离线播放与取出源码；对照页请连同其目录下载。源码保留真实模块目录，执行需要自备原图、显式 guide/provenance 及绑定的候选输入。64笔在第16号尾部走势严格停止，未进入明暗，不是完整四阶段绘画。

用户已明确授权仓库公开并直接启用部署；仓库当前为 PUBLIC，GitHub Pages 已启用 Actions 模式。[网站入口](https://amazing-fish.github.io/svg-painting-process/)待首次部署成功后可用，本文不把启用当成已经上线。新的 Pages 工作流在 main push 后构建并部署，PR 只构建校验；仅发布两个版本的 manifest 白名单产物。运行方法与发布边界见 [Pages 发布说明](docs/pages-publication.md)。

## 历史 v7.2 交付

- 原参考图确为图库的 `2f22ad27-e747-4500-b975-345352f21ba7.png`，SHA256 `bbeb4acc9f17aed429d0264e28d82e4b9d9e79beeb73b0693d1391b404381f93`。
- 原样复现 v6 三个归档模块，1086×1448、不缩图；前两阶段累计 196/676 笔，总耗时 99.734 秒。其 20 条开放轴和真实访问顺序单独保存，只作基线，未输入自动算法。
- 同图、同原生尺寸比较 v6、v7.1 和 v7.2；逐条显示开放轴、方向、笔宽、访问次序与覆盖。v6 最初三条轴的最近自动匹配覆盖仅 0%/37.5%/0%，当前不能模拟 v6 的初始走势。
- 删除 v7 特有的网格铺色前奏：旧版样例3前73笔都是长宽比约1.317的粗方块，v6没有这段。新版本直接沿自动开放轴落笔；自动轴仍过宽且缺腿部整段走势，本轮未宣称解决。
- v7.2 原生同图实际在底形第13条轴阻塞，保留88笔部分过程，不进入后续精修。局部分支实验也未复原关键走势，因此未混入产品。
- 修复误开 `player_v7.html` 源码模板时的占位解析错误；该文件是模板，不是作品。它现在明确提示并指向产物入口。所有实际打包入口严格填充指定画谱/源码容器，拒绝缺失、重复或错误占位。

## 打开与上传

`artifacts/v7.2/` 保留上一轮实际提交的 HTML、SVG 静帧、测量 JSON 和对应源码 ZIP，克隆或下载后打开其中 `index.html`。GitHub 文件页展示的是源码；要本地播放，请下载目录或仓库后打开 HTML。此前“Pages 尚未部署”的描述属于 v7.2 历史记录；当前启用状态见上文。

门户包含 v6 原生基线、v7.1 原生底形检查点、v7.2 当前失败结果、同原始时间过程对照、20条轴对照，以及前轮5例640基线的失败证据。历史引擎、画谱与播放器版本分别标明，源码链接指向对应引擎，不把旧画谱误标成当前算法生成。

用户已明确要求上传 HTML；因此上传这些选定产物及其内嵌参考预览。原始图库、整库和本地诊断缓存不上传。原5例640结果不能冒充本次1086×1448对照。

## Windows PowerShell

使用 Python 3.12；本机没有 `py` 时用 `.\.venv\Scripts\python.exe`。

```powershell
py -m pip install -r requirements.txt

# v6原样隔离复现，仅适用于上述原参考图，不是通用人物算法。
py .\tools\v6_baseline.py --source 'C:\Users\Administrator\Desktop\图片\AI\2f22ad27-e747-4500-b975-345352f21ba7.png' --output .\generated\v6-baseline-v7.2 --through 2

# 当前自动算法，同原图/原生尺寸请求前两阶段；粗层失败会保留JSON并返回1。
py .\build_portrait.py --source 'C:\Users\Administrator\Desktop\图片\AI\2f22ad27-e747-4500-b975-345352f21ba7.png' --work-dir .\generated\v7.2-native\run --max-edge 1448 --through 2

# 当前同版本画谱打包；不要直接打开源码模板。
py .\tools\render_player.py --data .\generated\v7.2-native\run\qa\painting-data.json --output .\generated\portrait-painting-v7.2.html

py .\tools\export_svg.py --data .\generated\v7.2-native\run\qa\painting-data.json --output .\generated\painting-v7.2.svg
py -m unittest discover -s tests -v
node .\test_player_v7.js
py .\tools\verify_artifact_manifest.py .\artifacts\v7.2\artifact-manifest.json
```

自动抽取不为每张图片写坐标/笔序。所有笔触共用画布、可叠加纠形，支持场不裁切绘画，不使用原图蒙版、视频或截图替代矢量。当前一笔只增长到笔尖；已完成笔用 Canvas2D Path2D 缓存相同SVG路径。

独立 SVG 是静帧，完整过程由 HTML 播放。对数映射 `t=T·ln(1+99s/D)/ln(100)` 保留原笔序；可切原始时间、逐笔定位、暂停/恢复与原图参考。后期对数播放不保证每笔肉眼可辨。

`needsRevisit` 反馈和5笔有界补画继续保留。预算未就绪停止当前及后续阶段；无安全改善明确记录为停滞，不能当作形体成功。`constructionBlocked` 与 `layers[].complete=false` 标记部分产物。

方案与实际限制见 [architecture.md](docs/architecture.md)，当前记录见 [delivery-state.md](docs/delivery-state.md)。
