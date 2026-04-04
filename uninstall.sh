#!/data/data/com.termux/files/usr/bin/bash

set -euo pipefail

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
INSTALL_ROOT="${PREFIX}/opt/termux-app-store"
LAUNCHER_PATH="${PREFIX}/bin/termux-app-store"
DESKTOP_FILE="${HOME}/.local/share/applications/termux-app-store.desktop"

ensure_safe_install_root() {
    case "${INSTALL_ROOT}" in
        "${PREFIX}/opt/termux-app-store") ;;
        *)
            echo "Refusing to modify unexpected install path: ${INSTALL_ROOT}" >&2
            exit 1
            ;;
    esac
}

main() {
    ensure_safe_install_root

    if [ -e "${LAUNCHER_PATH}" ]; then
        rm -f "${LAUNCHER_PATH}"
        echo "Removed launcher: ${LAUNCHER_PATH}"
    fi

    if [ -e "${DESKTOP_FILE}" ]; then
        rm -f "${DESKTOP_FILE}"
        echo "Removed desktop entry: ${DESKTOP_FILE}"
    fi

    if [ -d "${INSTALL_ROOT}" ]; then
        rm -rf "${INSTALL_ROOT}"
        echo "Removed installed files: ${INSTALL_ROOT}"
    fi

    echo "Uninstall complete."
    echo "Runtime packages were left installed."
}

main "$@"
