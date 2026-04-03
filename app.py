from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

os.environ.setdefault("GSK_RENDERER", "cairo")

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk


BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "src" / "data" / "apps.json"
ASSETS_DIR = BASE_DIR / "assets" / "icons"


def load_packages() -> list[dict]:
    with DATA_FILE.open("r", encoding="utf-8") as file:
        apps = json.load(file)

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
        app["installed"] = False

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


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def launch_command_in_terminal(command: str) -> tuple[bool, str]:
    wrapped_command = f"{command}; printf '\\n'; printf 'Press Enter to close...'; read _"
    terminal_commands = [
        ["x-terminal-emulator", "-e", "bash", "-lc", wrapped_command],
        ["xfce4-terminal", "--hold", "-e", f"bash -lc {shell_quote(wrapped_command)}"],
        ["gnome-terminal", "--", "bash", "-lc", wrapped_command],
        ["konsole", "-e", "bash", "-lc", wrapped_command],
        ["xterm", "-hold", "-e", "bash", "-lc", wrapped_command],
        ["bash", "-lc", wrapped_command],
    ]

    attempted = []
    for candidate in terminal_commands:
        attempted.append(candidate[0])
        try:
            subprocess.Popen(candidate)
            return True, candidate[0]
        except OSError:
            continue

    return False, ", ".join(attempted)


