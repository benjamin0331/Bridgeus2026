#!/bin/sh
# Copy the nuclear-energy node classifier's weight files (model.safetensors,
# ~391MB each, 7 total) from the training project's output directory into
# this repo's ml_models/ tree. Run this ON THE SERVER where both the training
# project output and a checkout of this repo are available — it does plain
# local file copies, no network transfer.
#
# See backend/apps/matching/ml_models/nuclear_node_model/README.md for why
# these files aren't committed to git.
#
# Usage:
#   ./scripts/copy_nuclear_weights.sh [source_dir]
#
# source_dir defaults to the training project's output directory found on
# the dev server. Override it if your checkout is elsewhere:
#   ./scripts/copy_nuclear_weights.sh /path/to/test_for_2_label_pytorch_train_30_restart

set -eu

SOURCE_DIR="${1:-/srv/BridgeUs_dev_server/nuclear/test_for_2_label_pytorch_train_30_restart}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="$SCRIPT_DIR/../apps/matching/ml_models/nuclear_node_model"

MODEL_DIRS="model_macro model_micro_0 model_micro_1 model_micro_2 model_micro_3 model_micro_4 model_micro_5"

if [ ! -d "$SOURCE_DIR" ]; then
  echo "[錯誤] 找不到來源目錄：$SOURCE_DIR"
  echo "       請確認訓練專案的實際路徑，或用第一個參數指定："
  echo "       ./scripts/copy_nuclear_weights.sh /實際路徑"
  exit 1
fi

echo "來源：$SOURCE_DIR"
echo "目的：$DEST_DIR"
echo ""

missing=0
for name in $MODEL_DIRS; do
  src="$SOURCE_DIR/$name/model.safetensors"
  if [ ! -f "$src" ]; then
    echo "[缺少] $src"
    missing=1
  fi
done

if [ "$missing" = "1" ]; then
  echo ""
  echo "[錯誤] 上面列出的檔案在來源目錄裡找不到，先確認訓練專案裡每個子目錄"
  echo "       的名稱是否跟 $DEST_DIR 底下的一致（model_macro / model_micro_0..5）。"
  exit 1
fi

for name in $MODEL_DIRS; do
  src="$SOURCE_DIR/$name/model.safetensors"
  dest="$DEST_DIR/$name/model.safetensors"
  cp "$src" "$dest"
  echo "[完成] $name  ($(du -h "$dest" | cut -f1))"
done

echo ""
echo "=== 全部複製完成 ==="
echo "驗證：cd backend && uv run python -c \"from apps.matching.services import nuclear_node_classifier as c; print(c.classify('核四延役有沒有可能通過安全審查'))\""
