# 家庭机交接 Prompt · v7.0

```text
接手 amazing-fish/svg-painting-process，推进通用 SVG 绘画过程生成器，当前仅聚焦人物图片。不要重新泛泛调研，也不要拓展风景。

家庭机环境：Windows 11 / PowerShell。
参考图库：C:\Users\Administrator\Desktop\图片\AI。
仓库：https://github.com/amazing-fish/svg-painting-process。
交付分支：bootstrap/portrait-v7。先核实远端 PR、实际 HEAD、AGENTS.md、README 和 docs/delivery-state.md；如已合并，从最新 main 建功能分支，否则检出交付分支复现，不覆盖已有改动。

目标是输出真实矢量笔迹与时间轴，可逐笔重放、定位与导出SVG。禁止原图蒙版渐显、视频生成或截图替代矢量。共享画布上自由叠加和纠形，不预切封闭人体块。整体底形→大明暗→体积冷暖→细节；长笔铺面与短笔点画按用途组合，避免等高线、碎条、无序跳笔。保持当前笔尖前缀增长，不能恢复远端预显。保留简洁播放器和对数播放。

已实现与限制：
- v6 由人工指定20条开放走势及顺序，完整历史源码在 archive/v6。
- v7 auto_guides.py 用低频Lab色面、连通支持场、PCA/骨架自动提取开放轴和笔宽；build_portrait.py 在共享画布生成四阶段；无逐图手工坐标。
- 支持场只供分析，不裁切绘画。Haar正脸检测只作注意提示，不识别人体姿态或衣物遮挡。
- 原插画、Hopper照片、宇航员照片同参数跑通，不能视为本机图库已验收。
- 关键缺口：construction_sequence()有限补画后，即使仍 needsRevisit 也会转移。readableProxy只是连续性等代理，不等于人能看懂；最终MAE不证明过程自然。
- 播放器已完成笔触用Canvas2D Path2D缓存相同SVG路径，当前一笔为SVG；独立SVG导出目前为最终或指定时刻的静帧，完整动画由HTML播放。

先做两项工作：
1. 建立本机真实图库基线。选固定人物样例覆盖实际可用的头像/半身/全身/复杂服饰，用同一参数和SHA256清单，先只生成前两阶段，保留失败项与耗时。初始排序前N仅用于发现，必须人工确认是有代表性的人物样例；之后固定manifest，不逐图调整坐标/笔序。
2. 根据基线，用最小改动修复“形体未立就转移”。区分走势错误、笔宽/方向错误和停止条件错误，消费 needsRevisit 反馈，针对性补主面与转折。不能仅堆笔、无限重试，或靠后期像素修补掩盖粗层问题。人物开放结构提示是否需要加入由失败证据决定。

命令（运行前核对README，依赖建议使用Python3.12）：
git clone https://github.com/amazing-fish/svg-painting-process.git
Set-Location .\svg-painting-process
git switch bootstrap/portrait-v7
py -m pip install -r requirements.txt
py .\tools\gallery_baseline.py --gallery 'C:\Users\Administrator\Desktop\图片\AI' --output .\generated\gallery-v7.0 --limit 5 --list-only
py .\tools\gallery_baseline.py --gallery 'C:\Users\Administrator\Desktop\图片\AI' --output .\generated\gallery-v7.0 --manifest .\generated\gallery-v7.0\manifest-v7.0.json --max-edge 640 --through 2
py -m unittest discover -s tests -v
node .\test_player_v7.js

批量工具输出HTML、SVG、画谱JSON和manifest；--through 4才是完整四阶段。源图库和生成物保留本地，不上传整库。

验收必须看相同样例/参数/原始时间的早期过程，查看第100/140/185/210笔及转移前后，提供连续原始速度片段。笔数只作定位，重点是统一底色主面、宽窄和关键转折是否成立。检查当前笔尖增长、串行时间轴、暂停/恢复、定位、原图切换及对数/原始模式切换。对数映射 t=T*ln(1+99*s/D)/ln(100)，前慢后快，不改变笔序，后期不能保证每笔肉眼可辨。

沿用 execute-github-issue-pr-workflow：核实现有Issue后建立聚焦分支、实现、测试、提交PR、等待审查；新建PR不在同轮自动合并。创建PR后使用 report-for-human 输出简明HTML。交付带版本文件、完整修改代码、前后过程对照与真实限制。开始只汇报当前概况和1～2步，随后直接推进；不要重新规划整条路线。
```
