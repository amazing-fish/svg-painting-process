# 交付状态

快照日期：2026-10-26（Asia/Shanghai）。本文件用于恢复工作；GitHub 当前 HEAD、PR、CI 和审查须重新核实。

- 仓库：`amazing-fish/svg-painting-process`，私有。
- 已合并起点：PR [#2](https://github.com/amazing-fish/svg-painting-process/pull/2)，`main` 为 `17651f354d38624c93e3394e61e7198a08d4b4f5`；原导入分支 `bootstrap/portrait-v7` 已不作为本轮工作分支。
- 当前工作：Issue [#3](https://github.com/amazing-fish/svg-painting-process/issues/3)，保持开放；功能分支 `fix/portrait-v7-revisit-baseline`，版本 v7.1。
- 本轮 PR、提交与远端审查：PR [#4](https://github.com/amazing-fish/svg-painting-process/pull/4) 已创建并保持开放；实现提交 [40697d7](https://github.com/amazing-fish/svg-painting-process/commit/40697d75bdd5f49eb7e1e9a32e74ef8641029496)。远端 CI/Codex 正在等待当前 PR HEAD 的结果，当前 HEAD 须以远端刷新为准。 新 PR 不在创建当轮合并。

## 本地基线与输出

已读取 `C:\Users\Administrator\Desktop\图片\AI` 并检查实际内容，固定 5 张人物插画，包含近景头像、半身、坐姿全身复杂服饰、双人、动态人物。样例不足处：没有照片人物，中性站姿全身未覆盖。固定清单保留路径、SHA256 和选样依据；不逐图调坐标、笔序或参数。

- 原版：本地 `generated/gallery-v7.0/manifest-v7.0.json`，来自上述 `main`，统一 `maxEdge=640`、`through=2`，HTML、SVG、JSON、失败项与耗时保留。
- 修改版：本地 `generated/gallery-v7.1/manifest-v7.1.json`，复用同一清单和参数。5 项均保留产物：1 项跑完请求的两阶段，4 项因粗层预算阻塞提前停止；批次按契约退出 1。原版总耗时 83.043 秒，修改版 126.050 秒，提前停止使两版全批次耗时不能当作等工作量性能比较。聚合结果见 `evidence/gallery-v7.1-summary.json`。
- 对照：`tools/compare_baseline.py` 生成本地 `generated/comparison-v7.1/`，输出固定第 100／140／185／210 笔 SVG、转移／停止附近同原始时间的连续 1× SVG 回放。笔数不足、共同时间不足、阻塞后没有下一转移均明确披露；实际可用片段以 `comparison-evidence.json` 为准。
- 原版 HTML 保留；v7.1 打包器拒绝将旧画谱与当前生成代码误配。用户图库、完整画谱、大生成物和人工 HTML 报告不提交业务仓库。

## v7.1 行为与限制

`needsRevisit` 报告传入同一开放走势补画；按残差选择主面宽度或转折侧向。初始及补画候选先预演，要求粗层 MAE、站点就绪、连续性和对比对应不退。补画最多 5 笔，没有无限重试或后期细笔补像素替代粗层判断。

预算耗尽而未达到代理条件时标记 `blocked_unresolved`，导出 `constructionBlocked`、`layers[].complete=false`，停止当前阶段余下走势、精修和后续阶段。批量清单保存 `incomplete` 和已有 HTML／SVG／JSON 并返回 1，这是可检查的未完成结果，不是崩溃或成功。当前轴没有安全粗层改善时可记录 `stalled_unresolved` 后转移；它仍是未完成，不是语义成功。

自动色面轴仍不等于人体结构理解，本次没有引入姿态或衣物模型。`readableProxy`、最终 MAE 与程序测试不能证明形体可读或作画自然；模拟 DOM 检查也不等于真实浏览器或帧率验证。预算阻塞意味着请求的阶段可能没有全部完成，不能把现有末帧称为完整四阶段作品。

## 验证记录

本轮必要检查为 Python unittest、相关语法检查、`node test_player_v7.js`，并分别核对真实浏览器交互和原始速度片段。本轮 `python -m unittest discover -s tests -v` 23 项通过，`node test_player_v7.js` 14,794 断言通过，相关 compileall 通过。默认系统临时目录曾拒绝写入，原测试在授权环境重跑通过，未削弱断言。完整单文件生成器实跑半身例，退出 1 并保留 HTML，92 笔几何和时序与模块版逐项相同。真实 Chrome 验证记录在本地 `generated/comparison-v7.1/browser-qa/`，Chrome 156.0.8078.47 后台实测通过：当前笔前缀、串行时间、暂停/恢复、定位、原图切换、原始/对数模式保持当前时间；5 组原始 1× 连续片段。5 例在 1280/390 两视口无横向溢出且画布顶边对齐，未做帧率基准。摘要见 `evidence/browser-v7.1-checks.json`。 历史 `evidence/` 中三张样例及旧测试数量不是本轮图库验收证据。

本轮不部署 GitHub Pages，不上传图库。后续发布先审查拟公开 HTML 的内容，因为页面可能内嵌参考图、画谱和生成代码；发布状态与本次本地验收分别记录。

## 固定样例结果

同一清单与参数，以下为实际耗时。`incomplete` 的阶段/笔数是已保留的部分过程，不能与原版完整两阶段直接比数量。

| 样例 | v7.0 秒 / 笔 | v7.1 秒 / 笔 | v7.1 结果 |
| --- | --- | --- | --- |
| 头像 | 16.804 / 676 | 10.281 / 198 | 底形阶段阻塞，guide 31 |
| 半身 | 11.153 / 324 | 3.915 / 92 | 底形阶段阻塞，guide 7 |
| 坐姿全身复杂服饰 | 15.120 / 658 | 35.228 / 568 | 请求的两阶段跑完，仍有 15 条未完成剖面 |
| 双人 | 25.291 / 1227 | 41.016 / 269 | 底形阶段阻塞，guide 30 |
| 动态人物 | 14.675 / 896 | 35.610 / 352 | 明暗阶段阻塞，guide 25 |

本次目视检查不通过自然度验收：头像与双人的关键关系仍不稳定，半身形体不可辨，复杂服饰仍有细碎笔迹。程序记录的粗层退化接受笔从原版 454 笔降为本次已执行范围内 0 笔，但修改版提前停止、覆盖不同，不能据此宣称自然度或泛化成功。第 100/140/185/210 笔和原始时间不足项已在对照页明确标记。

失败分类：旧调度把预算耗尽当成继续换轴的条件，现已阻断；笔宽/落笔侧向通过同轴粗层反馈修复。半身首个预算阻塞轴是画布边缘的窄片段（最大半径约 7.58 像素，而粗层评估 sigma=16），显示走势/宽度与粗层尺度仍不匹配；这尚不能证明必须加入姿态模型。本轮不加入人物结构模型，保留上述失败证据供后续同清单修复。

本轮最终人工决策报告位于本地 `reports/explain-diff-pr-4-v7.1.html`，不提交仓库；固定 base/head、审查阶段与结论以该报告及本地 `generated/review-cycle-v7.1.json` 为准。
