#!/usr/bin/env bash
# ==============================================================================
# Verified Agentic Development (VAD) - Cross-Platform WSL / POSIX Hook Relay
# ==============================================================================
# Relays hook scripts between Windows (Git Bash/cmd/powershell), WSL, macOS,
# and native Linux environments while preserving stdin JSON streams, argument
# flags, path translation, and exit codes.
# ==============================================================================

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/platform.conf"

# Defaults
PLATFORM_MODE="auto"
WSL_DISTRO=""
WINDOWS_TO_WSL_TRANSLATE="true"
PYTHON_LINUX="python3"
PYTHON_MACOS="python3"
PYTHON_WSL="python3"
PYTHON_WINDOWS="python.exe"
PLATFORM_DEBUG="0"

if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
fi

debug_log() {
    if [[ "$PLATFORM_DEBUG" == "1" ]]; then
        echo "[VAD Relay DEBUG] $*" >&2
    fi
}

# 1. Environment Detection
detect_environment() {
    local uname_s
    uname_s="$(uname -s 2>/dev/null || echo "Unknown")"
    local uname_o
    uname_o="$(uname -o 2>/dev/null || echo "Unknown")"

    if [[ "$uname_s" == "Darwin" ]]; then
        echo "macos"
    elif [[ "$uname_o" == "Msys" || "$uname_o" == "Cygwin" || "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        echo "windows"
    elif [[ "$uname_s" == "Linux" ]]; then
        if grep -qi "microsoft\|wsl" /proc/version 2>/dev/null; then
            echo "wsl"
        else
            echo "native_linux"
        fi
    else
        echo "windows"
    fi
}

ENV_TYPE="$PLATFORM_MODE"
if [[ "$ENV_TYPE" == "auto" || -z "$ENV_TYPE" ]]; then
    ENV_TYPE="$(detect_environment)"
fi

debug_log "Detected runtime environment: ${ENV_TYPE}"

# 2. Path Translation Helper
to_posix_path() {
    local in_path="$1"
    if [[ -z "$in_path" ]]; then
        echo ""
        return
    fi

    # Check for Windows drive letter format: C:\ or C:/
    if [[ "$in_path" =~ ^([a-zA-Z]):[\\/](.*) ]]; then
        local drive_letter="${BASH_REMATCH[1]}"
        local rest_path="${BASH_REMATCH[2]}"
        drive_letter="$(echo "$drive_letter" | tr '[:upper:]' '[:lower:]')"
        rest_path="${rest_path//\\//}"
        echo "/mnt/${drive_letter}/${rest_path}"
    else
        echo "${in_path//\\//}"
    fi
}

to_windows_path() {
    local in_path="$1"
    if [[ -z "$in_path" ]]; then
        echo ""
        return
    fi

    if [[ "$in_path" =~ ^/mnt/([a-zA-Z])/(.*) ]]; then
        local drive_letter="${BASH_REMATCH[1]}"
        local rest_path="${BASH_REMATCH[2]}"
        drive_letter="$(echo "$drive_letter" | tr '[:lower:]' '[:upper:]')"
        echo "${drive_letter}:/${rest_path}"
    else
        echo "$in_path"
    fi
}

# 3. Argument Translation
if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <target-hook-script> [args...]" >&2
    exit 1
fi

TARGET_SCRIPT="$1"
shift

# If TARGET_SCRIPT is relative, resolve it
if [[ ! "$TARGET_SCRIPT" =~ ^/ && ! "$TARGET_SCRIPT" =~ ^[a-zA-Z]: ]]; then
    TARGET_SCRIPT="$(cd "$(pwd)" && pwd)/${TARGET_SCRIPT}"
fi

debug_log "Target script before translation: ${TARGET_SCRIPT}"

# Buffer stdin if data is available
STDIN_DATA=""
if [ ! -t 0 ]; then
    STDIN_DATA="$(cat)"
fi

# 4. Dispatch based on host environment
case "$ENV_TYPE" in
    windows)
        # If running in Windows Git Bash, check if WSL is available and requested
        if command -v wsl.exe &>/dev/null && [[ "$WINDOWS_TO_WSL_TRANSLATE" == "true" ]]; then
            WSL_TARGET="$(to_posix_path "$TARGET_SCRIPT")"
            debug_log "Relaying to WSL: ${WSL_TARGET}"
            
            WSL_CMD=("wsl.exe")
            if [[ -n "$WSL_DISTRO" ]]; then
                WSL_CMD+=("-d" "$WSL_DISTRO")
            fi
            WSL_CMD+=("--" "bash" "$WSL_TARGET")

            for arg in "$@"; do
                WSL_CMD+=("$(to_posix_path "$arg")")
            done

            if [[ -n "$STDIN_DATA" ]]; then
                printf "%s" "$STDIN_DATA" | "${WSL_CMD[@]}"
                exit_code=$?
            else
                "${WSL_CMD[@]}"
                exit_code=$?
            fi
            exit $exit_code
        else
            # Native Windows fallback execution via bash
            debug_log "Executing natively on Windows via bash"
            if [[ -n "$STDIN_DATA" ]]; then
                printf "%s" "$STDIN_DATA" | bash "$TARGET_SCRIPT" "$@"
                exit $?
            else
                bash "$TARGET_SCRIPT" "$@"
                exit $?
            fi
        fi
        ;;

    wsl|native_linux|macos)
        debug_log "Executing directly in POSIX environment (${ENV_TYPE})"
        POSIX_SCRIPT="$(to_posix_path "$TARGET_SCRIPT")"
        if [[ -n "$STDIN_DATA" ]]; then
            printf "%s" "$STDIN_DATA" | bash "$POSIX_SCRIPT" "$@"
            exit $?
        else
            bash "$POSIX_SCRIPT" "$@"
            exit $?
        fi
        ;;

    *)
        echo "[VAD Relay Error] Unknown environment: ${ENV_TYPE}" >&2
        exit 1
        ;;
esac
