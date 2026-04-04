from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

os.environ.setdefault("GSK_RENDERER", "cairo")

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk


BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "src" / "data" / "apps.json"
ASSETS_DIR = BASE_DIR / "assets" / "icons"
APPSTORE_PACKAGE_ID = "termux-app-store"
APPSTORE_PACKAGE_NAME = "termux-app-store"
APPSTORE_REPO_URL = "https://github.com/HKHOP/TheGlobalTermux-AppStore.git"
APPSTORE_MANIFEST_FILE = BASE_DIR / ".termux_app_store_install.json"
CATALOG_MANIFEST_FILE = BASE_DIR / "src" / "data" / "catalog-manifest.json"
WINDOW_STATE_FILE = Path.home() / ".config" / "termux-app-store" / "window-state.json"
CACHE_DIR = Path.home() / ".cache" / "termux-app-store"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_CORE_DIR = CACHE_DIR / "core-update"
CACHE_CORE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_CATALOG_DIR = CACHE_DIR / "catalog-sync"
CACHE_CATALOG_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_REPO_BRANCH = "main"
CACHE_BUSTER = "20260404"
CATEGORY_FALLBACK_ICONS = {
    "Development": ["applications-development", "code-context", "text-x-script"],
    "Editors": ["accessories-text-editor", "text-editor", "document-edit"],
    "Media": ["multimedia-video-player", "applications-multimedia", "audio-x-generic"],
    "Security": ["security-high", "preferences-system-privacy", "network-workgroup"],
    "Internet": ["web-browser", "applications-internet", "network-workgroup"],
    "Office": ["x-office-document", "applications-office", "accessories-text-editor"],
    "Graphics": ["applications-graphics", "image-x-generic", "palette"],
    "Utilities": ["applications-utilities", "utilities-terminal", "applications-system"],
    "System": ["applications-system", "system-software-install", "preferences-system"],
}


def is_dark_gtk_theme(settings: Gtk.Settings | None) -> bool:
    if settings is None:
        return False

    prefer_dark = bool(settings.get_property("gtk-application-prefer-dark-theme"))
    theme_name = str(settings.get_property("gtk-theme-name") or "").lower()
    dark_markers = ("dark", "noir", "night", "black", "adwaita-dark")
    return prefer_dark or any(marker in theme_name for marker in dark_markers)


def load_packages() -> list[dict]:
    with DATA_FILE.open("r", encoding="utf-8") as file:
        apps = json.load(file)

    app_store_entry = {
        "id": APPSTORE_PACKAGE_ID,
        "packageName": APPSTORE_PACKAGE_NAME,
        "name": "Termux App Store",
        "category": "System",
        "summary": "Browse, install, and update software for your Termux desktop",
        "description": (
            "The store itself can now appear in the catalog, detect newer code on GitHub, "
            "and update from inside the UI."
        ),
        "tags": ["store", "updates", "github", "system"],
        "iconPath": "assets/icons/termux-app-store.svg",
        "iconName": "system-software-install",
        "source": "GitHub",
        "homepage": APPSTORE_REPO_URL.removesuffix(".git"),
        "maintainer": "HKHOP",
        "installCommand": "",
        "uninstallCommand": "",
        "installed": True,
        "isSelfPackage": True,
    }
    apps = [app_store_entry, *apps]

    for app in apps:
        app.setdefault("packageName", app.get("id", ""))
        app.setdefault("summary", app.get("description", ""))
        app.setdefault("tags", [])
        app.setdefault("iconPath", "")
        app.setdefault("iconName", "application-x-executable")
        app.setdefault("maintainer", "")
        app.setdefault("source", "Unknown source")
        app.setdefault("homepage", "")
        app.setdefault("uninstallCommand", "")
        app.setdefault("installCheckPath", "")
        app.setdefault("isSelfPackage", False)
        app.setdefault("updateCommand", app.get("installCommand", ""))
        app.setdefault("updateAvailable", False)
        app.setdefault("latestVersion", "")
        app.setdefault("currentVersion", "")
        app["installed"] = bool(app.get("installed", False))

    return apps


def detect_installed_packages() -> set[str]:
    commands = [
        ["dpkg-query", "-W", "-f=${binary:Package}\n"],
        ["dpkg", "--get-selections"],
    ]

    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue

        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            continue

        if command[0] == "dpkg":
            return {line.split()[0] for line in lines}

        return set(lines)

    return set()


def detect_upgradable_packages() -> tuple[set[str], dict[str, str]]:
    commands = [
        ["apt", "list", "--upgradable"],
        ["pkg", "list-upgradable"],
    ]

    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue

        packages: set[str] = set()
        latest_versions: dict[str, str] = {}
        for raw_line in completed.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.lower().startswith("listing"):
                continue
            if "/" not in line:
                continue

            package_name = line.split("/", 1)[0].strip()
            if not package_name:
                continue

            packages.add(package_name)
            latest_version = ""
            if " upgradable from: " in line:
                latest_version = line.split(" upgradable from: ", 1)[0].split()[-1].strip()
            elif " upgradable to: " in line:
                latest_version = line.split(" upgradable to: ", 1)[-1].strip()
            elif len(line.split()) > 1:
                latest_version = line.split()[1].strip()

            latest_versions[package_name] = latest_version

        return packages, latest_versions

    return set(), {}


def detect_installed_versions() -> dict[str, str]:
    commands = [
        ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"],
        ["dpkg", "-l"],
    ]

    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue

        versions: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            if command[0] == "dpkg-query":
                parts = stripped.split("\t", 1)
                if len(parts) == 2:
                    versions[parts[0].strip()] = parts[1].strip()
                continue

            if not stripped.startswith("ii"):
                continue
            parts = stripped.split()
            if len(parts) >= 3:
                versions[parts[1].strip()] = parts[2].strip()

        return versions

    return {}


