from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
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


def get_local_app_store_version() -> str:
    git_dir = BASE_DIR / ".git"
    if git_dir.exists():
        commit = _run_text_command(["git", "rev-parse", "HEAD"], cwd=BASE_DIR)
        if commit:
            return commit

    return str(read_app_store_manifest().get("commit", "")).strip()


def get_remote_app_store_version(repo_url: str) -> str:
    if not repo_url:
        return ""
    output = _run_text_command(["git", "ls-remote", repo_url, "HEAD"])
    return output.split()[0].strip() if output else ""


def make_app_store_install_command(repo_url: str) -> str:
    safe_repo = shlex.quote(repo_url)
    return (
        "pkg install -y git && "
        "tmpdir=$(mktemp -d) && "
        "trap 'rm -rf \"$tmpdir\"' EXIT && "
        f"git clone --depth 1 {safe_repo} \"$tmpdir\" && "
        "cd \"$tmpdir\" && "
        "bash install.sh"
    )


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

    icon_name = (package.get("iconName") or "").strip()
    icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    if not icon_name or (icon_theme is not None and not icon_theme.has_icon(icon_name)):
        icon_name = "application-x-executable"

    image = Gtk.Image.new_from_icon_name(icon_name)
    image.set_pixel_size(size)
    image.add_css_class("package-icon")
    return image


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
        self.set_default_size(1280, 760)

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

        self._load_css()
        self._build_ui()
        self._setup_theme_monitor()
        self._sync_theme()
        self._refresh_installed_state()
        self._populate_categories()
        self.refresh_package_list()

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
        subtitle_label = Gtk.Label(label="Browse and install terminal software")
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

        details_hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
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

        self.name_label = Gtk.Label(label="Select a package")
        self.name_label.set_xalign(0)
        self.name_label.add_css_class("detail-title")
        details_hero.append(self.name_label)

        self.meta_label = Gtk.Label(label="")
        self.meta_label.set_xalign(0)
        self.meta_label.add_css_class("detail-meta")
        details_hero.append(self.meta_label)

        self.install_state_label = Gtk.Label(label="")
        self.install_state_label.set_xalign(0)
        self.install_state_label.add_css_class("detail-meta")
        details_hero.append(self.install_state_label)

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

        self.description_label = Gtk.Label(
            label="Choose something from the catalog to inspect it here."
        )
        self.description_label.set_xalign(0)
        self.description_label.set_wrap(True)
        self.description_label.set_max_width_chars(90)
        self.description_label.add_css_class("detail-description")
        about_section.append(self.description_label)

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

        install_title = Gtk.Label(label="Install command")
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
        self.tags_label.set_text(", ".join(package.get("tags", [])) or "No tags")
        displayed_command = package.get("updateCommand") if package.get("updateAvailable") else package.get("installCommand")
        self.command_view.get_buffer().set_text(displayed_command or "")
        self.install_button.set_sensitive(bool(package.get("installCommand")) and not package.get("installed"))
        self.update_button.set_sensitive(bool(package.get("updateAvailable") and package.get("updateCommand")))
        self.uninstall_button.set_sensitive(bool(package.get("installed") and package.get("uninstallCommand")))
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
        self.install_state_label.set_text("")
        self.install_state_label.remove_css_class("status-installed")
        self.install_state_label.remove_css_class("status-missing")
        self.source_label.set_text("")
        self.maintainer_label.set_text("")
        self.description_label.set_text("No packages matched your filters.")
        self.tags_label.set_text("")
        self.command_view.get_buffer().set_text("")
        self.install_button.set_sensitive(False)
        self.update_button.set_sensitive(False)
        self.uninstall_button.set_sensitive(False)
        self.status_label.set_text("No packages available")
        self._set_details_visible(False)
        self._show_browse_view()

    def _set_details_visible(self, visible: bool) -> None:
        self.hero_category_label.set_visible(visible)
        self.hero_state_label.set_visible(visible)
        self.name_label.set_visible(visible)
        self.meta_label.set_visible(visible)
        self.install_state_label.set_visible(visible)
        self.source_label.set_visible(visible)
        self.maintainer_label.set_visible(visible)
        self.description_label.set_visible(visible)
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
        self.subtitle_label.set_text("Browse and install terminal software")

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

        if self.progress_pulse_source is not None:
            GLib.source_remove(self.progress_pulse_source)
        self.progress_pulse_source = GLib.timeout_add(120, self._pulse_progress_bar)

        def run_command() -> None:
            try:
                completed = subprocess.run(
                    ["bash", "-lc", command],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                GLib.idle_add(
                    self._on_command_completed,
                    action_label,
                    package_name,
                    completed.returncode,
                    completed.stdout,
                    completed.stderr,
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
        self.operation_in_progress = False
        if self.progress_pulse_source is not None:
            GLib.source_remove(self.progress_pulse_source)
            self.progress_pulse_source = None

        command_succeeded = return_code == 0
        if command_succeeded:
            self.operation_progress.set_fraction(1.0)
            self.operation_progress.set_text(f"{action_label} complete")
            self._refresh_installed_state()
            self.refresh_package_list()
            self._check_app_store_updates_async(force=True)
            final_status = f"{action_label} finished for {package_name}"
        else:
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
        return False

    def _reload_catalog(self, _button: Gtk.Button) -> None:
        self.packages = load_packages()
        self._refresh_installed_state()
        self._check_app_store_updates_async(force=True)
        self._populate_categories()
        self.refresh_package_list()
        self.status_label.set_text("Catalog reloaded")

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
            package["installed"] = package_name in self.installed_packages
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
        package["installCommand"] = make_app_store_install_command(self.app_store_repo_url)
        package["updateCommand"] = make_app_store_install_command(self.app_store_repo_url)
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
            local_version = get_local_app_store_version()
            remote_version = get_remote_app_store_version(repo_url)
            update_available = bool(local_version and remote_version and local_version != remote_version)
            GLib.idle_add(
                self._apply_app_store_update_result,
                repo_url,
                local_version,
                remote_version,
                update_available,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _apply_app_store_update_result(
        self,
        repo_url: str,
        local_version: str,
        remote_version: str,
        update_available: bool,
    ) -> bool:
        self.app_store_repo_url = repo_url or APPSTORE_REPO_URL
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
