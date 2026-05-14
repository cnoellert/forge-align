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
    # Rewrite config.yaml with current state.
    # Args: conda_python red_backend arri_backend deploy_targets...
    mkdir -p "$CONFIG_DIR"
    local conda_python="$1"
    local red_backend="$2"
    local arri_backend="$3"
    shift 3

    {
        echo "# forge-align configuration — managed by install.sh"
        echo "conda_python: $conda_python"
        echo ""
        echo "# Optional vendor-raw decode backends (used by forge-io v0.3.0+)."
        echo "# Hook injects these as FORGE_RED_REDLINE_PATH / FORGE_ARRI_ART_PATH"
        echo "# into the cli_solve subprocess env. Leave empty to skip raw decode."
        echo "red_backend: ${red_backend}"
        echo "arri_backend: ${arri_backend}"
        echo ""
        echo "# All deploy targets (global + project-specific)"
        echo "deploy_targets:"
    } > "$CONFIG_FILE"
    for t in "$@"; do
        echo "  - $t" >> "$CONFIG_FILE"
    done
}

_detect_redline() {
    # Probe standard REDline install locations on macOS/Linux.
    local candidates=(
        "/Applications/REDCINE-X Professional/REDCINE-X PRO.app/Contents/MacOS/REDline"
        "/Applications/REDCINE-X PRO.app/Contents/MacOS/REDline"
        "/usr/local/bin/REDline"
        "/opt/REDCINE-X/REDline"
    )
    for c in "${candidates[@]}"; do
        [[ -x "$c" ]] && echo "$c" && return 0
    done
    return 1
}

_detect_art_cmd() {
    # Probe standard art-cmd install locations.
    for c in /Applications/art-cmd_*/bin/art-cmd /usr/local/bin/art-cmd /opt/art-cmd/bin/art-cmd; do
        [[ -x "$c" ]] && echo "$c" && return 0
    done
    return 1
}

