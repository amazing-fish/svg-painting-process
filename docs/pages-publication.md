# v8.1 完整 HTML 与 Pages 发布

2026-10-27，Asia/Shanghai。用户明确要求完整 HTML 并由本轮直接启用、完成部署，且授权仓库改为公开。仓库已为 PUBLIC，GitHub Pages 已启用 Actions 模式；[网站入口](https://amazing-fish.github.io/svg-painting-process/)尚待首次部署成功，当前不声称已经上线。

## 交付入口与边界

- [v8.1 门户](../artifacts/v8.1/index.html)：64笔完整回放、41/64笔对照、真正v6/64笔对照、静帧、公开画谱及完整源码ZIP。
- [独立回放 HTML](../artifacts/v8.1/replay-v8.1-64.html)：内嵌画谱、授权参考预览与相同实验源码 ZIP，单独下载可离线播放并取出源码。对照页与其SVG资源应连同目录下载。
- [完整实验源码 ZIP](../artifacts/v8.1/source-portrait-coarse-v8.1.zip)：保留实际模块目录，固定于PR #5合并提交 `e393e3cd7473e607b6a6061d141b027b57d73f3d`。生成实验为 `portrait-coarse-v8.1`，数据/播放器兼容版本为7.2；版本含义不能混用。
- [历史 v7.2 门户](../artifacts/v7.2/index.html)：保留历史画谱与匹配源码，不冒充新实验结果。

64笔在59.971秒处保留未完成结果，第16号尾部走势预算耗尽后严格停止，未进入明暗。完整HTML表示交付文件完整，不表示四阶段绘画完成；人物形体、自然度与v6效果基线均未通过。

原始图像、显式 guides/provenance 和绑定的原始候选不在源码 ZIP 内；实验执行须自行提供这些输入并满足原有哈希与结构校验。参考预览不能替代输入证明。完整源码可离线阅读；执行实验时另安装 `requirements.txt` 中的依赖，详见归档内 `SOURCE-README.md` 与 [实验包说明](../experiments/portrait_coarse/README.md)。

## 本机重新打包

在仓库根目录使用 Python 3.12；本机可用 `.\.venv\Scripts\python.exe` 替换下列 `python`。发布工具和静态站点构建器只需要标准库，打包源码需要本地Git及上述固定提交。

以下画谱与对照目录是本机已有的冻结输入，未随仓库克隆提供。已核对原画谱SHA256为 `dd1c2091d7b3d6caf0dd62c9516a001f7d724e618e6061620693e598528e107d`；不要用已脱敏公开JSON替代原画谱，也不要仅为通过检查改写预期哈希。

```powershell
python tools/publish_experiment_artifacts.py `
  --repo . `
  --replay-data generated/head-plane-v9/packaged-replay-002/qa/painting-data.json `
  --delivery generated/head-plane-v9/delivery/source-checked64 `
  --output generated/publication-reproduce-v8.1 `
  --source-ref e393e3cd7473e607b6a6061d141b027b57d73f3d `
  --expected-data-sha256 dd1c2091d7b3d6caf0dd62c9516a001f7d724e618e6061620693e598528e107d
```

输出目录必须是全新路径；重复运行请选择另一个目录。该命令重新生成发布副本及其manifest，不覆盖 `artifacts/v8.1/`，不修改源画谱，也不运行算法。固定对照目录必须同时具有 `old41-vs-new64` 与 `true-v6-vs-new64` 的原始画谱、对照HTML和SVG资源。

## 构建与自动部署

只需仓库内已提交的两个版本产物即可构建站点，不需要本机冻结输入、原图、模型、GitHub token或第三方Python库：

```powershell
python -m unittest discover -s tests -p test_pages_site.py -v
python -S tools/build_pages_site.py --output generated/pages-site-local
```

站点输出也必须为全新目录。根入口是可点击门户，提供最新v8.1与历史v7.2入口；两个版本保持原目录关系。构建仅复制 `artifacts/v8.1/` 和 `artifacts/v7.2/` 中被各自manifest精确覆盖的文件及manifest本身，绝不将整个仓库根目录作为站点上传。原始图库、`generated/`、`reports/`、未跟踪pose代码和诊断缓存均不进入Pages产物。

构建会校验文件数、字节数、SHA256、路径与静态相对链接，拒绝软硬链接；单文件超过50MiB或整站超过250MiB即失败。v8.1另复用发布器校验，保证回放HTML内嵌ZIP与外部ZIP逐字节相同、源码内部manifest有效、HTML内画谱与公开JSON一致；篡改后重算外层manifest仍会失败，不生成空白成功页。

`.github/workflows/pages.yml` 的运行行为：

- PR：运行发布边界测试和完整站点构建校验，不上传站点、不部署，也没有Pages/OIDC写权限。
- main push：构建验证后上传 `_site`，部署到 `github-pages` 环境；工作流自身进入main后即可触发首次运行。
- 手动运行：工作流必须已经存在于默认分支，且运行引用为main才能部署。不能在首次合入前把feature运行当成main部署。

部署job才拥有 `pages: write`、`id-token: write`，并在发布前比对main当前提交；同一生产发布并发会取消旧运行，防止过期产物覆盖。官方Actions固定正式release SHA。Pages模式启用与工作流构建成功都不能单独证明网站可用；首轮仍须核验实际部署、网站页面和源码下载。
