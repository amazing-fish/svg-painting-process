# SVG Painting Process · v7.0 实验版

目标是把人物图片转换为可检查、可逐笔回放的 SVG 绘画过程。先让底形和大明暗成立，再塑造体积与细节；最终相似度和绘画过程自然度分别验收。

v7.0 已用图像算法替代 v6 针对单张插画手写的 20 条走势：低频色面分析 → 开放主轴与宽度 → 邻近访问顺序 → 四阶段绘制。所有笔触共用画布，分析色面不会成为封闭绘画遮罩。当前实现是探索基线，尚不能理解人体解剖、衣物结构或遮挡关系。

## Windows PowerShell 快速开始

```powershell
git clone https://github.com/amazing-fish/svg-painting-process.git
Set-Location .\svg-painting-process
# 初次交付仍在 PR 中时，先检出实际交付分支；先核实 PR 状态。
git switch bootstrap/portrait-v7
py -m pip install -r requirements.txt

# 生成四阶段、笔迹数据和自动走势。
py .\build_portrait.py --source 'C:\Users\Administrator\Desktop\图片\AI\人物.jpg' --work-dir .\generated\person --max-edge 768 --through 4

# 打包离线播放器；页面可下载内嵌的完整单文件生成代码。
py .\tools\render_player.py --data .\generated\person\qa\painting-data.json --output .\generated\person-painting-v7.0.html

# 导出最终静态 SVG；可加 --time-ms 导出原始时间轴指定帧。
py .\tools\export_svg.py --data .\generated\person\qa\painting-data.json --output .\generated\person-painting-v7.0.svg
```

将 `人物.jpg` 换成真实文件名。首次家庭机图库验收尚未执行；请先固定选样清单与参数，再作同条件比较：

```powershell
# 按相对路径排序选前 5 张，仅生成带 SHA256 的固定清单；不会识别图片内容。
py .\tools\gallery_baseline.py --gallery 'C:\Users\Administrator\Desktop\图片\AI' --output .\generated\gallery-v7.0 --limit 5 --list-only

# 复用同一清单，先检查前两阶段；完整四阶段使用 --through 4。
py .\tools\gallery_baseline.py --gallery 'C:\Users\Administrator\Desktop\图片\AI' --output .\generated\gallery-v7.0 --manifest .\generated\gallery-v7.0\manifest-v7.0.json --max-edge 640 --through 2
```

批量入口输出 HTML、SVG 与运行清单。排序选样不保证全是人物；家庭机须先确认清单是否适合作为人物测试集，再固定后续比较样本。可手动整理清单中的代表样本，保留对应文件相对路径和 SHA256 后用 `--manifest` 复跑。图库图片和生成物默认不进 Git。

## 播放器

默认界面只留画布、原图切换、播放／暂停、进度、倍速与版本。时间方式、总览时长和源码下载收进“选项”。

对数播放保持笔序不变，将原始画谱时间 `s` 映射成展示时间 `t`：

```text
t = T · ln(1 + 99s/D) / ln(100)
```

`D` 是原始总时长，`T` 默认 240 秒。前段慢看铺形，后段逐渐加速；后期一帧可能完成多笔。需要逐笔观察时切换原始时间或使用方向键。切换时间模式保留当前画面。

笔迹数据保存完整 SVG 路径。为控制大量 DOM 节点，已完成笔触由 Canvas2D `Path2D` 缓存相同 SVG 几何，当前笔触用真实增长的 SVG 几何显示。这是混合回放渲染，不是原图蒙版显露。

## 验证与已知差距

同一算法和参数已在原插画、Grace Hopper 人物照、astronaut 人物照三张样例上跑通，无逐图人工走势坐标。这三张样例不是用户本机图库验收，也不足以证明任意人物图片都能自然绘制。

当前关键缺口：粗形检查未通过时，调度器只追加一次有限补画，之后仍可能换走势。因此仍可能出现“形体尚不可辨就离开”。下一任务优先闭合这项调度反馈，再考虑人物结构提示。最终像素相似度不能替代早期过程验收。

没有生成模型或远程模型服务依赖；OpenCV 内置 Haar 正脸分类器属于本地预训练模型，仅作注意提示，不是人体结构识别。

```powershell
node .\test_player_v7.js
py -m compileall .\auto_guides.py .\build_portrait.py .\global_plan.py .\construction_episodes.py
```

播放器逻辑测试使用模拟 DOM／Canvas；真实浏览器验证必须单独记录版本与证据，不能由测试数量推断帧率。

方案见 [architecture.md](docs/architecture.md)，后续工作见 [roadmap.md](docs/roadmap.md)，交付状态见 [delivery-state.md](docs/delivery-state.md)。
