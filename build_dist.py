#!/usr/bin/env python3
"""build_dist.py — 生成可导入的 Skill ZIP 包到 dist/。

用法：python3 build_dist.py
产出：dist/maimai-mealcrew.zip （含 SKILL.md、scripts、references、templates、examples）
不打包 __pycache__、本地状态与任何凭证。
"""

import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(ROOT, "skill", "maimai-mealcrew")
DIST_DIR = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST_DIR, "maimai-mealcrew.zip")

EXCLUDE_DIRS = {"__pycache__", "sessions", ".git"}


def main():
    if not os.path.isdir(SKILL_DIR):
        raise SystemExit(f"未找到 Skill 目录：{SKILL_DIR}")
    os.makedirs(DIST_DIR, exist_ok=True)
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(SKILL_DIR):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in files:
                if name.endswith(".pyc"):
                    continue
                full = os.path.join(base, name)
                arc = os.path.relpath(full, os.path.join(ROOT, "skill"))
                z.write(full, arc)
                count += 1
    print(f"[build_dist] 已生成 {OUT}（{count} 个文件）")


if __name__ == "__main__":
    main()
