# SVG Painting Process · v7.1 实验版

将人物图片转换为真实矢量笔迹和串行时间轴，可逐笔重放、定位并导出 SVG 静帧。先建立底形和大明暗，再塑造体积与细节；最终相似度与绘画过程自然度分别验收。

v7 从低频 Lab 色面、连通支持场、PCA／骨架提取开放走势和笔宽，无逐图手写坐标。所有笔触共用画布，支持场只作分析，不裁切落笔。当前算法不理解人体解剖、衣物结构或遮挡。

v7.1 让未完成走势的反馈参与补画：按当前残差选择主面宽度或转折侧向，候选笔必须不降低粗层剖面、连续性与对比对应。补画仍最多 5 笔；预算耗尽且未达代理条件会停止当前阶段和后续阶段，保存未完成结果，不继续精修掩盖。明确没有安全粗层改善时可记录 `stalled_unresolved` 后转移；这不代表形体已被人识别。

## Windows PowerShell 快速开始

先核实远端状态：导入 PR [#2](https://github.com/amazing-fish/svg-painting-process/pull/2) 已合并；本次工作为 Issue [#3](https://github.com/amazing-fish/svg-painting-process/issues/3)，开发分支为 `fix/portrait-v7-revisit-baseline`。

```powershell
git clone https://github.com/amazing-fish/svg-painting-process.git
Set-Location .\svg-painting-process
git switch fix/portrait-v7-revisit-baseline
py -m pip install -r requirements.txt

# 请求最多四阶段；粗层预算耗尽会提前停止并返回退出码 1。
py .\build_portrait.py --source 'C:\Users\Administrator\Desktop\图片\AI\人物.jpg' --work-dir .\generated\person --max-edge 768 --through 4

# 同版本画谱可打包离线播放器，并下载内嵌的完整单文件生成代码。
py .\tools\render_player.py --data .\generated\person\qa\painting-data.json --output .\generated\person-painting-v7.1.html

# 导出当前结果的静态 SVG；可加 --time-ms 指定原始时间。
py .\tools\export_svg.py --data .\generated\person\qa\painting-data.json --output .\generated\person-painting-v7.1.svg
```

建议使用 Python 3.12。若没有 `py`，换成 Python 3.12 的解释器路径；本轮家庭机使用 `.\.venv\Scripts\python.exe`。将 `人物.jpg` 换成实际文件。

`painting-data.json` 的 `constructionBlocked` 记录预算阻塞，`layers[].complete=false` 表示当前阶段未完成。返回 1 时先读状态和已有画谱；它可能是有意停止，并非程序崩溃。`complete=true` 仅表示该阶段调度已结束，仍须检查 `stalled_unresolved`，不能当作自然度验收。

## 固定图库基线与对照

家庭机已检查本地图库并固定 5 张人物插画，覆盖近景头像、半身、坐姿全身复杂服饰、双人及动态人物。它们不覆盖照片人像或中性站姿全身，选样与自然度验收分开记录。原版 v7.0 清单保存在本地 `generated/gallery-v7.0/manifest-v7.0.json`，统一 `max-edge=640`、`through=2`。

```powershell
# 新建基线时先发现候选；排序前 N 不识别内容，须人工确认后再固定清单。
py .\tools\gallery_baseline.py --gallery 'C:\Users\Administrator\Desktop\图片\AI' --output .\generated\discovery-v7.1 --limit 5 --list-only

# 复用已经确认的原版 SHA256 清单，同样只请求前两阶段。
py .\tools\gallery_baseline.py --gallery 'C:\Users\Administrator\Desktop\图片\AI' --output .\generated\gallery-v7.1 --manifest .\generated\gallery-v7.0\manifest-v7.0.json --max-edge 640 --through 2

py .\tools\compare_baseline.py --before .\generated\gallery-v7.0\manifest-v7.0.json --after .\generated\gallery-v7.1\manifest-v7.1.json --output .\generated\comparison-v7.1
```

清单固定输入路径、SHA256、选样依据和参数，不逐图调坐标或笔序。批量入口保留耗时、失败及未完成项；`incomplete` 仍保存 HTML、SVG、JSON，并使批次返回 1。其他机器须先建立自己的人工确认清单，不能假定上述本地文件存在。原版 HTML 保留；v7.1 打包器拒绝把旧版画谱与当前生成代码错误组合。

对照页输出第 100／140／185／210 笔的 SVG 静帧、未完成转移或停止附近的连续原始 1× 矢量片段，并保持两版相同原始时间。笔数不足、共同时间不足、阻塞后没有下一转移时明确标为不可用；不补造定位或片段。笔数仅作定位，重点仍是底色主面、宽窄与关键转折。图库和生成物留在本地。

## 播放器与导出

简洁界面保留画布、原图切换、播放／暂停、进度、倍速与版本。时间方式、总览时长和源码下载收进“选项”。对数播放将原始画谱时间 `s` 映射成展示时间 `t`：

```text
t = T · ln(1 + 99s/D) / ln(100)
```

`D` 是原始总时长，`T` 默认 240 秒。前段慢、后段快，笔序和几何不变；后期一帧可能完成多笔。切换原始／对数模式保留当前画面，逐笔检查可用方向键。

已完成笔触用 Canvas2D `Path2D` 缓存相同 SVG 路径，当前一笔用 SVG 只增长到笔尖。没有原图蒙版显露，也不以视频或截图替代矢量。独立 SVG 导出是最终或指定时间静帧；完整动画由 HTML 播放。未完成画谱的末帧不能称为完整四阶段结果。

## 验证与限制

```powershell
py -m unittest discover -s tests -v
node .\test_player_v7.js
py -m compileall .\auto_guides.py .\build_portrait.py .\global_plan.py .\construction_episodes.py
```

自动测试验证反馈、停止与输出契约；模拟 DOM／Canvas 不能证明真实浏览器帧率。`readableProxy` 只测剖面对应和连续性，最终 MAE 只测像素差，均不能证明人物形体可读或作画自然。OpenCV 内置 Haar 正脸检测仅作注意提示，不识别人体姿态、衣物或遮挡；本次未引入人物结构模型。

本轮不部署 GitHub Pages，也不上传图库。后续发布前需审查拟发布 HTML 的内容；离线页面可能内嵌参考图和完整画谱。

方案见 [architecture.md](docs/architecture.md)，当前范围见 [roadmap.md](docs/roadmap.md)，执行结果与 PR 状态见 [delivery-state.md](docs/delivery-state.md)。
