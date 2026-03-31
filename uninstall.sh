#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# FORGE CV Align — Uninstaller
#
# Removes the hook, pip package, config, and optionally the conda env.
#
# Usage:
#   bash uninstall.sh
#   bash uninstall.sh --env forgeTest
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

HOOK_NAME="forge_cv_align"
CONFIG_DIR="$HOME/.forge"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
SHARED_PYTHON_DIR="/opt/Autodesk/shared/python"

# ── Parse args ──────────────────────────────────────────────────────
ENV_NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)  ENV_NAME="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash uninstall.sh [--env ENV_NAME]"
            echo ""
            echo "Options:"
            echo "  --env   Conda environment to uninstall from (reads from config if not set)"
            exit 0 ;;
        *)  echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=== FORGE CV Align — Uninstall ==="
echo ""

# ── Resolve env name from config if not provided ─────────────────
if [[ -z "$ENV_NAME" ]]; then
    if [[ -f "$CONFIG_FILE" ]]; then
        CONDA_PYTHON=$(grep "^conda_python:" "$CONFIG_FILE" | cut -d: -f2 | tr -d ' ')
        if [[ -n "$CONDA_PYTHON" ]]; then
            # Extract env name from path like /home/user/miniconda3/envs/ENVNAME/bin/python
            ENV_NAME=$(echo "$CONDA_PYTHON" | sed -n 's|.*/envs/\([^/]*\)/.*|\1|p')
        fi
    fi
    if [[ -z "$ENV_NAME" ]]; then
        read -rp "Conda environment name: " ENV_NAME
    else
        echo "Detected conda env: $ENV_NAME"
    fi
fi

# ── Step 1: Remove hook from shared location ─────────────────────
if [[ -d "$SHARED_PYTHON_DIR/$HOOK_NAME" ]]; then
    echo "Removing global hook: $SHARED_PYTHON_DIR/$HOOK_NAME"
    rm -rf "$SHARED_PYTHON_DIR/$HOOK_NAME"
    echo "  Done."
else
    echo "No global hook found at $SHARED_PYTHON_DIR/$HOOK_NAME"
fi

# ── Step 2: Uninstall pip package ────────────────────────────────
echo ""
if conda run -n "$ENV_NAME" pip show forge-align &>/dev/null; then
    echo "Uninstalling forge-align from '$ENV_NAME'..."
    conda run -n "$ENV_NAME" pip uninstall forge-align -y
    echo "  Done."
else
    echo "forge-align not installed in '$ENV_NAME'"
fi

# ── Step 3: Remove config ────────────────────────────────────────
echo ""
if [[ -f "$CONFIG_FILE" ]]; then
    echo "Removing config: $CONFIG_FILE"
    rm -f "$CONFIG_FILE"
    # Remove dir if empty
    rmdir "$CONFIG_DIR" 2>/dev/null || true
    echo "  Done."
else
    echo "No config at $CONFIG_FILE"
fi

# ── Step 4: Optionally remove conda env ──────────────────────────
echo ""
read -rp "Remove conda environment '$ENV_NAME'? [y/N]: " REMOVE_ENV
if [[ "$REMOVE_ENV" =~ ^[Yy]$ ]]; then
    echo "Removing conda environment '$ENV_NAME'..."
    conda remove -n "$ENV_NAME" --all -y
    echo "  Done."
else
    echo "Keeping conda environment '$ENV_NAME'."
fi

echo ""
echo "=== Uninstall complete ==="
echo ""
echo "Restart Flame (or rescan hooks) to remove the menu entry."
