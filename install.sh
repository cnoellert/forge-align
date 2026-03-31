#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# FORGE CV Align — Installer
#
# Sets up a conda environment, installs CV dependencies, and deploys
# the hook. Can install globally (all Flame projects) or per-project.
#
# Usage:
#   bash install.sh
#   bash install.sh --global
#   bash install.sh --project /path/to/flame/project
#   bash install.sh --env myenv --global
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_NAME="forge_cv_align"
DEFAULT_ENV="forge"
CONFIG_DIR="$HOME/.forge"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
SHARED_PYTHON_DIR="/opt/Autodesk/shared/python"

# ── Parse args ──────────────────────────────────────────────────────
ENV_NAME=""
PROJECT_PATH=""
DEPLOY_GLOBAL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)      ENV_NAME="$2";      shift 2 ;;
        --project)  PROJECT_PATH="$2";  shift 2 ;;
        --global)   DEPLOY_GLOBAL="yes"; shift ;;
        -h|--help)
            echo "Usage: bash install.sh [--env ENV_NAME] [--global | --project /path/to/flame/project]"
            echo ""
            echo "Options:"
            echo "  --env       Conda environment name (default: forge)"
            echo "  --global    Install hook for all Flame projects (/opt/Autodesk/shared/python)"
            echo "  --project   Install hook for a specific Flame project"
            exit 0 ;;
        *)  echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=== FORGE CV Align — Install ==="
echo ""

# ── Step 1: Conda environment ──────────────────────────────────────
if ! command -v conda &>/dev/null; then
    echo "ERROR: conda not found. Install Miniconda or Anaconda first."
    echo "  https://docs.anaconda.com/miniconda/"
    exit 1
fi

if [[ -z "$ENV_NAME" ]]; then
    read -rp "Conda environment name [$DEFAULT_ENV]: " ENV_NAME
    ENV_NAME="${ENV_NAME:-$DEFAULT_ENV}"
fi

# Check if env exists, create if not
if ! conda env list | grep -q "^${ENV_NAME} "; then
    echo "Creating conda environment '$ENV_NAME' (Python 3.11)..."
    conda create -n "$ENV_NAME" python=3.11 -y
else
    echo "Conda environment '$ENV_NAME' exists."
fi

# ── Step 2: Install forge_cv package (bundled) ─────────────────────
echo ""
echo "Installing forge_cv and CV dependencies..."
conda run -n "$ENV_NAME" pip install -e "$SCRIPT_DIR"
echo "  Installed: forge-cv-align (opencv-python-headless, numpy)"

# Verify import works
if conda run -n "$ENV_NAME" python -c "from forge_cv.solver import solve_alignment" 2>/dev/null; then
    echo "  Verified: forge_cv imports OK"
else
    echo "  WARNING: forge_cv import check failed — check install output above"
fi

# ── Step 3: Check ffmpeg ─────────────────────────────────────────
echo ""
if command -v ffmpeg &>/dev/null; then
    echo "ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "WARNING: ffmpeg not found. Required for MOV/MP4 reference extraction."
    echo "  Install via: sudo apt install ffmpeg  (Linux)"
    echo "          or:  brew install ffmpeg       (macOS)"
fi

# ── Step 4: Resolve and save conda python path ────────────────────
CONDA_PYTHON=$(conda run -n "$ENV_NAME" python -c "import sys; print(sys.executable)")
echo ""
echo "Conda Python: $CONDA_PYTHON"

# Write config to ~/.forge/config.yaml (no sudo needed)
mkdir -p "$CONFIG_DIR"
if [[ -f "$CONFIG_FILE" ]] && grep -q "^conda_python:" "$CONFIG_FILE"; then
    sed -i.bak "s|^conda_python:.*|conda_python: $CONDA_PYTHON|" "$CONFIG_FILE"
    rm -f "${CONFIG_FILE}.bak"
else
    echo "conda_python: $CONDA_PYTHON" >> "$CONFIG_FILE"
fi
echo "  Saved to $CONFIG_FILE"

# ── Step 5: Deploy hook ───────────────────────────────────────────
echo ""

# If neither --global nor --project was passed, prompt the user
if [[ -z "$DEPLOY_GLOBAL" && -z "$PROJECT_PATH" ]]; then
    echo "Where should the hook be installed?"
    echo ""
    echo "  1) All projects  — $SHARED_PYTHON_DIR"
    echo "  2) Single project — you provide the project path"
    echo "  3) Skip deploy   — I'll do it manually"
    echo ""
    read -rp "Choice [1/2/3]: " DEPLOY_CHOICE
    case "$DEPLOY_CHOICE" in
        1) DEPLOY_GLOBAL="yes" ;;
        2) read -rp "Flame project path: " PROJECT_PATH ;;
        *) echo "Skipping hook deploy." ;;
    esac
fi

_deploy_hook() {
    local dest="$1"
    echo "Deploying hook to: $dest"
    rm -rf "$dest"
    mkdir -p "$dest"
    cp "$SCRIPT_DIR/scripts/forge_cv_align.py" "$dest/"
    echo "  Done."
}

if [[ "$DEPLOY_GLOBAL" == "yes" ]]; then
    DEST="$SHARED_PYTHON_DIR/$HOOK_NAME"
    if [[ ! -d "$SHARED_PYTHON_DIR" ]]; then
        echo "Creating $SHARED_PYTHON_DIR (may require sudo)..."
        sudo mkdir -p "$SHARED_PYTHON_DIR" 2>/dev/null || mkdir -p "$SHARED_PYTHON_DIR"
    fi
    _deploy_hook "$DEST"
    echo ""
    echo "  Hook available to all Flame projects."
    echo "  Rescan Python Hooks in Flame (or restart) to activate."

elif [[ -n "$PROJECT_PATH" ]]; then
    SETUPS_DIR="$PROJECT_PATH/setups"
    if [[ ! -d "$SETUPS_DIR" ]]; then
        SETUPS_DIR="/System/Volumes/Data${PROJECT_PATH}/setups"
    fi
    if [[ ! -d "$SETUPS_DIR" ]]; then
        echo "ERROR: Could not find setups directory at $PROJECT_PATH"
        exit 1
    fi
    _deploy_hook "$SETUPS_DIR/python/$HOOK_NAME"
    echo ""
    echo "  Rescan Python Hooks in Flame (or restart) to activate."

else
    echo ""
    echo "To deploy manually:"
    echo "  Global (all projects):"
    echo "    mkdir -p $SHARED_PYTHON_DIR/$HOOK_NAME"
    echo "    cp $SCRIPT_DIR/scripts/forge_cv_align.py $SHARED_PYTHON_DIR/$HOOK_NAME/"
    echo ""
    echo "  Single project:"
    echo "    mkdir -p /path/to/project/setups/python/$HOOK_NAME"
    echo "    cp $SCRIPT_DIR/scripts/forge_cv_align.py /path/to/project/setups/python/$HOOK_NAME/"
fi

echo ""
echo "=== Install complete ==="
echo ""
echo "Usage: In Flame, right-click 2+ timeline segments → FORGE → CV Align"