# ── Read existing backend paths (preserve across re-installs) ──────
RED_BACKEND="$(_read_config_value red_backend)"
ARRI_BACKEND="$(_read_config_value arri_backend)"

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
for pp in ${PROJECT_PATHS[@]+"${PROJECT_PATHS[@]}"}; do
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

    CONDA_BASE="$(conda info --base)"
    ENV_BIN="${CONDA_BASE}/envs/${ENV_NAME}/bin"

    # ── Step 2: Install forge_cv package ───────────────────────────
    echo ""
    info "Installing OpenImageIO + OpenColorIO (forge-io runtime)..."
    # conda-forge is ideal on many linux-64 stacks, but osx-arm64 often has no
    # opencolorio package and openimageio builds may lack OCIO. PyPI ships
    # matching wheels (e.g. OpenImageIO 3.x + opencolorio 2.5) that work there.
    if conda install -n "$ENV_NAME" -c conda-forge openimageio opencolorio -y &>/dev/null; then
        ok "OIIO + OCIO installed from conda-forge"
    else
        warn "conda-forge OIIO/OCIO install failed (common on osx-arm64); falling back to PyPI"
        "$ENV_BIN/pip" install OpenImageIO opencolorio
        ok "OIIO + OCIO installed from PyPI"
    fi

    info "Installing forge_cv and CV dependencies..."
    "$ENV_BIN/pip" install -e "$SCRIPT_DIR" 2>&1 | tail -3
    ok "forge-align installed"

    # ── Step 2b: Fix opencv-python conflict ────────────────────────
    # lightglue (if installed) pulls in opencv-python which conflicts
    # with our opencv-python-headless. Remove the non-headless variant.
    if "$ENV_BIN/pip" show opencv-python &>/dev/null 2>&1; then
        warn "opencv-python conflicts with opencv-python-headless — removing"
        "$ENV_BIN/pip" uninstall opencv-python -y 2>&1 | tail -1
        ok "opencv-python removed (headless variant retained)"
    fi

    # ── Step 2c: Optional SuperPoint deps ─────────────────────────
    # torch + lightglue enable the SuperPoint detector (best for large
    # scale gaps and cross-appearance matching). ~2 GB download.
    if "$ENV_BIN/python" -c "import torch; from lightglue import SuperPoint" 2>/dev/null; then
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
            "$ENV_BIN/pip" install 'torch>=2.0.0' 'lightglue @ git+https://github.com/cvg/LightGlue.git' 2>&1 | tail -5
            # lightglue pulls in opencv-python which replaces opencv-python-headless.
            # Uninstalling opencv-python nukes the shared cv2 files, so headless
            # must be force-reinstalled to restore them.
            if "$ENV_BIN/pip" show opencv-python &>/dev/null 2>&1; then
                "$ENV_BIN/pip" uninstall opencv-python -y &>/dev/null
                "$ENV_BIN/pip" install --force-reinstall opencv-python-headless &>/dev/null
            fi
            if "$ENV_BIN/python" -c "import torch; from lightglue import SuperPoint" 2>/dev/null; then
                ok "SuperPoint installed"
            else
                warn "SuperPoint install failed — SIFT/AKAZE still available"
            fi
        else
            info "Skipping SuperPoint (SIFT/AKAZE still available)"
        fi
    fi

    # ── Step 3: Verify import ──────────────────────────────────────
    if "$ENV_BIN/python" -c "import forge_io; from forge_cv.solver import solve_alignment" 2>/dev/null; then
        ok "forge_io + forge_cv imports OK"
    else
        warn "forge_cv import check failed — check install output above"
    fi

    # ── Step 4: Install ffmpeg (via conda, into the env) ─────────
    echo ""
    if "$ENV_BIN/ffmpeg" -version &>/dev/null 2>&1; then
        ok "ffmpeg found in env"
    else
        info "Installing ffmpeg into '$ENV_NAME'..."
        conda install -n "$ENV_NAME" -c conda-forge ffmpeg -y 2>&1 | tail -3
        if "$ENV_BIN/ffmpeg" -version &>/dev/null 2>&1; then
            ok "ffmpeg installed"
        else
            warn "ffmpeg install failed — MOV/MP4 reference extraction won't work"
            warn "Install manually: conda install -n $ENV_NAME -c conda-forge ffmpeg"
        fi
    fi

    # ── Step 5: Save conda python path ─────────────────────────────
    CONDA_PYTHON=$("$ENV_BIN/python" -c "import sys; print(sys.executable)")
    ok "Conda Python: $CONDA_PYTHON"

    # ── Step 5b: Vendor-raw backend detection ──────────────────────
    # forge-io v0.3.0+ decodes ARRI .ari/.arx and RED .r3d via vendor
    # binaries. The hook will inject these paths as FORGE_*_PATH env
    # vars into the cli_solve subprocess so Flame doesn't have to be
    # launched from a shell that already exports them.
    echo ""
    if [[ -z "$RED_BACKEND" ]]; then
        DETECTED_RED="$(_detect_redline || true)"
        if [[ -n "$DETECTED_RED" ]]; then
            read -rp "  REDline detected at $DETECTED_RED — enable R3D decode? [Y/n]: " USE_RED
            [[ ! "$USE_RED" =~ ^[Nn]$ ]] && RED_BACKEND="$DETECTED_RED"
        else
            info "REDline not found at standard paths — R3D decode disabled (set red_backend in $CONFIG_FILE later to enable)"
        fi
    else
        ok "REDline: $RED_BACKEND (from existing config)"
    fi

    if [[ -z "$ARRI_BACKEND" ]]; then
        DETECTED_ARRI="$(_detect_art_cmd || true)"
        if [[ -n "$DETECTED_ARRI" ]]; then
            read -rp "  art-cmd detected at $DETECTED_ARRI — enable ARRI decode? [Y/n]: " USE_ARRI
            [[ ! "$USE_ARRI" =~ ^[Nn]$ ]] && ARRI_BACKEND="$DETECTED_ARRI"
        else
            info "art-cmd not found at standard paths — ARRI decode disabled (set arri_backend in $CONFIG_FILE later to enable)"
        fi
    else
        ok "art-cmd: $ARRI_BACKEND (from existing config)"
    fi

    # ── Step 6: Interactive target selection (if none specified) ────
    if [[ ${#DEPLOY_TARGETS[@]} -eq 0 && -z "$DEPLOY_GLOBAL" && ${#PROJECT_PATHS[@]} -eq 0 ]]; then
        echo ""
        read -rp "  Deploy globally? [Y/n]: " DEPLOY_GLOBAL_YN
        if [[ ! "$DEPLOY_GLOBAL_YN" =~ ^[Nn]$ ]]; then
            read -rp "  Deploy path [$SHARED_PYTHON_DIR]: " CUSTOM_SHARED
            CUSTOM_SHARED="${CUSTOM_SHARED:-$SHARED_PYTHON_DIR}"
            DEPLOY_TARGETS+=("$CUSTOM_SHARED/$HOOK_NAME")
        fi

        echo ""
        read -rp "  Also deploy to a specific project? [y/N]: " ADD_PROJECT
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
    _save_config "$CONDA_PYTHON" "$RED_BACKEND" "$ARRI_BACKEND" "${DEPLOY_TARGETS[@]+"${DEPLOY_TARGETS[@]}"}"
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
