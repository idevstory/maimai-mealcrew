# dist/

本目录存放**发布时可导入的 Skill ZIP 包**。

- 生成方式：在项目根目录运行 `python3 build_dist.py`
- 产出：`maimai-mealcrew.zip`（含 `SKILL.md`、scripts、references、templates、examples）
- 不打包 `__pycache__`、本地状态与任何凭证

> `.gitignore` 已忽略 `dist/*.zip`，避免把构建产物提交进源码仓库；发布时按需生成。