def build_icon_widget(package: dict, size: int = 38) -> Gtk.Widget:
    icon_path = package.get("iconPath", "").strip()
    if icon_path:
        candidate = Path(icon_path)
        if not candidate.is_absolute():
            candidate = BASE_DIR / candidate
        if candidate.exists():
            image = Gtk.Image.new_from_file(str(candidate))
            image.set_pixel_size(size)
            image.add_css_class("package-icon")
            return image

    icon_name = package.get("iconName", "application-x-executable")
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

        self._load_css()
        self._build_ui()
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
        }

        .content-panel {
            background: #f6f7f8;
        }

        .detail-panel {
            background: #ffffff;
            border-left: 1px solid #d8dee4;
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
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(root)

        header = Gtk.HeaderBar()
        header.set_show_title_buttons(True)
        self.set_titlebar(header)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_label = Gtk.Label(label="Termux App Store")
        title_label.add_css_class("section-title")
        title_label.set_xalign(0)
        subtitle_label = Gtk.Label(label="Browse and install terminal software")
        subtitle_label.add_css_class("section-subtitle")
        subtitle_label.set_xalign(0)
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

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        root.append(main)

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

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        details.set_size_request(360, -1)
        details.set_margin_top(18)
        details.set_margin_bottom(18)
        details.set_margin_end(18)
        details.set_margin_start(0)
        details.set_hexpand(False)
        details.add_css_class("detail-panel")
        main.append(details)

        detail_heading = Gtk.Label(label="Details")
        detail_heading.set_xalign(0)
        detail_heading.add_css_class("section-title")
        details.append(detail_heading)

        self.name_label = Gtk.Label(label="Select a package")
        self.name_label.set_xalign(0)
        self.name_label.add_css_class("detail-title")
        details.append(self.name_label)

        self.meta_label = Gtk.Label(label="")
        self.meta_label.set_xalign(0)
        self.meta_label.add_css_class("detail-meta")
        details.append(self.meta_label)

        self.install_state_label = Gtk.Label(label="")
        self.install_state_label.set_xalign(0)
        self.install_state_label.add_css_class("detail-meta")
        details.append(self.install_state_label)

        self.source_label = Gtk.Label(label="")
        self.source_label.set_xalign(0)
        self.source_label.add_css_class("detail-meta")
        details.append(self.source_label)

        self.maintainer_label = Gtk.Label(label="")
        self.maintainer_label.set_xalign(0)
        self.maintainer_label.add_css_class("detail-meta")
        details.append(self.maintainer_label)

        self.description_label = Gtk.Label(
            label="Choose something from the catalog to inspect it here."
        )
        self.description_label.set_xalign(0)
        self.description_label.set_wrap(True)
        self.description_label.set_max_width_chars(34)
        self.description_label.add_css_class("detail-description")
        details.append(self.description_label)

        tags_title = Gtk.Label(label="Tags")
        tags_title.set_xalign(0)
        tags_title.add_css_class("install-label")
        details.append(tags_title)

        self.tags_label = Gtk.Label(label="")
        self.tags_label.set_xalign(0)
        self.tags_label.set_wrap(True)
        self.tags_label.add_css_class("detail-meta")
        details.append(self.tags_label)

        install_title = Gtk.Label(label="Install command")
        install_title.set_xalign(0)
        install_title.add_css_class("install-label")
        details.append(install_title)

        command_scroller = Gtk.ScrolledWindow()
        command_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        command_scroller.set_min_content_height(96)
        details.append(command_scroller)

        self.command_view = Gtk.TextView()
        self.command_view.set_editable(False)
        self.command_view.set_cursor_visible(False)
        self.command_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        command_scroller.set_child(self.command_view)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        details.append(button_row)

        self.install_button = Gtk.Button(label="Install")
        self.install_button.connect("clicked", self._run_install_command)
        button_row.append(self.install_button)

        self.uninstall_button = Gtk.Button(label="Uninstall")
        self.uninstall_button.connect("clicked", self._run_uninstall_command)
        button_row.append(self.uninstall_button)

        copy_button = Gtk.Button(label="Copy")
        copy_button.connect("clicked", self._copy_command)
        button_row.append(copy_button)

        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_xalign(0)
        self.status_label.add_css_class("status-label")
        details.append(self.status_label)

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
            first_row = self.package_list.get_row_at_index(0)
            if first_row is not None:
                self.package_list.select_row(first_row)
                self.show_package(first_row.package_data)
        else:
            self.clear_details()

    def _on_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        self.show_package(row.package_data)

    def show_package(self, package: dict) -> None:
        self.selected_package = package
        self.name_label.set_text(package["name"])
        self.meta_label.set_text(f"Category: {package['category']}")
        self.install_state_label.set_text(
            "Installed in Termux" if package.get("installed") else "Not currently installed"
        )
        self.install_state_label.remove_css_class("status-installed")
        self.install_state_label.remove_css_class("status-missing")
        self.install_state_label.add_css_class(
            "status-installed" if package.get("installed") else "status-missing"
        )
        self.source_label.set_text(f"Source: {package['source']}")
        self.maintainer_label.set_text(
            f"Maintainer: {package['maintainer']}" if package.get("maintainer") else ""
        )
        self.description_label.set_text(package["description"])
        self.tags_label.set_text(", ".join(package.get("tags", [])))
        self.command_view.get_buffer().set_text(package["installCommand"])
        self.install_button.set_sensitive(not package.get("installed"))
        self.uninstall_button.set_sensitive(bool(package.get("installed") and package.get("uninstallCommand")))
        self.status_label.set_text(f"Selected {package['name']}")

    def clear_details(self) -> None:
        self.selected_package = None
        self.name_label.set_text("No package selected")
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
        self.uninstall_button.set_sensitive(False)
        self.status_label.set_text("No packages available")

    def _copy_command(self, _button: Gtk.Button) -> None:
        if not self.selected_package:
            return

        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(self.selected_package["installCommand"])
        self.status_label.set_text("Install command copied")

    def _run_install_command(self, _button: Gtk.Button) -> None:
        if not self.selected_package:
            self._show_info("No package selected", "Choose a package first.")
            return

        command = self.selected_package["installCommand"]
        package_name = self.selected_package["name"]
        self._show_confirm(
            f"Install {package_name}?",
            command,
            lambda confirmed: self._start_install(command, package_name) if confirmed else None,
        )

    def _run_uninstall_command(self, _button: Gtk.Button) -> None:
        if not self.selected_package:
            self._show_info("No package selected", "Choose a package first.")
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
        success, launcher = launch_command_in_terminal(command)
        if success:
            self.status_label.set_text(f"Opened install for {package_name} in {launcher}")
            return

        self._show_info(
            "Install failed",
            f"Could not launch a terminal for the install command.\nTried: {launcher}",
        )
        self.status_label.set_text(f"Failed to start install for {package_name}")

    def _start_uninstall(self, command: str, package_name: str) -> None:
        success, launcher = launch_command_in_terminal(command)
        if success:
            self.status_label.set_text(f"Opened uninstall for {package_name} in {launcher}")
            return

        self._show_info(
            "Uninstall failed",
            f"Could not launch a terminal for the uninstall command.\nTried: {launcher}",
        )
        self.status_label.set_text(f"Failed to start uninstall for {package_name}")

    def _reload_catalog(self, _button: Gtk.Button) -> None:
        self.packages = load_packages()
        self._refresh_installed_state()
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
        for package in self.packages:
            package["installed"] = package.get("packageName", "") in self.installed_packages

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
