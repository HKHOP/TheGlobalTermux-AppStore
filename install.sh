#!/data/data/com.termux/files/usr/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
INSTALL_ROOT="${PREFIX}/opt/termux-app-store"
BIN_DIR="${PREFIX}/bin"
LAUNCHER_PATH="${BIN_DIR}/termux-app-store"
DESKTOP_DIR="${HOME}/.local/share/applications"
DESKTOP_FILE="${DESKTOP_DIR}/termux-app-store.desktop"
DEFAULT_REPO_URL="https://github.com/HKHOP/TheGlobalTermux-AppStore.git"
MANIFEST_PATH="${INSTALL_ROOT}/.termux_app_store_install.json"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

ensure_safe_install_root() {
    case "${INSTALL_ROOT}" in
        "${PREFIX}/opt/termux-app-store") ;;
        *)
            echo "Refusing to modify unexpected install path: ${INSTALL_ROOT}" >&2
            exit 1
            ;;
    esac
}

copy_project_files() {
    mkdir -p "${INSTALL_ROOT}"
    cp "${SCRIPT_DIR}/app.py" "${INSTALL_ROOT}/app.py"
    cp "${SCRIPT_DIR}/README.md" "${INSTALL_ROOT}/README.md"
    cp "${SCRIPT_DIR}/LICENSE.txt" "${INSTALL_ROOT}/LICENSE.txt"
    cp "${SCRIPT_DIR}/install.sh" "${INSTALL_ROOT}/install.sh"
    cp "${SCRIPT_DIR}/uninstall.sh" "${INSTALL_ROOT}/uninstall.sh"
    rm -rf "${INSTALL_ROOT}/src" "${INSTALL_ROOT}/assets"
    cp -R "${SCRIPT_DIR}/src" "${INSTALL_ROOT}/src"
    cp -R "${SCRIPT_DIR}/assets" "${INSTALL_ROOT}/assets"
}

write_manifest() {
    local repo_url="${DEFAULT_REPO_URL}"
    local commit=""

    if command -v git >/dev/null 2>&1; then
        repo_url="$(git -C "${SCRIPT_DIR}" config --get remote.origin.url 2>/dev/null || printf '%s' "${DEFAULT_REPO_URL}")"
        commit="$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || true)"
    fi

    cat > "${MANIFEST_PATH}" <<EOF
{
  "repo_url": "${repo_url}",
  "commit": "${commit}"
}
EOF
}

write_launcher() {
    mkdir -p "${BIN_DIR}"
    cat > "${LAUNCHER_PATH}" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd "${INSTALL_ROOT}"
exec python app.py "\$@"
EOF
    chmod 755 "${LAUNCHER_PATH}"
}

write_desktop_entry() {
    mkdir -p "${DESKTOP_DIR}"
    cat > "${DESKTOP_FILE}" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Termux App Store
Comment=Browse and install Termux desktop software
Exec=${LAUNCHER_PATH}
Icon=${INSTALL_ROOT}/assets/icons/termux-app-store.svg
Terminal=false
Categories=System;Utility;
StartupNotify=true
EOF
}

main() {
    require_command pkg
    require_command python
    ensure_safe_install_root

    echo "Installing runtime packages..."
    pkg install -y x11-repo >/dev/null
    pkg install -y python gtk4 pygobject >/dev/null

    echo "Copying application files to ${INSTALL_ROOT}..."
    copy_project_files

    echo "Creating launcher at ${LAUNCHER_PATH}..."
    write_launcher

    echo "Writing install metadata..."
    write_manifest

    echo "Creating desktop entry at ${DESKTOP_FILE}..."
    write_desktop_entry

    echo
    echo "Installation complete."
    echo "Run with: termux-app-store"
}

main "$@"
