#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# FORGE CV Align — Installer
#
# Full install: conda env, pip deps, deploy hook, verify.
# Deploy-only:  copy hook to all targets, clear pycache, verify.
#
# Usage:
#   bash install.sh                         # full install (interactive)
#   bash install.sh --deploy-only           # redeploy hook only (fast)
#   bash install.sh --global                # full install, global deploy
#   bash install.sh --project /path/to/proj # full install, project deploy
#   bash install.sh --env myenv --global    # specify conda env
#
# Deploy targets are saved in ~/.forge/config.yaml and reused by
# --deploy-only. Add more with --project (cumulative).
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_NAME="forge_cv_align"
HOOK_SOURCE="$SCRIPT_DIR/scripts/forge_cv_align.py"
DEFAULT_ENV="forge-cv"
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
PROJECT_PATHS=()
DEPLOY_GLOBAL=""
DEPLOY_ONLY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)          ENV_NAME="$2";          shift 2 ;;
        --project)      PROJECT_PATHS+=("$2");  shift 2 ;;
        --global)       DEPLOY_GLOBAL="yes";    shift ;;
        --deploy-only)  DEPLOY_ONLY="yes";      shift ;;
        -h|--help)
            echo "Usage: bash install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --env NAME        Conda environment name (default: forge-cv)"
            echo "  --global          Deploy hook globally (/opt/Autodesk/shared/python)"
            echo "  --project PATH    Deploy to a Flame project (repeatable)"
            echo "  --deploy-only     Skip env/pip setup — just deploy, clear cache, verify"
            echo "  -h, --help        Show this help"
            echo ""
            echo "Deploy targets are saved in ~/.forge/config.yaml and reused by --deploy-only."
            exit 0 ;;
        *)  echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Verify hook source exists ──────────────────────────────────────
if [[ ! -f "$HOOK_SOURCE" ]]; then
    err "Hook source not found: $HOOK_SOURCE"
    exit 1
fi

echo ""
if [[ -n "$DEPLOY_ONLY" ]]; then
    echo -e "${CYAN}=== FORGE CV Align — Deploy ===${NC}"
else
    echo -e "${CYAN}=== FORGE CV Align — Install ===${NC}"
fi
echo ""

# ── Config helpers ─────────────────────────────────────────────────
_read_config_value() {
    # Read a single-value key from config.yaml
    local key="$1"
    if [[ -f "$CONFIG_FILE" ]]; then
        grep "^${key}:" "$CONFIG_FILE" 2>/dev/null | head -1 | sed "s/^${key}: *//"
    fi
}

_read_config_list() {
    # Read a YAML list under a key (one item per "  - value" line)
    local key="$1"
    if [[ -f "$CONFIG_FILE" ]]; then
        sed -n "/^${key}:/,/^[^ ]/{ /^  - /p; }" "$CONFIG_FILE" | sed 's/^  - //'
    fi
}

_save_config() {
    # Rewrite config.yaml with current state
    mkdir -p "$CONFIG_DIR"
    local conda_python="$1"
    shift

    cat > "$CONFIG_FILE" <<YAML
# forge-align configuration — managed by install.sh
conda_python: $conda_python

# All deploy targets (global + project-specific)
deploy_targets:
YAML
    for t in "$@"; do
        echo "  - $t" >> "$CONFIG_FILE"
    done
}

# ── Collect deploy targets ─────────────────────────────────────────
# Start with saved targets from config
DEPLOY_TARGETS=()
while IFS= read -r line; do
    [[ -n "$line" ]] && DEPLOY_TARGETS+=("$line")
done < <(_read_config_list deploy_targets)