def read_app_store_manifest() -> dict:
    if not APPSTORE_MANIFEST_FILE.exists():
        return {}

    try:
        return json.loads(APPSTORE_MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_app_store_manifest(updates: dict) -> None:
    manifest = read_app_store_manifest()
    manifest.update(updates)
    APPSTORE_MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _run_text_command(command: list[str], cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""

    return completed.stdout.strip()


def get_app_store_repo_url() -> str:
    git_dir = BASE_DIR / ".git"
    if git_dir.exists():
        repo_url = _run_text_command(["git", "config", "--get", "remote.origin.url"], cwd=BASE_DIR)
        if repo_url:
            return repo_url

    manifest_url = str(read_app_store_manifest().get("repo_url", "")).strip()
    return manifest_url or APPSTORE_REPO_URL


def get_app_store_branch() -> str:
    git_dir = BASE_DIR / ".git"
    if git_dir.exists():
        branch = _run_text_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=BASE_DIR)
        if branch and branch != "HEAD":
            return branch

    manifest_branch = str(read_app_store_manifest().get("branch", "")).strip()
    return manifest_branch or DEFAULT_REPO_BRANCH


def get_catalog_core_version(manifest: dict | None = None) -> str:
    catalog_manifest = manifest if manifest is not None else read_catalog_manifest()
    return str(catalog_manifest.get("core_version", "")).strip()


def get_local_app_store_version() -> str:
    installed_manifest = read_app_store_manifest()
    installed_core_version = str(installed_manifest.get("core_version", "")).strip()
    if installed_core_version:
        return installed_core_version

    installed_commit = str(installed_manifest.get("commit", "")).strip()
    if installed_commit:
        return installed_commit

    catalog_core_version = get_catalog_core_version()
    if catalog_core_version:
        return catalog_core_version

    git_dir = BASE_DIR / ".git"
    if git_dir.exists():
        commit = _run_text_command(["git", "rev-parse", "HEAD"], cwd=BASE_DIR)
        if commit:
            return commit

    return ""


def make_repo_web_base(repo_url: str) -> str:
    normalized = repo_url.removesuffix(".git")
    if normalized.startswith("git@github.com:"):
        normalized = normalized.replace("git@github.com:", "https://github.com/")
    return normalized


def make_raw_base(repo_url: str, branch: str) -> str:
    web_base = make_repo_web_base(repo_url)
    parsed = urllib.parse.urlparse(web_base)
    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.lower() != "github.com" or len(path_parts) < 2:
        return ""
    owner, repo = path_parts[:2]
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"


def read_catalog_manifest() -> dict:
    if not CATALOG_MANIFEST_FILE.exists():
        return {"apps_json": "src/data/apps.json", "icons": []}

    try:
        return json.loads(CATALOG_MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"apps_json": "src/data/apps.json", "icons": []}


def download_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def download_text(url: str) -> str:
    return download_bytes(url).decode("utf-8")


def expand_install_check_path(raw_path: str) -> Path | None:
    path_text = str(raw_path or "").strip()
    if not path_text:
        return None

    expanded = os.path.expandvars(os.path.expanduser(path_text))
    return Path(expanded)


def fetch_remote_catalog_manifest(repo_url: str, branch: str) -> dict:
    raw_base = make_raw_base(repo_url, branch)
    if not raw_base:
        raise ValueError("Could not determine the GitHub raw URL for catalog sync.")

    manifest_url = f"{raw_base}/src/data/catalog-manifest.json?v={CACHE_BUSTER}"
    return json.loads(download_text(manifest_url))


def get_remote_app_store_version(repo_url: str, branch: str) -> str:
    if not repo_url:
        return ""

    try:
        remote_manifest = fetch_remote_catalog_manifest(repo_url, branch)
    except (ValueError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return ""

    return get_catalog_core_version(remote_manifest)


def sync_remote_catalog(repo_url: str, branch: str) -> tuple[bool, str]:
    try:
        remote_manifest = fetch_remote_catalog_manifest(repo_url, branch)
        raw_base = make_raw_base(repo_url, branch)
        if not raw_base:
            return False, "Could not determine the GitHub raw URL for catalog sync."
    except (ValueError, urllib.error.URLError, json.JSONDecodeError, TimeoutError) as error:
        return False, f"Catalog manifest download failed: {error}"

    apps_path = Path(str(remote_manifest.get("apps_json", "src/data/apps.json")))
    icon_paths = [Path(path) for path in remote_manifest.get("icons", [])]

    try:
        apps_target = BASE_DIR / apps_path
        apps_target.parent.mkdir(parents=True, exist_ok=True)
        apps_url = f"{raw_base}/{apps_path.as_posix()}?v={CACHE_BUSTER}"
        apps_target.write_bytes(download_bytes(apps_url))

        for icon_path in icon_paths:
            icon_target = BASE_DIR / icon_path
            icon_target.parent.mkdir(parents=True, exist_ok=True)
            icon_url = f"{raw_base}/{icon_path.as_posix()}?v={CACHE_BUSTER}"
            icon_target.write_bytes(download_bytes(icon_url))

        CATALOG_MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        CATALOG_MANIFEST_FILE.write_text(json.dumps(remote_manifest, indent=2), encoding="utf-8")
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        return False, f"Catalog assets download failed: {error}"

    return True, f"Catalog synced ({len(icon_paths)} icons updated)."


def build_core_update_command(repo_url: str, branch: str) -> str:
    raw_base = make_raw_base(repo_url, branch)
    if not raw_base:
        return ""

    cache_core = shlex.quote(str(CACHE_CORE_DIR))
    raw_base_q = shlex.quote(raw_base)
    core_files = [
        "app.py",
        "install.sh",
        "uninstall.sh",
        "README.md",
        "LICENSE.txt",
        "src/data/catalog-manifest.json",
        "assets/icons/termux-app-store.svg",
    ]
    download_lines = []
    for relative_path in core_files:
        relative_q = shlex.quote(relative_path)
        target_path = shlex.quote(str(CACHE_CORE_DIR / relative_path))
        target_dir = shlex.quote(str((CACHE_CORE_DIR / relative_path).parent))
        download_lines.append(
            f"mkdir -p {target_dir} && curl -fsSL {raw_base_q}/{relative_path}?v={CACHE_BUSTER} -o {target_path}"
        )

    return (
        "pkg install -y curl && "
        f"rm -rf {cache_core} && mkdir -p {cache_core} && "
        + " && ".join(download_lines)
        + f" && cd {cache_core} && bash install.sh"
    )


def load_window_state() -> dict:
    if not WINDOW_STATE_FILE.exists():
        return {}

    try:
        return json.loads(WINDOW_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_window_state(width: int, height: int, maximized: bool) -> None:
    WINDOW_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "width": max(640, int(width)),
        "height": max(480, int(height)),
        "maximized": bool(maximized),
    }
    WINDOW_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def build_icon_widget(package: dict, size: int = 38) -> Gtk.Widget:
    icon_path = (package.get("iconPath") or "").strip()
    if icon_path:
        candidate = Path(icon_path)
        if not candidate.is_absolute():
            candidate = BASE_DIR / candidate
        if candidate.exists():
            image = Gtk.Image.new_from_file(str(candidate))
            image.set_pixel_size(size)
            image.add_css_class("package-icon")
            return image

    icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    icon_candidates: list[str] = []

    explicit_icon = (package.get("iconName") or "").strip()
    if explicit_icon:
        icon_candidates.append(explicit_icon)

    for key in ("packageName", "id"):
        value = (package.get(key) or "").strip()
        if value:
            icon_candidates.extend(
                [
                    value,
                    value.replace(".", "-"),
                    value.replace("-", "_"),
                    f"{value}-desktop",
                ]
            )

    icon_candidates.extend(CATEGORY_FALLBACK_ICONS.get(package.get("category", ""), []))
    icon_candidates.extend(["application-x-addon", "application-default-icon", "applications-other"])

    seen: set[str] = set()
    for icon_name in icon_candidates:
        if not icon_name or icon_name in seen:
            continue
        seen.add(icon_name)
        if icon_theme is not None and icon_theme.has_icon(icon_name):
            image = Gtk.Image.new_from_icon_name(icon_name)
            image.set_pixel_size(size)
            image.add_css_class("package-icon")
            return image

    return build_generated_icon_widget(package, size)


def build_generated_icon_widget(package: dict, size: int) -> Gtk.Widget:
    initial = (package.get("name") or package.get("packageName") or "?").strip()[:1].upper() or "?"
    category = str(package.get("category", "")).lower()
    accent_class = "generated-icon-system"
    if category in {"development", "editors"}:
        accent_class = "generated-icon-dev"
    elif category in {"media", "graphics"}:
        accent_class = "generated-icon-media"
    elif category in {"internet", "security"}:
        accent_class = "generated-icon-net"

    frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    frame.set_size_request(size, size)
    frame.set_halign(Gtk.Align.FILL)
    frame.set_valign(Gtk.Align.CENTER)
    frame.add_css_class("generated-icon")
    frame.add_css_class(accent_class)

    label = Gtk.Label(label=initial)
    label.set_halign(Gtk.Align.CENTER)
    label.set_valign(Gtk.Align.CENTER)
    label.add_css_class("generated-icon-label")
    frame.append(label)
    return frame


class PackageRow(Gtk.ListBoxRow):
    def __init__(self, package: dict) -> None:
        super().__init__()
        self.package_data = package

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.set_margin_top(6)
        card.set_margin_bottom(6)
        card.set_margin_start(6)
        card.set_margin_end(6)
        card.add_css_class("package-row")

        icon = build_icon_widget(package, 38)
        card.append(icon)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_hexpand(True)
        card.append(content)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        name = Gtk.Label()
        name.set_markup(f"<b>{GLib.markup_escape_text(package['name'])}</b>")
        name.set_xalign(0)
        name.set_hexpand(True)
        name.add_css_class("package-name")
        title_row.append(name)

        if package.get("updateAvailable"):
            update_label = Gtk.Label(label="Update available")
            update_label.set_xalign(1)
            update_label.add_css_class("update-pill")
            title_row.append(update_label)

        if package.get("installed"):
            installed_label = Gtk.Label(label="Installed")
            installed_label.set_xalign(1)
            installed_label.add_css_class("status-pill")
            title_row.append(installed_label)

        category = Gtk.Label(label=package["category"])
        category.set_xalign(1)
        category.add_css_class("package-pill")
        title_row.append(category)

        content.append(title_row)

        description = Gtk.Label(label=package["summary"])
        description.set_xalign(0)
        description.set_wrap(True)
        description.set_max_width_chars(38)
        description.add_css_class("package-summary")
        content.append(description)

        tags = Gtk.Label(label=" | ".join(package.get("tags", [])))
        tags.set_xalign(0)
        tags.add_css_class("package-tags")
        content.append(tags)

        self.set_child(card)


class CategoryRow(Gtk.ListBoxRow):
    def __init__(self, label_text: str) -> None:
        super().__init__()
        self.label_text = label_text

        label = Gtk.Label(label=label_text)
        label.set_xalign(0)
        label.set_margin_top(10)
        label.set_margin_bottom(10)
        label.set_margin_start(12)
        label.set_margin_end(12)
        self.set_child(label)


class ConfirmDialog(Gtk.MessageDialog):
    def __init__(self, parent: Gtk.Window, title: str, detail: str) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=title,
        )
        self.set_property("secondary-text", detail)


class InfoDialog(Gtk.MessageDialog):
    def __init__(self, parent: Gtk.Window, title: str, detail: str) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        self.set_property("secondary-text", detail)


class TermuxStoreWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Termux App Store")
        self.window_state = load_window_state()
        self._window_state_dirty = False
        self._window_state_save_source: int | None = None
        self.set_default_size(
            int(self.window_state.get("width", 1280)),
            int(self.window_state.get("height", 760)),
        )

        self.packages = load_packages()
        self.installed_packages = set()
        self.filtered_packages = self.packages[:]
        self.selected_package: dict | None = None
        self.current_category = "All"
        self.operation_in_progress = False
        self.progress_pulse_source: int | None = None
        self.settings = Gtk.Settings.get_default()
        self.suppress_row_activation = False
        self.installed_versions: dict[str, str] = {}
        self.upgradable_packages: set[str] = set()
        self.latest_package_versions: dict[str, str] = {}
        self.app_store_update_check_in_progress = False
        self.app_store_update_known = False
        self.app_store_update_available = False
        self.app_store_current_version = get_local_app_store_version()
        self.app_store_latest_version = ""
        self.app_store_repo_url = get_app_store_repo_url()
        self.app_store_branch = get_app_store_branch()
        self.catalog_sync_in_progress = False

        self._load_css()
        self._build_ui()
        self._setup_window_state_tracking()
        self._setup_theme_monitor()
        self._sync_theme()
        self._refresh_installed_state()
        self._populate_categories()
        self.refresh_package_list()
        self._apply_saved_window_state()
        self._sync_catalog_async(silent=True)

    def _load_css(self) -> None:
        css = b"""
        window {
            background: #f6f7f8;
        }

        headerbar {
            background: #ffffff;
            border-bottom: 1px solid #d8dee4;
            box-shadow: none;
        }

        .sidebar {
            background: #eef2f5;
            border-right: 1px solid #d8dee4;
            border-radius: 18px;
        }

        .content-panel {
            background: #f6f7f8;
        }

        .detail-panel {
            background: #ffffff;
            border-left: 1px solid #d8dee4;
        }

        .browse-shell {
            background: #f6f7f8;
        }

        .details-shell {
            background: #f6f7f8;
        }

        .details-hero {
            background: #ffffff;
            border: 1px solid #d8dee4;
            border-radius: 24px;
            padding: 18px;
        }

        .details-app-header {
            spacing: 18px;
        }

        .details-app-icon {
            min-width: 88px;
            min-height: 88px;
            background: linear-gradient(135deg, #f472b6 0%, #60a5fa 52%, #facc15 100%);
            border-radius: 26px;
            padding: 14px;
        }

        .details-app-icon .package-icon {
            color: #ffffff;
        }

        .details-title-block {
            spacing: 6px;
        }

        .detail-subtitle {
            color: #4b5563;
            font-size: 18px;
        }

        .detail-support {
            color: #6b7280;
            font-size: 13px;
        }

        .preview-strip {
            spacing: 12px;
        }

        .preview-card {
            min-height: 156px;
            background: linear-gradient(180deg, rgba(17,24,39,0.12) 0%, rgba(17,24,39,0.04) 100%);
            border: 1px solid #d8dee4;
            border-radius: 18px;
            padding: 16px;
        }

        .preview-card-primary {
            background: linear-gradient(135deg, #c7e0ff 0%, #8ec5ff 45%, #d8f3ff 100%);
        }

        .preview-card-secondary {
            background: linear-gradient(135deg, #dfe6ee 0%, #f8fafc 100%);
        }

        .preview-card-tertiary {
            background: linear-gradient(135deg, #bfd6ea 0%, #d7e7f7 100%);
        }

        .preview-card .package-icon {
            color: rgba(15, 23, 42, 0.75);
        }

        .preview-title {
            color: #111827;
            font-size: 14px;
            font-weight: 700;
        }

        .preview-copy {
            color: #4b5563;
            font-size: 12px;
        }

        .details-section {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 20px;
            padding: 18px;
        }

        .details-toolbar {
            background: transparent;
        }

        .details-command {
            min-height: 120px;
        }

        .empty-state {
            background: #ffffff;
            border: 1px dashed #cbd5e1;
            border-radius: 24px;
            padding: 24px;
        }

        .empty-title {
            color: #111827;
            font-size: 26px;
            font-weight: 800;
        }

        .empty-copy {
            color: #6b7280;
            font-size: 14px;
        }

        .section-title {
            color: #1f2937;
            font-size: 18px;
            font-weight: 700;
        }

        .section-subtitle {
            color: #6b7280;
            font-size: 13px;
        }

        .package-row {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 8px;
        }

        .package-list row {
            background: transparent;
            border-radius: 14px;
            margin: 0 0 6px 0;
            padding: 0;
            outline: none;
            box-shadow: none;
        }

        .package-list row:selected,
        .package-list row:selected:hover,
        .package-list row:focus,
        .package-list row:focus-within {
            background: #dbeafe;
            border-radius: 14px;
            outline: none;
            box-shadow: none;
        }

        .package-list row:selected .package-row,
        .package-list row:selected:hover .package-row {
            border-color: #93c5fd;
            background: #f8fbff;
        }

        .package-name {
            color: #111827;
            font-size: 15px;
        }

        .package-icon {
            color: #2563eb;
        }

        .generated-icon {
            border-radius: 18px;
            background: linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%);
            padding: 0;
        }

        .generated-icon-dev {
            background: linear-gradient(135deg, #bfdbfe 0%, #93c5fd 100%);
        }

        .generated-icon-media {
            background: linear-gradient(135deg, #fde68a 0%, #f9a8d4 100%);
        }

        .generated-icon-net {
            background: linear-gradient(135deg, #86efac 0%, #67e8f9 100%);
        }

        .generated-icon-system {
            background: linear-gradient(135deg, #d1d5db 0%, #93c5fd 100%);
        }

        .generated-icon-label {
            color: #0f172a;
            font-size: 20px;
            font-weight: 800;
        }

        .package-summary {
            color: #6b7280;
        }

        .package-tags {
            color: #2563eb;
            font-size: 12px;
        }

        .package-pill {
            background: #e8f0fe;
            color: #1d4ed8;
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 12px;
        }

        .status-pill {
            background: #ecfdf3;
            color: #15803d;
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 12px;
        }

        .update-pill {
            background: #fff4cc;
            color: #92400e;
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 12px;
        }

        .hero-card {
            background: #155e75;
            border-radius: 16px;
            color: #ffffff;
            padding: 8px;
        }

        .hero-title {
            color: #ffffff;
            font-size: 28px;
            font-weight: 800;
        }

        .hero-copy {
            color: rgba(255,255,255,0.88);
            font-size: 14px;
        }

        .detail-title {
            color: #111827;
            font-size: 26px;
            font-weight: 800;
        }

        .detail-meta {
            color: #6b7280;
            font-size: 13px;
        }

        .detail-description {
            color: #374151;
            font-size: 14px;
        }

        .body-copy {
            color: #374151;
            font-size: 15px;
            line-height: 1.45;
        }

        .install-label {
            color: #111827;
            font-weight: 700;
        }

        .status-installed {
            color: #15803d;
            font-weight: 700;
        }

        .status-missing {
            color: #b45309;
            font-weight: 700;
        }

        textview {
            background: #0f172a;
            color: #a7f3d0;
            border-radius: 10px;
            padding: 10px;
        }

        .status-label {
            color: #6b7280;
            font-size: 12px;
        }

        .category-list row:selected {
            background: #dbeafe;
            border-radius: 10px;
        }

        window.dark {
            background: #10161d;
            color: #e5edf5;
        }

        window.dark headerbar {
            background: #18212b;
            border-bottom: 1px solid #2a3642;
        }

        window.dark .sidebar {
            background: #131b24;
            border-right: 1px solid #2a3642;
        }

        window.dark .content-panel {
            background: #10161d;
        }

        window.dark .detail-panel {
            background: #18212b;
            border-left: 1px solid #2a3642;
        }

        window.dark .details-shell {
            background: #10161d;
        }

        window.dark .details-hero,
        window.dark .details-section,
        window.dark .empty-state {
            background: #18212b;
            border-color: #2a3642;
        }

        window.dark .details-app-icon {
            background: linear-gradient(135deg, #7c3aed 0%, #2563eb 50%, #0f766e 100%);
        }

        window.dark .detail-subtitle {
            color: #d3dbe5;
        }

        window.dark .detail-support {
            color: #9ba9b8;
        }

        window.dark .preview-card {
            border-color: #2a3642;
        }

        window.dark .preview-card-primary {
            background: linear-gradient(135deg, #124466 0%, #1f5c8e 50%, #2d7aa8 100%);
        }

        window.dark .preview-card-secondary {
            background: linear-gradient(135deg, #202c38 0%, #293646 100%);
        }

        window.dark .preview-card-tertiary {
            background: linear-gradient(135deg, #223445 0%, #2b4153 100%);
        }

        window.dark .preview-card .package-icon {
            color: rgba(255, 255, 255, 0.88);
        }

        window.dark .preview-title {
            color: #f4f7fb;
        }

        window.dark .preview-copy {
            color: #c2ccd8;
        }

        window.dark .section-title {
            color: #f4f7fb;
        }

        window.dark .section-subtitle {
            color: #9ba9b8;
        }

        window.dark .package-row {
            background: #18212b;
            border: 1px solid #2a3642;
        }

        window.dark .package-list row:selected,
        window.dark .package-list row:selected:hover,
        window.dark .package-list row:focus,
        window.dark .package-list row:focus-within {
            background: #1c3149;
        }

        window.dark .package-list row:selected .package-row,
        window.dark .package-list row:selected:hover .package-row {
            border-color: #4d84bd;
            background: #1d2834;
        }

        window.dark .package-name {
            color: #f4f7fb;
        }

        window.dark .package-icon {
            color: #7cc7ff;
        }

        window.dark .generated-icon {
            background: linear-gradient(135deg, #1d3557 0%, #274c77 100%);
        }

        window.dark .generated-icon-dev {
            background: linear-gradient(135deg, #1d4ed8 0%, #2563eb 100%);
        }

        window.dark .generated-icon-media {
            background: linear-gradient(135deg, #9d174d 0%, #7c3aed 100%);
        }

        window.dark .generated-icon-net {
            background: linear-gradient(135deg, #0f766e 0%, #0369a1 100%);
        }

        window.dark .generated-icon-system {
            background: linear-gradient(135deg, #334155 0%, #475569 100%);
        }

        window.dark .generated-icon-label {
            color: #f8fafc;
        }

        window.dark .package-summary {
            color: #a9b6c4;
        }

        window.dark .package-tags {
            color: #7cc7ff;
        }

        window.dark .package-pill {
            background: #223244;
            color: #9fd3ff;
        }

        window.dark .status-pill {
            background: #173223;
            color: #87d8a8;
        }

        window.dark .update-pill {
            background: #4b3313;
            color: #f7d28a;
        }

        window.dark .hero-card {
            background: #0f4b63;
        }

        window.dark .hero-title {
            color: #ffffff;
        }

        window.dark .hero-copy {
            color: rgba(255,255,255,0.82);
        }

        window.dark .detail-title {
            color: #f4f7fb;
        }

        window.dark .detail-meta {
            color: #9ba9b8;
        }

        window.dark .detail-description {
            color: #d3dbe5;
        }

        window.dark .body-copy {
            color: #d3dbe5;
        }

        window.dark .empty-title {
            color: #f4f7fb;
        }

        window.dark .empty-copy {
            color: #9ba9b8;
        }

        window.dark .install-label {
            color: #f4f7fb;
        }

        window.dark .status-installed {
            color: #87d8a8;
        }

        window.dark .status-missing {
            color: #f0c674;
        }

        window.dark textview {
            background: #0b1220;
            color: #9af0c3;
        }

        window.dark .status-label {
            color: #9ba9b8;
        }

        window.dark .category-list row:selected {
            background: #1c3149;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _setup_theme_monitor(self) -> None:
        if self.settings is None:
            return

        self.settings.connect("notify::gtk-theme-name", self._on_theme_settings_changed)
        self.settings.connect(
            "notify::gtk-application-prefer-dark-theme",
            self._on_theme_settings_changed,
        )

    def _on_theme_settings_changed(self, *_args: object) -> None:
        self._sync_theme()

    def _sync_theme(self) -> None:
        if is_dark_gtk_theme(self.settings):
            self.add_css_class("dark")
        else:
            self.remove_css_class("dark")

    def _setup_window_state_tracking(self) -> None:
        self.connect("close-request", self._on_close_request)
        self.connect("notify::maximized", self._on_window_state_changed)
        self.connect("notify::default-width", self._on_window_state_changed)
        self.connect("notify::default-height", self._on_window_state_changed)

    def _apply_saved_window_state(self) -> None:
        if self.window_state.get("maximized"):
            self.maximize()

    def _on_window_state_changed(self, *_args: object) -> None:
        self._schedule_window_state_save()

    def _schedule_window_state_save(self) -> None:
        self._window_state_dirty = True
        if self._window_state_save_source is not None:
            return
        self._window_state_save_source = GLib.timeout_add(250, self._flush_window_state_save)

    def _flush_window_state_save(self) -> bool:
        self._window_state_save_source = None
        if not self._window_state_dirty:
            return False

        self._window_state_dirty = False
        width = self.get_default_width()
        height = self.get_default_height()
        if width <= 0:
            width = self.get_width()
        if height <= 0:
            height = self.get_height()
        save_window_state(width, height, self.is_maximized())
        return False

    def _on_close_request(self, *_args: object) -> bool:
        self._flush_window_state_save()
        return False

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(root)

        header = Gtk.HeaderBar()
        header.set_show_title_buttons(True)
        self.set_titlebar(header)

        self.back_button = Gtk.Button(label="Back")
        self.back_button.connect("clicked", self._go_back_to_browse)
        self.back_button.set_visible(False)
        header.pack_start(self.back_button)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_label = Gtk.Label(label="Termux App Store")
        title_label.add_css_class("section-title")
        title_label.set_xalign(0)
        subtitle_label = Gtk.Label(label="Browse and install Termux and X11 apps")
        subtitle_label.add_css_class("section-subtitle")
        subtitle_label.set_xalign(0)
        self.title_label = title_label
        self.subtitle_label = subtitle_label
        title_box.append(title_label)
        title_box.append(subtitle_label)
        header.set_title_widget(title_box)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search apps, tools, and tags")
        self.search_entry.set_size_request(300, -1)
        self.search_entry.connect("search-changed", self._on_filters_changed)
        header.pack_end(self.search_entry)

        refresh_button = Gtk.Button(label="Refresh")
        refresh_button.connect("clicked", self._reload_catalog)
        header.pack_end(refresh_button)

        self.view_stack = Gtk.Stack()
        self.view_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.view_stack.set_transition_duration(260)
        root.append(self.view_stack)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        main.add_css_class("browse-shell")
        self.view_stack.add_named(main, "browse")

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sidebar.set_size_request(220, -1)
        sidebar.set_margin_top(18)
        sidebar.set_margin_bottom(18)
        sidebar.set_margin_start(14)
        sidebar.set_margin_end(14)
        sidebar.add_css_class("sidebar")
        main.append(sidebar)

        sidebar_title = Gtk.Label(label="Categories")
        sidebar_title.set_xalign(0)
        sidebar_title.add_css_class("section-title")
        sidebar.append(sidebar_title)

        sidebar_subtitle = Gtk.Label(label="Filter the catalog")
        sidebar_subtitle.set_xalign(0)
        sidebar_subtitle.add_css_class("section-subtitle")
        sidebar.append(sidebar_subtitle)

        category_scroller = Gtk.ScrolledWindow()
        category_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        category_scroller.set_vexpand(True)
        sidebar.append(category_scroller)

        self.category_list = Gtk.ListBox()
        self.category_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.category_list.connect("row-selected", self._on_category_selected)
        self.category_list.add_css_class("category-list")
        category_scroller.set_child(self.category_list)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        content.set_hexpand(True)
        content.add_css_class("content-panel")
        main.append(content)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hero.set_margin_bottom(4)
        hero.add_css_class("hero-card")
        content.append(hero)

        hero_title = Gtk.Label(label="Software for your Termux desktop")
        hero_title.set_xalign(0)
        hero_title.add_css_class("hero-title")
        hero.append(hero_title)

        hero_copy = Gtk.Label(
            label="Search curated packages, inspect commands, and launch installs without leaving your X11 session."
        )
        hero_copy.set_xalign(0)
        hero_copy.set_wrap(True)
        hero_copy.add_css_class("hero-copy")
        hero.append(hero_copy)

        list_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        content.append(list_header)

        list_title = Gtk.Label(label="Explore")
        list_title.set_xalign(0)
        list_title.set_hexpand(True)
        list_title.add_css_class("section-title")
        list_header.append(list_title)

        self.results_label = Gtk.Label(label="0 packages")
        self.results_label.set_xalign(1)
        self.results_label.add_css_class("section-subtitle")
        list_header.append(self.results_label)

        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroller.set_vexpand(True)
        content.append(list_scroller)

        self.package_list = Gtk.ListBox()
        self.package_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.package_list.connect("row-selected", self._on_row_selected)
        self.package_list.add_css_class("package-list")
        list_scroller.set_child(self.package_list)

        details_shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        details_shell.add_css_class("details-shell")
        self.view_stack.add_named(details_shell, "details")

        details_scroller = Gtk.ScrolledWindow()
        details_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        details_scroller.set_vexpand(True)
        details_shell.append(details_scroller)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        details.set_margin_top(22)
        details.set_margin_bottom(22)
        details.set_margin_start(22)
        details.set_margin_end(22)
        details_scroller.set_child(details)

        details_hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        details_hero.add_css_class("details-hero")
        details.append(details_hero)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.add_css_class("details-toolbar")
        details_hero.append(toolbar)

        self.hero_category_label = Gtk.Label(label="")
        self.hero_category_label.add_css_class("package-pill")
        toolbar.append(self.hero_category_label)

        self.hero_state_label = Gtk.Label(label="")
        self.hero_state_label.add_css_class("status-pill")
        toolbar.append(self.hero_state_label)

        app_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        app_header.add_css_class("details-app-header")
        details_hero.append(app_header)

        self.detail_icon_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.detail_icon_frame.add_css_class("details-app-icon")
        app_header.append(self.detail_icon_frame)

        title_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        title_block.set_hexpand(True)
        title_block.add_css_class("details-title-block")
        app_header.append(title_block)

        self.name_label = Gtk.Label(label="Select a package")
        self.name_label.set_xalign(0)
        self.name_label.add_css_class("detail-title")
        title_block.append(self.name_label)

        self.detail_subtitle_label = Gtk.Label(label="")
        self.detail_subtitle_label.set_xalign(0)
        self.detail_subtitle_label.set_wrap(True)
        self.detail_subtitle_label.add_css_class("detail-subtitle")
        title_block.append(self.detail_subtitle_label)

        self.detail_support_label = Gtk.Label(label="")
        self.detail_support_label.set_xalign(0)
        self.detail_support_label.add_css_class("detail-support")
        title_block.append(self.detail_support_label)

        self.meta_label = Gtk.Label(label="")
        self.meta_label.set_xalign(0)
        self.meta_label.add_css_class("detail-meta")
        details_hero.append(self.meta_label)

        self.install_state_label = Gtk.Label(label="")
        self.install_state_label.set_xalign(0)
        self.install_state_label.add_css_class("detail-meta")
        details_hero.append(self.install_state_label)

        self.preview_strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.preview_strip.add_css_class("preview-strip")
        details_hero.append(self.preview_strip)

        self.preview_cards: list[tuple[Gtk.Box, Gtk.Box, Gtk.Label, Gtk.Label]] = []
        for css_class in ("preview-card-primary", "preview-card-secondary", "preview-card-tertiary"):
            preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            preview.set_hexpand(True)
            preview.add_css_class("preview-card")
            preview.add_css_class(css_class)

            icon_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            preview.append(icon_holder)

            preview_title = Gtk.Label(label="")
            preview_title.set_xalign(0)
            preview_title.add_css_class("preview-title")
            preview.append(preview_title)

            preview_copy = Gtk.Label(label="")
            preview_copy.set_xalign(0)
            preview_copy.set_wrap(True)
            preview_copy.add_css_class("preview-copy")
            preview.append(preview_copy)

            self.preview_strip.append(preview)
            self.preview_cards.append((preview, icon_holder, preview_title, preview_copy))

        actions_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        actions_section.add_css_class("details-section")
        details.append(actions_section)

        actions_title = Gtk.Label(label="Actions")
        actions_title.set_xalign(0)
        actions_title.add_css_class("install-label")
        actions_section.append(actions_title)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions_section.append(button_row)

        self.install_button = Gtk.Button(label="Install")
        self.install_button.connect("clicked", self._run_install_command)
        button_row.append(self.install_button)

        self.update_button = Gtk.Button(label="Update")
        self.update_button.connect("clicked", self._run_update_command)
        button_row.append(self.update_button)

        self.uninstall_button = Gtk.Button(label="Uninstall")
        self.uninstall_button.connect("clicked", self._run_uninstall_command)
        button_row.append(self.uninstall_button)

        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self._copy_command)
        button_row.append(copy_button)

        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_xalign(0)
        self.status_label.add_css_class("status-label")
        actions_section.append(self.status_label)

        self.operation_progress = Gtk.ProgressBar()
        self.operation_progress.set_show_text(True)
        self.operation_progress.set_text("Idle")
        self.operation_progress.set_fraction(0.0)
        actions_section.append(self.operation_progress)

        about_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        about_section.add_css_class("details-section")
        details.append(about_section)

        about_title = Gtk.Label(label="About this package")
        about_title.set_xalign(0)
        about_title.add_css_class("install-label")
        about_section.append(about_title)

        self.source_label = Gtk.Label(label="")
        self.source_label.set_xalign(0)
        self.source_label.add_css_class("detail-meta")
        about_section.append(self.source_label)

        self.maintainer_label = Gtk.Label(label="")
        self.maintainer_label.set_xalign(0)
        self.maintainer_label.add_css_class("detail-meta")
        about_section.append(self.maintainer_label)

        self.description_label = Gtk.Label(label="Choose something from the catalog to inspect it here.")
        self.description_label.set_xalign(0)
        self.description_label.set_wrap(True)
        self.description_label.set_max_width_chars(90)
        self.description_label.add_css_class("detail-description")
        about_section.append(self.description_label)

        self.description_extra_label = Gtk.Label(label="")
        self.description_extra_label.set_xalign(0)
        self.description_extra_label.set_wrap(True)
        self.description_extra_label.set_max_width_chars(90)
        self.description_extra_label.add_css_class("body-copy")
        about_section.append(self.description_extra_label)

        tags_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        tags_section.add_css_class("details-section")
        details.append(tags_section)

        tags_title = Gtk.Label(label="Tags")
        tags_title.set_xalign(0)
        tags_title.add_css_class("install-label")
        tags_section.append(tags_title)

        self.tags_label = Gtk.Label(label="")
        self.tags_label.set_xalign(0)
        self.tags_label.set_wrap(True)
        self.tags_label.add_css_class("detail-meta")
        tags_section.append(self.tags_label)

        install_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        install_section.add_css_class("details-section")
        details.append(install_section)

        install_title = Gtk.Label(label="Live command console")
        install_title.set_xalign(0)
        install_title.add_css_class("install-label")
        install_section.append(install_title)

        command_scroller = Gtk.ScrolledWindow()
        command_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        command_scroller.set_min_content_height(120)
        command_scroller.add_css_class("details-command")
        install_section.append(command_scroller)

        self.command_view = Gtk.TextView()
        self.command_view.set_editable(False)
        self.command_view.set_cursor_visible(False)
        self.command_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.command_view.set_monospace(True)
        command_scroller.set_child(self.command_view)

        self.empty_state = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.empty_state.set_margin_top(12)
        self.empty_state.add_css_class("empty-state")
        details.append(self.empty_state)

        empty_title = Gtk.Label(label="Open an app page")
        empty_title.set_xalign(0)
        empty_title.add_css_class("empty-title")
        self.empty_state.append(empty_title)

        empty_copy = Gtk.Label(
            label="Choose something from the catalog to open a dedicated details screen with install actions and metadata."
        )
        empty_copy.set_xalign(0)
        empty_copy.set_wrap(True)
        empty_copy.add_css_class("empty-copy")
        self.empty_state.append(empty_copy)

        self._set_details_visible(False)
        self._show_browse_view()

    def _populate_categories(self) -> None:
        self._clear_listbox(self.category_list)

        categories = ["All", *sorted({item["category"] for item in self.packages})]
        for category in categories:
            self.category_list.append(CategoryRow(category))

        first_row = self.category_list.get_row_at_index(0)
        if first_row is not None:
            self.category_list.select_row(first_row)

    def _on_category_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        self.current_category = row.label_text
        self.refresh_package_list()

    def _on_filters_changed(self, _widget: Gtk.Widget) -> None:
        self.refresh_package_list()

    def refresh_package_list(self) -> None:
        query = self.search_entry.get_text().strip().lower()
        visible_view = self.view_stack.get_visible_child_name() if hasattr(self, "view_stack") else "browse"
        previous_id = self.selected_package.get("id") if self.selected_package else None

        self.filtered_packages = []
        for package in self.packages:
            search_text = " ".join(
                [
                    package["name"],
                    package["summary"],
                    package["description"],
                    package["category"],
                    *package.get("tags", []),
                ]
            ).lower()

            matches_query = not query or query in search_text
            matches_category = self.current_category == "All" or package["category"] == self.current_category
            if matches_query and matches_category:
                self.filtered_packages.append(package)

        self._clear_listbox(self.package_list)
        for package in self.filtered_packages:
            self.package_list.append(PackageRow(package))

        count = len(self.filtered_packages)
        self.results_label.set_text(f"{count} package{'s' if count != 1 else ''}")

        if self.filtered_packages:
            selected_index = 0
            if previous_id is not None:
                for index, package in enumerate(self.filtered_packages):
                    if package.get("id") == previous_id:
                        selected_index = index
                        break

            row_to_select = self.package_list.get_row_at_index(selected_index)
            if row_to_select is not None:
                self.suppress_row_activation = True
                self.package_list.select_row(row_to_select)
                self.suppress_row_activation = False
                self.show_package(row_to_select.package_data)
                if visible_view == "details":
                    self._show_details_view()
        else:
            self.clear_details()

    def _on_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None or self.suppress_row_activation:
            return
        self.show_package(row.package_data)
        self._show_details_view()

    def show_package(self, package: dict) -> None:
        self.selected_package = package
        self.name_label.set_text(package["name"])
        self.hero_category_label.set_text(package["category"])
        hero_state = "Available"
        if package.get("updateAvailable"):
            hero_state = "Update available"
        elif package.get("installed"):
            hero_state = "Installed"
        self.hero_state_label.set_text(hero_state)
        self.hero_state_label.remove_css_class("status-pill")
        self.hero_state_label.remove_css_class("update-pill")
        self.hero_state_label.add_css_class("update-pill" if package.get("updateAvailable") else "status-pill")

        meta_parts = [f"Package: {package['packageName']}"]
        if package.get("currentVersion"):
            meta_parts.append(f"Installed version: {package['currentVersion'][:12]}")
        if package.get("latestVersion"):
            meta_parts.append(f"Latest: {package['latestVersion'][:12]}")
        self.meta_label.set_text(" | ".join(meta_parts))
        self.detail_subtitle_label.set_text(package["summary"])
        support_bits = [package["category"]]
        if package.get("source"):
            support_bits.append(package["source"])
        if package.get("homepage"):
            support_bits.append(package["homepage"].replace("https://", "").replace("http://", ""))
        self.detail_support_label.set_text(" • ".join(support_bits))

        if package.get("updateAvailable"):
            install_state = "An update is ready to install"
        elif package.get("installed"):
            install_state = "Installed in Termux"
        else:
            install_state = "Not currently installed"
        self.install_state_label.set_text(install_state)
        self.install_state_label.remove_css_class("status-installed")
        self.install_state_label.remove_css_class("status-missing")
        self.install_state_label.add_css_class(
            "status-installed" if package.get("installed") or package.get("updateAvailable") else "status-missing"
        )
        self.source_label.set_text(f"Source: {package['source']}")
        self.maintainer_label.set_text(
            f"Maintainer: {package['maintainer']}" if package.get("maintainer") else ""
        )
        self.description_label.set_text(package["description"])
        self.description_extra_label.set_text(self._build_description_extension(package))
        self.tags_label.set_text(", ".join(package.get("tags", [])) or "No tags")
        displayed_command = package.get("updateCommand") if package.get("updateAvailable") else package.get("installCommand")
        self._set_console_text(self._format_command_preview(displayed_command or ""))
        self.install_button.set_sensitive(bool(package.get("installCommand")) and not package.get("installed"))
        self.update_button.set_sensitive(bool(package.get("updateAvailable") and package.get("updateCommand")))
        self.uninstall_button.set_sensitive(bool(package.get("installed") and package.get("uninstallCommand")))
        self._set_box_content(self.detail_icon_frame, build_icon_widget(package, 56))
        self._populate_preview_cards(package)
        self.status_label.set_text(f"Selected {package['name']}")
        self._set_details_visible(True)

    def clear_details(self) -> None:
        self.selected_package = None
        self.name_label.set_text("No package selected")
        self.hero_category_label.set_text("")
        self.hero_state_label.set_text("")
        self.hero_state_label.remove_css_class("update-pill")
        self.hero_state_label.add_css_class("status-pill")
        self.meta_label.set_text("")
        self.detail_subtitle_label.set_text("")
        self.detail_support_label.set_text("")
        self.install_state_label.set_text("")
        self.install_state_label.remove_css_class("status-installed")
        self.install_state_label.remove_css_class("status-missing")
        self.source_label.set_text("")
        self.maintainer_label.set_text("")
        self.description_label.set_text("No packages matched your filters.")
        self.description_extra_label.set_text("")
        self.tags_label.set_text("")
        self._set_console_text("")
        self.install_button.set_sensitive(False)
        self.update_button.set_sensitive(False)
        self.uninstall_button.set_sensitive(False)
        self._set_box_content(self.detail_icon_frame, None)
        for _, icon_holder, preview_title, preview_copy in self.preview_cards:
            self._set_box_content(icon_holder, None)
            preview_title.set_text("")
            preview_copy.set_text("")
        self.status_label.set_text("No packages available")
        self._set_details_visible(False)
        self._show_browse_view()

    def _build_description_extension(self, package: dict) -> str:
        tag_text = ", ".join(package.get("tags", [])[:4])
        if tag_text:
            return (
                f"This package fits naturally into a {package['category'].lower()} workflow and is especially useful "
                f"when you want quick access to {tag_text}. The store page is laid out to make the install path, "
                "source, and current package status easier to scan at a glance."
            )
        return (
            f"This package fits naturally into a {package['category'].lower()} workflow. The store page is laid out "
            "to make the install path, source, and current package status easier to scan at a glance."
        )

    def _populate_preview_cards(self, package: dict) -> None:
        preview_rows = [
            ("Overview", package["summary"]),
            ("Highlights", ", ".join(package.get("tags", [])[:3]) or package["category"]),
            ("Install flow", package.get("updateCommand") if package.get("updateAvailable") else package.get("installCommand") or "No command available"),
        ]
        for (_, icon_holder, preview_title, preview_copy), (title, copy) in zip(self.preview_cards, preview_rows):
            self._set_box_content(icon_holder, build_icon_widget(package, 34))
            preview_title.set_text(title)
            preview_copy.set_text(copy[:90])

    def _set_details_visible(self, visible: bool) -> None:
        self.hero_category_label.set_visible(visible)
        self.hero_state_label.set_visible(visible)
        self.detail_icon_frame.set_visible(visible)
        self.name_label.set_visible(visible)
        self.detail_subtitle_label.set_visible(visible)
        self.detail_support_label.set_visible(visible)
        self.meta_label.set_visible(visible)
        self.install_state_label.set_visible(visible)
        self.preview_strip.set_visible(visible)
        self.source_label.set_visible(visible)
        self.maintainer_label.set_visible(visible)
        self.description_label.set_visible(visible)
        self.description_extra_label.set_visible(visible)
        self.tags_label.set_visible(visible)
        self.command_view.set_visible(visible)
        self.install_button.set_visible(visible)
        self.update_button.set_visible(visible)
        self.uninstall_button.set_visible(visible)
        self.empty_state.set_visible(not visible)

    def _show_browse_view(self) -> None:
        self.view_stack.set_visible_child_name("browse")
        self.back_button.set_visible(False)
        self.search_entry.set_visible(True)
        self.title_label.set_text("Termux App Store")
        self.subtitle_label.set_text("Browse and install Termux and X11 apps")

    def _show_details_view(self) -> None:
        if self.selected_package is None:
            return

        self.view_stack.set_visible_child_name("details")
        self.back_button.set_visible(True)
        self.search_entry.set_visible(False)
        self.title_label.set_text(self.selected_package["name"])
        self.subtitle_label.set_text(self.selected_package["category"])

    def _go_back_to_browse(self, _button: Gtk.Button) -> None:
        self._show_browse_view()

    def _copy_command(self, _button: Gtk.Button) -> None:
        if not self.selected_package:
            return

        clipboard = Gdk.Display.get_default().get_clipboard()
        command = self.selected_package.get("updateCommand") if self.selected_package.get("updateAvailable") else self.selected_package.get("installCommand")
        clipboard.set(command or "")
        self.status_label.set_text("Command copied")

    def _format_command_preview(self, command: str) -> str:
        if not command.strip():
            return "No command available for this package yet."
        return f"$ {command}\n\nReady to run from inside the store."

    def _set_console_text(self, text: str) -> None:
        self.command_view.get_buffer().set_text(text)

    def _append_console_text(self, text: str) -> bool:
        buffer = self.command_view.get_buffer()
        end_iter = buffer.get_end_iter()
        buffer.insert(end_iter, text)
        return False

    def _run_install_command(self, _button: Gtk.Button) -> None:
        if not self.selected_package:
            self._show_info("No package selected", "Choose a package first.")
            return
        if self.operation_in_progress:
            self._show_info("Operation in progress", "Please wait for the current command to finish.")
            return

        command = self.selected_package["installCommand"]
        package_name = self.selected_package["name"]
        self._show_confirm(
            f"Install {package_name}?",
            command,
            lambda confirmed: self._start_install(command, package_name) if confirmed else None,
        )

    def _run_update_command(self, _button: Gtk.Button) -> None:
        if not self.selected_package:
            self._show_info("No package selected", "Choose a package first.")
            return
        if self.operation_in_progress:
            self._show_info("Operation in progress", "Please wait for the current command to finish.")
            return

        command = self.selected_package.get("updateCommand", "")
        package_name = self.selected_package["name"]
        if not command:
            self._show_info("Update unavailable", "No update command is configured for this app.")
            return

        self._show_confirm(
            f"Update {package_name}?",
            command,
            lambda confirmed: self._start_update(command, package_name) if confirmed else None,
        )

    def _run_uninstall_command(self, _button: Gtk.Button) -> None:
        if not self.selected_package:
            self._show_info("No package selected", "Choose a package first.")
            return
        if self.operation_in_progress:
            self._show_info("Operation in progress", "Please wait for the current command to finish.")
            return

        command = self.selected_package.get("uninstallCommand", "")
        package_name = self.selected_package["name"]
        if not command:
            self._show_info("Uninstall unavailable", "No uninstall command is configured for this app.")
            return

        self._show_confirm(
            f"Uninstall {package_name}?",
            command,
            lambda confirmed: self._start_uninstall(command, package_name) if confirmed else None,
        )

    def _start_install(self, command: str, package_name: str) -> None:
        self._start_command_execution(
            command=command,
            package_name=package_name,
            action_label="Installing",
        )

    def _start_uninstall(self, command: str, package_name: str) -> None:
        self._start_command_execution(
            command=command,
            package_name=package_name,
            action_label="Uninstalling",
        )

    def _start_update(self, command: str, package_name: str) -> None:
        self._start_command_execution(
            command=command,
            package_name=package_name,
            action_label="Updating",
        )

    def _start_command_execution(self, command: str, package_name: str, action_label: str) -> None:
        self.operation_in_progress = True
        self.install_button.set_sensitive(False)
        self.update_button.set_sensitive(False)
        self.uninstall_button.set_sensitive(False)
        self.operation_progress.set_text(f"{action_label} {package_name}…")
        self.operation_progress.set_fraction(0.05)
        self.status_label.set_text(f"{action_label} {package_name}...")
        self._set_console_text(f"$ {command}\n\n[{action_label}] Starting {package_name}...\n")

        if self.progress_pulse_source is not None:
            GLib.source_remove(self.progress_pulse_source)
        self.progress_pulse_source = GLib.timeout_add(120, self._pulse_progress_bar)

        def run_command() -> None:
            output_chunks: list[str] = []
            try:
                process = subprocess.Popen(
                    ["bash", "-lc", command],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert process.stdout is not None
                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    output_chunks.append(line)
                    GLib.idle_add(self._append_console_text, line)
                process.stdout.close()
                return_code = process.wait()
                GLib.idle_add(
                    self._on_command_completed,
                    action_label,
                    package_name,
                    return_code,
                    "".join(output_chunks),
                    "",
                )
            except OSError as error:
                GLib.idle_add(
                    self._on_command_completed,
                    action_label,
                    package_name,
                    -1,
                    "",
                    str(error),
                )

        threading.Thread(target=run_command, daemon=True).start()

    def _pulse_progress_bar(self) -> bool:
        if not self.operation_in_progress:
            return False
        self.operation_progress.pulse()
        return True

    def _on_command_completed(
        self,
        action_label: str,
        package_name: str,
        return_code: int,
        stdout_text: str,
        stderr_text: str,
    ) -> bool:
        should_restart_app = bool(
            action_label == "Updating"
            and self.selected_package is not None
            and self.selected_package.get("isSelfPackage")
        )
        self.operation_in_progress = False
        if self.progress_pulse_source is not None:
            GLib.source_remove(self.progress_pulse_source)
            self.progress_pulse_source = None

        command_succeeded = return_code == 0
        if command_succeeded:
            GLib.idle_add(self._append_console_text, f"\n[{action_label}] Finished successfully.\n")
            self.operation_progress.set_fraction(1.0)
            self.operation_progress.set_text(f"{action_label} complete")
            self._refresh_installed_state()
            self.refresh_package_list()
            self._check_app_store_updates_async(force=True)
            final_status = f"{action_label} finished for {package_name}"
        else:
            GLib.idle_add(self._append_console_text, f"\n[{action_label}] Failed with exit code {return_code}.\n")
            self.operation_progress.set_fraction(0.0)
            self.operation_progress.set_text(f"{action_label} failed")
            failure_output = (stderr_text or stdout_text).strip()[-500:]
            if not failure_output:
                failure_output = "No command output was returned."
            self._show_info(
                f"{action_label} failed",
                f"{package_name} could not be processed.\n\n{failure_output}",
            )
            final_status = f"{action_label} failed for {package_name}"

        if self.selected_package:
            self.show_package(self.selected_package)
        self.status_label.set_text(final_status)
        if command_succeeded and should_restart_app:
            self.status_label.set_text("Update installed. Restarting app store...")
            GLib.timeout_add(450, self._restart_application)
        return False

    def _reload_catalog(self, _button: Gtk.Button) -> None:
        self._sync_catalog_async(force=True)

    def _show_info(self, title: str, detail: str) -> None:
        dialog = InfoDialog(self, title, detail)
        dialog.connect("response", lambda d, _r: d.close())
        dialog.present()

    def _show_confirm(self, title: str, detail: str, callback) -> None:
        dialog = ConfirmDialog(self, title, detail)

        def on_response(dlg: Gtk.MessageDialog, response: int) -> None:
            dlg.close()
            callback(response == Gtk.ResponseType.YES)

        dialog.connect("response", on_response)
        dialog.present()

    def _refresh_installed_state(self) -> None:
        self.installed_packages = detect_installed_packages()
        self.installed_versions = detect_installed_versions()
        self.upgradable_packages, self.latest_package_versions = detect_upgradable_packages()

        for package in self.packages:
            if package.get("isSelfPackage"):
                self._populate_app_store_package(package)
                continue

            package_name = package.get("packageName", "")
            install_check_path = expand_install_check_path(package.get("installCheckPath", ""))
            package["installed"] = package_name in self.installed_packages or bool(
                install_check_path and install_check_path.exists()
            )
            package["currentVersion"] = self.installed_versions.get(package_name, "")
            package["latestVersion"] = self.latest_package_versions.get(package_name, "")
            package["updateAvailable"] = package_name in self.upgradable_packages and package.get("installed", False)
            package["updateCommand"] = package.get("installCommand", "")

        self._check_app_store_updates_async()

    def _populate_app_store_package(self, package: dict) -> None:
        package["installed"] = True
        package["source"] = "GitHub"
        package["homepage"] = self.app_store_repo_url.removesuffix(".git")
        package["currentVersion"] = self.app_store_current_version
        package["latestVersion"] = self.app_store_latest_version
        package["updateAvailable"] = self.app_store_update_available
        package["installCommand"] = build_core_update_command(self.app_store_repo_url, self.app_store_branch)
        package["updateCommand"] = build_core_update_command(self.app_store_repo_url, self.app_store_branch)
        package["uninstallCommand"] = ""

        if not self.app_store_update_known:
            package["summary"] = "Checking GitHub for new app store updates"
        elif self.app_store_update_available:
            package["summary"] = "A newer app store version is available on GitHub"
        else:
            package["summary"] = "The app store is currently up to date"

    def _check_app_store_updates_async(self, force: bool = False) -> None:
        if self.app_store_update_check_in_progress:
            return
        if self.app_store_update_known and not force:
            return

        self.app_store_update_check_in_progress = True

        def worker() -> None:
            repo_url = get_app_store_repo_url()
            branch = get_app_store_branch()
            local_version = get_local_app_store_version()
            remote_version = get_remote_app_store_version(repo_url, branch)
            update_available = bool(local_version and remote_version and local_version != remote_version)
            GLib.idle_add(
                self._apply_app_store_update_result,
                repo_url,
                branch,
                local_version,
                remote_version,
                update_available,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_app_store_update_result(
        self,
        repo_url: str,
        branch: str,
        local_version: str,
        remote_version: str,
        update_available: bool,
    ) -> bool:
        self.app_store_repo_url = repo_url or APPSTORE_REPO_URL
        self.app_store_branch = branch or DEFAULT_REPO_BRANCH
        write_app_store_manifest({"repo_url": self.app_store_repo_url, "branch": self.app_store_branch})
        self.app_store_current_version = local_version
        self.app_store_latest_version = remote_version
        self.app_store_update_available = update_available
        self.app_store_update_known = bool(remote_version)
        self.app_store_update_check_in_progress = False

        for package in self.packages:
            if package.get("isSelfPackage"):
                self._populate_app_store_package(package)
                break

        self.refresh_package_list()
        return False

    def _sync_catalog_async(self, force: bool = False, silent: bool = False) -> None:
        if self.catalog_sync_in_progress:
            return

        self.catalog_sync_in_progress = True
        if not silent:
            self.status_label.set_text("Syncing catalog and icons...")

        def worker() -> None:
            repo_url = get_app_store_repo_url()
            branch = get_app_store_branch()
            success, message = sync_remote_catalog(repo_url, branch)
            GLib.idle_add(self._apply_catalog_sync_result, success, message, silent)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_catalog_sync_result(self, success: bool, message: str, silent: bool) -> bool:
        self.catalog_sync_in_progress = False
        self.packages = load_packages()
        self._refresh_installed_state()
        self._populate_categories()
        self.refresh_package_list()

        if success:
            if not silent:
                self.status_label.set_text(message)
        else:
            if not silent:
                self.status_label.set_text("Catalog sync failed")
            self._show_info("Catalog sync failed", message)
        return False

    def _restart_application(self) -> bool:
        app_path = BASE_DIR / "app.py"
        try:
            subprocess.Popen(
                [sys.executable, str(app_path)],
                cwd=str(BASE_DIR),
            )
        except OSError as error:
            self._show_info("Restart failed", f"The update finished, but the app could not restart.\n\n{error}")
            return False

        app = self.get_application()
        self.close()
        if app is not None:
            app.quit()
        return False

    @staticmethod
    def _set_box_content(container: Gtk.Box, child: Gtk.Widget | None) -> None:
        current = container.get_first_child()
        while current is not None:
            next_child = current.get_next_sibling()
            container.remove(current)
            current = next_child
        if child is not None:
            container.append(child)

    @staticmethod
    def _clear_listbox(listbox: Gtk.ListBox) -> None:
        row = listbox.get_first_child()
        while row is not None:
            next_row = row.get_next_sibling()
            listbox.remove(row)
            row = next_row


class TermuxStoreApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="com.termuxappstore.desktop")

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = TermuxStoreWindow(self)
        window.present()


def main() -> None:
    app = TermuxStoreApplication()
    app.run(None)


if __name__ == "__main__":
    main()
