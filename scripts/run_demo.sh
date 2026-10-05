#!/usr/bin/env bash
# 一键跑 Demo，产出所有图到 out/
set -e
cd "$(dirname "$0")/.."
mkdir -p out
python3 packages/geometry/hkgeometry/demo.py
echo "--- 产出 ---"
ls -la out/*.png
