# Termux App Store

A lightweight desktop GUI MVP for `termux-x11`. This project now starts as a Python GTK4 app that can:

- list curated Termux packages
- search packages by name, tags, and description
- show install commands for desktop users inside Termux
- launch package install commands from the GUI

## Why this shape?

Inside `termux-x11`, the app should feel like a normal desktop application rather than a website. Termux packages are still installed with `pkg` or `apt`, so the app store should focus on:

- discovery
- trust and source transparency
- copy-paste install commands
- package metadata and categories
- future support for repo sync and richer desktop workflows

## MVP scope

This starter includes:

- a Python desktop app built with GTK4
- a curated JSON catalog in `src/data/apps.json`
- search and category filtering
- package details and install command actions

## Project structure

```text
src/
  data/
    packages.json
app.py
```

## Run locally

1. Make sure your Termux session has GUI support and GTK4 Python bindings available.

```bash
pkg install x11-repo python gtk4 pygobject
```

2. Start your desktop/X11 session, then run:

```bash
python app.py
```

The app opens as a desktop window inside `termux-x11`.

## Editing the catalog

Add or edit apps in `src/data/apps.json`.

Each app can include fields like:

- `id`
- `packageName`
- `name`
- `iconPath`
- `iconName`
- `category`
- `summary`
- `description`
- `tags`
- `installCommand`
- `uninstallCommand`
- `source`
- `homepage`
- `maintainer`

`packageName` is what the app uses to detect whether a package is already installed in Termux.

If `iconPath` is set, the app will try to load that image or SVG first. If it is missing, `iconName` is used as the fallback system icon.

Recommended place for custom icons:

- `assets/icons/your-app.svg`

## Product ideas for next steps

- sync from real Termux repositories
- add screenshots, maintainers, and update metadata
- support install collections like "web dev", "pentest", or "python"
- track installed packages and available upgrades
- add trust indicators for third-party sources
- improve the UI with icons, tabs, and background install progress

## Important note

This MVP is still a simple frontend for the Termux package manager. The safest path is to start with trusted metadata and install commands, then add automation carefully.
