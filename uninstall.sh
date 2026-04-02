#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# FORGE CV Align — Uninstaller
#
# Removes the hook from all known locations (config + filesystem scan),
# clears pycache, uninstalls the pip package, removes config, and
# optionally removes the conda environment.
#
# Usage:
#   bash uninstall.sh
#   bash uninstall.sh --env myenv
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_NAME="forge_cv_align"
CONFIG_DIR="$HOME/.forge"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
SHARED_PYTHON_DIR="/opt/Autodesk/shared/python"

# ── Colours ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
CYAN='\033[0;36m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}OK${NC}  $*"; }
warn() { echo -e "  ${YELLOW}!!${NC}  $*"; }
err()  { echo -e "  ${RED}ERR${NC} $*"; }
info() { echo -e "  ${CYAN}--${NC}  $*"; }

# ── Parse args ─────────────────────────────────────────────────────
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

echo ""
echo -e "${CYAN}=== FORGE CV Align — Uninstall ===${NC}"
echo ""

# ── Config helpers ─────────────────────────────────────────────────
_read_config_value() {
    local key="$1"
    if [[ -f "$CONFIG_FILE" ]]; then
        grep "^${key}:" "$CONFIG_FILE" 2>/dev/null | head -1 | sed "s/^${key}: *//"
    fi
}

_read_config_list() {
    local key="$1"
    if [[ -f "$CONFIG_FILE" ]]; then
        sed -n "/^${key}:/,/^[^ ]/{ /^  - /p; }" "$CONFIG_FILE" | sed 's/^  - //'
    fi
}

# ── Resolve env name from config if not provided ───────────────────
if [[ -z "$ENV_NAME" ]]; then
    CONDA_PYTHON=$(_read_config_value conda_python)
    if [[ -n "$CONDA_PYTHON" ]]; then
        ENV_NAME=$(echo "$CONDA_PYTHON" | sed -n 's|.*/envs/\([^/]*\)/.*|\1|p')
    fi
    if [[ -z "$ENV_NAME" ]]; then
        read -rp "Conda environment name: " ENV_NAME
    else
        info "Detected conda env: $ENV_NAME"
    fi
fi

# ── Step 1: Collect ALL hook locations ─────────────────────────────
# Start with config targets, then scan filesystem for any others
REMOVE_TARGETS=()
SEEN=()

_add_target() {
    local path="$1"
    # Normalise /System/Volumes/Data/mnt → /mnt (macOS firmlink)
    local norm="${path#/System/Volumes/Data}"
    for s in "${SEEN[@]+"${SEEN[@]}"}"; do
        [[ "$s" == "$norm" ]] && return
    done
    SEEN+=("$norm")
    REMOVE_TARGETS+=("$path")
}

# From config
while IFS= read -r line; do
    [[ -n "$line" ]] && _add_target "$line"
done < <(_read_config_list deploy_targets)

# Scan: global shared python dir
if [[ -d "$SHARED_PYTHON_DIR/$HOOK_NAME" ]]; then
    _add_target "$SHARED_PYTHON_DIR/$HOOK_NAME"
fi

# Scan: all Flame project setups directories
# Check common Flame project roots for forge_cv_align hook dirs
for search_root in /mnt/*/projects /Volumes/*/projects /srv/*/projects; do
    if [[ -d "$search_root" ]]; then
        while IFS= read -r found; do
            _add_target "$found"
        done < <(find "$search_root" -maxdepth 4 -type d -name "$HOOK_NAME" -path "*/python/$HOOK_NAME" 2>/dev/null)
    fi
done

# ── Step 2: Remove all hook locations ──────────────────────────────
echo ""
if [[ ${#REMOVE_TARGETS[@]} -gt 0 ]]; then
    echo "Found ${#REMOVE_TARGETS[@]} hook location(s):"
    for dest in "${REMOVE_TARGETS[@]}"; do
        echo "    $dest"
    done
    echo ""

    for dest in "${REMOVE_TARGETS[@]}"; do
        if [[ -d "$dest" ]]; then
            # Clear pycache first
            find "$dest" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
            # Remove the hook directory
            rm -rf "$dest" 2>/dev/null || sudo rm -rf "$dest" 2>/dev/null || true
            if [[ ! -d "$dest" ]]; then
                ok "Removed: $dest"
            else
                err "Could not remove: $dest"
            fi
        else
            info "Already gone: $dest"
        fi
    done
else
    info "No hook locations found"
fi

# ── Step 3: Clear pycache in source repo ───────────────────────────
echo ""
if [[ -d "$SCRIPT_DIR/forge_cv" ]]; then
    find "$SCRIPT_DIR/forge_cv" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    ok "Cleared pycache in source repo"
fi

# ── Step 4: Check for stale forge_cv_timewarp ──────────────────────
if [[ -d "$SHARED_PYTHON_DIR/forge_cv_timewarp" ]]; then
    echo ""
    warn "Stale forge_cv_timewarp found at: $SHARED_PYTHON_DIR/forge_cv_timewarp"
    read -rp "  Remove it? [y/N]: " REMOVE_TW
    if [[ "$REMOVE_TW" =~ ^[Yy]$ ]]; then
        rm -rf "$SHARED_PYTHON_DIR/forge_cv_timewarp" 2>/dev/null || \
            sudo rm -rf "$SHARED_PYTHON_DIR/forge_cv_timewarp" 2>/dev/null || true
        if [[ ! -d "$SHARED_PYTHON_DIR/forge_cv_timewarp" ]]; then
            ok "Removed forge_cv_timewarp"
        else
            err "Could not remove forge_cv_timewarp"
        fi
    fi
fi

# ── Step 5: Uninstall pip package ──────────────────────────────────
echo ""
if command -v conda &>/dev/null && conda run -n "$ENV_NAME" pip show forge-align &>/dev/null 2>&1; then
    info "Uninstalling forge-align from '$ENV_NAME'..."
    conda run -n "$ENV_NAME" pip uninstall forge-align -y 2>&1 | tail -1
    ok "forge-align uninstalled"
else
    info "forge-align not installed in '$ENV_NAME'"
fi

# ── Step 6: Remove config ─────────────────────────────────────────
echo ""
if [[ -f "$CONFIG_FILE" ]]; then
    info "Removing config: $CONFIG_FILE"
    rm -f "$CONFIG_FILE"
    rmdir "$CONFIG_DIR" 2>/dev/null || true
    ok "Config removed"
else
    info "No config at $CONFIG_FILE"
fi

# ── Step 7: Optionally remove conda env ────────────────────────────
echo ""
read -rp "Remove conda environment '$ENV_NAME'? [y/N]: " REMOVE_ENV
if [[ "$REMOVE_ENV" =~ ^[Yy]$ ]]; then
    info "Removing conda environment '$ENV_NAME'..."
    conda remove -n "$ENV_NAME" --all -y
    ok "Environment removed"
else
    info "Keeping conda environment '$ENV_NAME'"
fi

echo ""
echo -e "${GREEN}=== Uninstall complete ===${NC}"
echo ""
echo "  Restart Flame (or rescan hooks) to remove the menu entry."
echo ""