# Add global if requested
if [[ "$DEPLOY_GLOBAL" == "yes" ]]; then
    GLOBAL_DEST="$SHARED_PYTHON_DIR/$HOOK_NAME"
    # Add if not already in list
    if [[ ${#DEPLOY_TARGETS[@]} -eq 0 ]] || ! printf '%s\n' "${DEPLOY_TARGETS[@]}" | grep -qxF "$GLOBAL_DEST"; then
        DEPLOY_TARGETS+=("$GLOBAL_DEST")
    fi
fi

# Add project paths if requested
for pp in "${PROJECT_PATHS[@]+"${PROJECT_PATHS[@]}"}"; do
    # Resolve the setups/python directory
    SETUPS_DIR="$pp/setups"
    if [[ ! -d "$SETUPS_DIR" ]]; then
        SETUPS_DIR="/System/Volumes/Data${pp}/setups"
    fi
    if [[ ! -d "$SETUPS_DIR" ]]; then
        err "Could not find setups directory for: $pp"
        exit 1
    fi
    PROJ_DEST="$SETUPS_DIR/python/$HOOK_NAME"
    if [[ ${#DEPLOY_TARGETS[@]} -eq 0 ]] || ! printf '%s\n' "${DEPLOY_TARGETS[@]}" | grep -qxF "$PROJ_DEST"; then
        DEPLOY_TARGETS+=("$PROJ_DEST")
    fi
done

# ── Full install steps (skipped with --deploy-only) ────────────────
CONDA_PYTHON=""

if [[ -z "$DEPLOY_ONLY" ]]; then

    # ── Step 1: Conda environment ──────────────────────────────────
    if ! command -v conda &>/dev/null; then
        err "conda not found. Install Miniconda or Anaconda first."
        echo "  https://docs.anaconda.com/miniconda/"
        exit 1
    fi

    if [[ -z "$ENV_NAME" ]]; then
        read -rp "Conda environment name [$DEFAULT_ENV]: " ENV_NAME
        ENV_NAME="${ENV_NAME:-$DEFAULT_ENV}"
    fi

    if ! conda env list | grep -q "^${ENV_NAME} "; then
        info "Creating conda environment '$ENV_NAME' (Python 3.11)..."
        conda create -n "$ENV_NAME" python=3.11 -y
    else
        ok "Conda environment '$ENV_NAME' exists"
    fi

    # ── Step 2: Install forge_cv package ───────────────────────────
    echo ""
    info "Installing forge_cv and CV dependencies..."
    conda run -n "$ENV_NAME" pip install -e "$SCRIPT_DIR" 2>&1 | tail -3
    ok "forge-align installed"

    # ── Step 2b: Fix opencv-python conflict ────────────────────────
    # lightglue (if installed) pulls in opencv-python which conflicts
    # with our opencv-python-headless. Remove the non-headless variant.
    if conda run -n "$ENV_NAME" pip show opencv-python &>/dev/null 2>&1; then
        warn "opencv-python conflicts with opencv-python-headless — removing"
        conda run -n "$ENV_NAME" pip uninstall opencv-python -y 2>&1 | tail -1
        ok "opencv-python removed (headless variant retained)"
    fi

    # ── Step 2c: Optional SuperPoint deps ─────────────────────────
    # torch + lightglue enable the SuperPoint detector (best for large
    # scale gaps and cross-appearance matching). ~2 GB download.
    if conda run -n "$ENV_NAME" python -c "import torch; from lightglue import SuperPoint" 2>/dev/null; then
        ok "SuperPoint deps already installed (torch + lightglue)"
    else
        echo ""
        echo "  Optional: SuperPoint detector (torch + lightglue)"
        echo "  Best for large scale gaps and cross-appearance matching."
        echo "  Requires ~2 GB download. SIFT works well for most cases."
        echo ""
        read -rp "  Install SuperPoint support? [y/N]: " INSTALL_SP
        if [[ "$INSTALL_SP" =~ ^[Yy]$ ]]; then
            info "Installing torch + lightglue (this may take a few minutes)..."
            conda run -n "$ENV_NAME" pip install "torch>=2.0.0" "lightglue>=0.1" 2>&1 | tail -5
            # lightglue pulls in opencv-python — remove it again
            if conda run -n "$ENV_NAME" pip show opencv-python &>/dev/null 2>&1; then
                conda run -n "$ENV_NAME" pip uninstall opencv-python -y &>/dev/null
            fi
            if conda run -n "$ENV_NAME" python -c "import torch; from lightglue import SuperPoint" 2>/dev/null; then
                ok "SuperPoint installed"
            else
                warn "SuperPoint install failed — SIFT/AKAZE still available"
            fi
        else
            info "Skipping SuperPoint (SIFT/AKAZE still available)"
        fi
    fi

    # ── Step 3: Verify import ──────────────────────────────────────
    if conda run -n "$ENV_NAME" python -c "from forge_cv.solver import solve_alignment" 2>/dev/null; then
        ok "forge_cv imports OK"
    else
        warn "forge_cv import check failed — check install output above"
    fi

    # ── Step 4: Install ffmpeg (via conda, into the env) ─────────
    echo ""
    if conda run -n "$ENV_NAME" ffmpeg -version &>/dev/null 2>&1; then
        ok "ffmpeg found in env"
    else
        info "Installing ffmpeg into '$ENV_NAME'..."
        conda install -n "$ENV_NAME" -c conda-forge ffmpeg -y 2>&1 | tail -3
        if conda run -n "$ENV_NAME" ffmpeg -version &>/dev/null 2>&1; then
            ok "ffmpeg installed"
        else
            warn "ffmpeg install failed — MOV/MP4 reference extraction won't work"
            warn "Install manually: conda install -n $ENV_NAME -c conda-forge ffmpeg"
        fi
    fi

    # ── Step 5: Save conda python path ─────────────────────────────
    CONDA_PYTHON=$(conda run -n "$ENV_NAME" python -c "import sys; print(sys.executable)")
    ok "Conda Python: $CONDA_PYTHON"

    # ── Step 6: Interactive target selection (if none specified) ────
    if [[ ${#DEPLOY_TARGETS[@]} -eq 0 && -z "$DEPLOY_GLOBAL" && ${#PROJECT_PATHS[@]+"${#PROJECT_PATHS[@]}"} -eq 0 ]]; then
        echo ""
        echo "  Hook will be deployed globally to: $SHARED_PYTHON_DIR"
        echo "  (Available to all Flame projects on this machine.)"
        echo ""
        read -rp "  Also deploy to a specific project? [y/N]: " ADD_PROJECT
        DEPLOY_TARGETS+=("$SHARED_PYTHON_DIR/$HOOK_NAME")
        if [[ "$ADD_PROJECT" =~ ^[Yy]$ ]]; then
            read -rp "  Flame project path (e.g. /mnt/server/projects/myproject): " PP
            SD="$PP/setups"
            [[ ! -d "$SD" ]] && SD="/System/Volumes/Data${PP}/setups"
            if [[ -d "$SD" ]]; then
                DEPLOY_TARGETS+=("$SD/python/$HOOK_NAME")
            else
                err "Could not find setups directory for: $PP"
            fi
        fi
    fi

else
    # Deploy-only mode: read conda_python from config
    CONDA_PYTHON=$(_read_config_value conda_python)
    if [[ -z "$CONDA_PYTHON" ]]; then
        CONDA_PYTHON="(not configured — run full install first)"
    fi
fi

# ── Deploy hook to all targets ─────────────────────────────────────
echo ""
if [[ ${#DEPLOY_TARGETS[@]} -eq 0 ]]; then
    info "No deploy targets configured."
    echo ""
    echo "  To deploy later:"
    echo "    bash install.sh --deploy-only --global"
    echo "    bash install.sh --deploy-only --project /path/to/flame/project"
else
    echo -e "${CYAN}Deploying to ${#DEPLOY_TARGETS[@]} target(s)...${NC}"
    echo ""

    DEPLOY_OK=0
    DEPLOY_FAIL=0

    for dest in "${DEPLOY_TARGETS[@]}"; do
        # Create directory (may need sudo for /opt/Autodesk)
        if [[ ! -d "$dest" ]]; then
            if [[ "$dest" == /opt/* ]]; then
                sudo mkdir -p "$dest" 2>/dev/null || mkdir -p "$dest"
            else
                mkdir -p "$dest"
            fi
        fi

        # Copy hook
        if cp "$HOOK_SOURCE" "$dest/forge_cv_align.py" 2>/dev/null || \
           sudo cp "$HOOK_SOURCE" "$dest/forge_cv_align.py" 2>/dev/null; then

            # Clear pycache at target
            find "$dest" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

            # Verify file matches source
            if diff -q "$HOOK_SOURCE" "$dest/forge_cv_align.py" &>/dev/null; then
                ok "$dest"
                DEPLOY_OK=$((DEPLOY_OK + 1))
            else
                err "$dest — file mismatch after copy!"
                DEPLOY_FAIL=$((DEPLOY_FAIL + 1))
            fi
        else
            err "$dest — copy failed (permission denied?)"
            DEPLOY_FAIL=$((DEPLOY_FAIL + 1))
        fi
    done

    # Clear pycache in source repo too
    find "$SCRIPT_DIR/forge_cv" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

    echo ""
    if [[ $DEPLOY_FAIL -eq 0 ]]; then
        ok "All $DEPLOY_OK target(s) deployed and verified"
    else
        warn "$DEPLOY_OK deployed, $DEPLOY_FAIL failed"
    fi
fi

# ── Save config ────────────────────────────────────────────────────
if [[ -n "$CONDA_PYTHON" && "$CONDA_PYTHON" != "(not configured"* ]]; then
    _save_config "$CONDA_PYTHON" "${DEPLOY_TARGETS[@]+"${DEPLOY_TARGETS[@]}"}"
    info "Config saved: $CONFIG_FILE"
fi

# ── Post-deploy instructions ──────────────────────────────────────
echo ""
echo -e "${CYAN}=== Post-deploy ===${NC}"
echo ""
echo "  Flame must reload the hook module. Choose one:"
echo ""
echo "  Option A — Restart Flame (safest)"
echo ""
echo "  Option B — Evict cached module via bridge:"
echo "    import sys; [sys.modules.pop(k) for k in list(sys.modules) if 'forge_cv_align' in k]"
echo "    Then: Rescan Python Hooks"
echo ""

if [[ -z "$DEPLOY_ONLY" ]]; then
    echo -e "${GREEN}=== Install complete ===${NC}"
else
    echo -e "${GREEN}=== Deploy complete ===${NC}"
fi
echo ""
