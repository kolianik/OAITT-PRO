#!/bin/bash
# OAITT-PRO Model Preparation Script
set -euo pipefail

echo "Preparing OAITT-PRO Environment..."

# Ensure GigaAM submodule is cloned
if [ ! -d "vendor/gigaam/gigaam" ]; then
    echo "Cloning GigaAM submodule..."
    git clone https://github.com/salute-developers/GigaAM.git vendor/gigaam
fi

# Run python downloader
python3 prepare.py
