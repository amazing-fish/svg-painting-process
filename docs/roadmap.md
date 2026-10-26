# 人物生成当前范围

当前主线为 [Issue #3](https://github.com/amazing-fish/svg-painting-process/issues/3)。[PR #5](https://github.com/amazing-fish/svg-painting-process/pull/5) 已由用户合并至 `e393e3cd7473e607b6a6061d141b027b57d73f3d`；本轮完整 HTML 发布分支为 `fix/publish-complete-html-pages`。

后续[开放主面实验](portrait-coarse-experiment.md)已把后上发的阻塞定位到初始笔载色。条件策略及最终源支持重测保留前32笔，后上发通过原数值阈值，最新64笔在最后尾部走势停止。先交付可复现的显式实验入口与验证，再继续尾部主面和多图形体；不把局部ready当成人物效果完成。

以v6原参考图/20条指定开放走势为效果基线；先看同原图、同原生尺寸、同原始时间的实际过程。v7.2删除了粗方块网格前奏，但自动提轴仍缺关键腿部走势，尚未达到v6。现有局部分支试验未达标，不进入产品。

本轮交付 [v8.1 完整 HTML 门户](../artifacts/v8.1/index.html)、64笔完整回放、旧41笔/新64笔与真正v6/新64笔对照，以及对应完整实验源码 ZIP；[v7.2](../artifacts/v7.2/index.html) 保留为历史入口。用户已授权仓库公开并直接部署，Pages Actions 模式已启用，待首次 main 部署成功后核验网站与下载；流程见 [发布说明](pages-publication.md)。

绘画后续继续聚焦尾部未完成主面、多图形体、缺失的长腿开放走势、合并场宽度与访问次序，不因 HTML 可发布而宣布算法完成，不扩展风景。人物结构提示由具体失败证据决定。

原五例640清单继续冻结作泛化失败集，不能替代原生v6基线。不得为验收图写新坐标/笔序，不以最终像素精修替代粗层可读性。此前“Pages 尚未部署”属于旧轮次状态；启用发布机制不等于首轮已经上线。
