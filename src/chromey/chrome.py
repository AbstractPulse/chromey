from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_CHROME_PATH_CANDIDATES = (
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/opt/google/chrome/google-chrome",
    "/usr/bin/google-chrome-beta",
    "/opt/google/chrome-beta/google-chrome-beta",
    "/usr/bin/google-chrome-unstable",
    "/opt/google/chrome-unstable/google-chrome-unstable",
    "/var/lib/flatpak/exports/bin/com.google.Chrome",
    "/var/lib/flatpak/exports/bin/com.google.ChromeBeta",
    "/var/lib/flatpak/exports/bin/com.google.ChromeDev",
    str(Path.home() / ".local" / "share" / "flatpak" / "exports" / "bin" / "com.google.Chrome"),
    str(Path.home() / ".local" / "share" / "flatpak" / "exports" / "bin" / "com.google.ChromeBeta"),
    str(Path.home() / ".local" / "share" / "flatpak" / "exports" / "bin" / "com.google.ChromeDev"),
)
DEFAULT_CHROME_USER_DATA_DIR_CANDIDATES = (
    Path.home() / ".config" / "google-chrome",
    Path.home() / ".config" / "google-chrome-beta",
    Path.home() / ".config" / "google-chrome-unstable",
    Path.home() / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome",
)
DEFAULT_PROXY_CHROME_USER_DATA_DIR = Path.home() / ".config" / "chromey" / "chrome-browser"
DEFAULT_PROXY_EXTENSION_STAGING_DIR = Path.home() / ".config" / "chromey" / "extension"
CHROMEY_EXTENSION_ID = "kjannalhcgkgfbchjccgpfdhfipihgib"


@dataclass(frozen=True)
class ChromeLaunchOptions:
    chrome_path: str | None = None
    cdp_url: str | None = None
    user_data_dir: str | None = None
    profile_directory: str = "Default"
    show_profile_picker: bool = False
    extension_path: Path | None = None


def flatpak_app_id(chrome_path: str | None) -> str | None:
    if not chrome_path:
        return None
    path = Path(chrome_path)
    name = path.name
    if name.startswith("com.google.Chrome"):
        return name
    return None


def flatpak_config_root(app_id: str) -> Path:
    return Path.home() / ".var" / "app" / app_id / "config"


def flatpak_data_root(app_id: str) -> Path:
    return Path.home() / ".var" / "app" / app_id / "data"


def detect_chrome_path(explicit_path: str | None = None) -> str | None:
    if explicit_path:
        return explicit_path
    env_value = (os.environ.get("BROWSER_PATH") or "").strip()
    env_path = Path(env_value).expanduser() if env_value else None
    if env_path is not None and env_path.exists():
        return str(env_path)

    for executable_name in ("google-chrome-stable", "google-chrome", "google-chrome-beta", "google-chrome-unstable"):
        resolved = shutil.which(executable_name)
        if resolved:
            return resolved

    for candidate in DEFAULT_CHROME_PATH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def discover_user_data_dir(explicit_path: str | None = None) -> Path | None:
    if explicit_path:
        return Path(explicit_path).expanduser()
    for candidate in DEFAULT_CHROME_USER_DATA_DIR_CANDIDATES:
        if candidate.exists():
            return candidate
    return DEFAULT_CHROME_USER_DATA_DIR_CANDIDATES[0]


def default_launch_user_data_dir(chrome_path: str | None = None) -> Path:
    app_id = flatpak_app_id(chrome_path)
    if app_id:
        return flatpak_config_root(app_id) / "chromey" / "chrome-browser"
    return DEFAULT_PROXY_CHROME_USER_DATA_DIR


def resolve_launch_user_data_dir(explicit_path: str | None = None, *, chrome_path: str | None = None) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser()
    return default_launch_user_data_dir(chrome_path)


def default_launch_extension_dir(chrome_path: str | None = None) -> Path:
    app_id = flatpak_app_id(chrome_path)
    if app_id:
        return flatpak_data_root(app_id) / "chromey" / "extension"
    return DEFAULT_PROXY_EXTENSION_STAGING_DIR


def _sync_directory(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    source_entries = {child.name: child for child in source.iterdir()}

    for existing_child in list(target.iterdir()):
        source_child = source_entries.get(existing_child.name)
        if source_child is None:
            if existing_child.is_dir():
                shutil.rmtree(existing_child)
            else:
                existing_child.unlink()
            continue

        if source_child.is_dir() and existing_child.is_file():
            existing_child.unlink()
        elif source_child.is_file() and existing_child.is_dir():
            shutil.rmtree(existing_child)

    for name, source_child in source_entries.items():
        target_child = target / name
        if source_child.is_dir():
            _sync_directory(source_child, target_child)
        else:
            target_child.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_child, target_child)


def stage_extension_for_launch(extension_path: Path, *, chrome_path: str | None = None) -> Path:
    source = extension_path.expanduser().resolve()
    app_id = flatpak_app_id(chrome_path)
    if app_id:
        target = default_launch_extension_dir(chrome_path)
        _sync_directory(source, target)
        return target
    return source


def list_profiles(user_data_dir: Path | None) -> list[str]:
    if user_data_dir is None or not user_data_dir.exists():
        return []
    profiles: list[str] = []
    for child in sorted(user_data_dir.iterdir()):
        if child.is_dir() and (child / "Preferences").exists():
            profiles.append(child.name)
    return profiles


def friendly_profile_labels(user_data_dir: Path | None) -> list[str]:
    if user_data_dir is None:
        return []
    local_state_path = user_data_dir / "Local State"
    if not local_state_path.exists():
        return []

    try:
        payload = json.loads(local_state_path.read_text())
    except Exception:
        return []

    profile_section = payload.get("profile", {})
    if not isinstance(profile_section, dict):
        return []
    info_cache = profile_section.get("info_cache", {})
    if not isinstance(info_cache, dict):
        return []

    labels: list[str] = []
    for profile_dir, info in info_cache.items():
        if isinstance(info, dict) and isinstance(info.get("name"), str) and info["name"].strip():
            labels.append(f"{info['name'].strip()} [{profile_dir}]")
        else:
            labels.append(profile_dir)
    return labels


def set_profile_picker(user_data_dir: Path, enabled: bool) -> None:
    local_state_path = user_data_dir / "Local State"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    if local_state_path.exists():
        payload = json.loads(local_state_path.read_text())
    else:
        payload = {}

    profile_section = payload.setdefault("profile", {})
    if not isinstance(profile_section, dict):
        raise RuntimeError("Unexpected Chrome Local State format.")
    profile_section["show_picker_on_startup"] = enabled
    local_state_path.write_text(json.dumps(payload, separators=(",", ":")))


def rewrite_installed_extension_path(
    user_data_dir: Path,
    *,
    extension_id: str,
    new_path: Path,
    profile_directory: str = "Default",
) -> tuple[bool, str | None]:
    preferences_path = user_data_dir / profile_directory / "Preferences"
    if not preferences_path.exists():
        return False, None

    try:
        payload = json.loads(preferences_path.read_text())
    except Exception:
        return False, None

    settings = ((payload.get("extensions") or {}).get("settings")) or {}
    if not isinstance(settings, dict):
        return False, None

    extension_payload = settings.get(extension_id)
    if not isinstance(extension_payload, dict):
        return False, None

    current_path = extension_payload.get("path")
    if not isinstance(current_path, str) or not current_path.strip():
        return False, None

    normalized_new_path = str(new_path)
    if current_path == normalized_new_path:
        return False, current_path

    extension_payload["path"] = normalized_new_path
    preferences_path.write_text(json.dumps(payload, separators=(",", ":")))
    return True, current_path


def _cdp_port_from_url(cdp_url: str) -> int:
    parsed = urllib.parse.urlparse(cdp_url if "://" in cdp_url else f"http://{cdp_url}")
    if parsed.port is None:
        raise RuntimeError(f"Could not determine the CDP port from {cdp_url!r}.")
    return parsed.port


def probe_cdp_url(cdp_url: str | None) -> str | None:
    if not cdp_url:
        return None

    normalized = cdp_url.rstrip("/")
    if normalized.startswith(("ws://", "wss://")):
        return normalized

    probe_url = normalized
    if not probe_url.endswith("/json/version"):
        probe_url = f"{probe_url}/json/version"

    request = urllib.request.Request(probe_url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    return normalized if isinstance(payload.get("webSocketDebuggerUrl"), str) else None


def wait_for_cdp(cdp_url: str, *, timeout_seconds: float = 12.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if probe_cdp_url(cdp_url):
            return True
        time.sleep(0.5)
    return False


def launch_chrome(options: ChromeLaunchOptions) -> subprocess.Popen[bytes]:
    chrome_path = detect_chrome_path(options.chrome_path)
    if not chrome_path:
        raise RuntimeError("No local Chrome executable was found. Install Chrome or pass --chrome-path.")

    app_id = flatpak_app_id(chrome_path)
    effective_cdp_url = options.cdp_url or DEFAULT_CDP_URL
    cdp_port = _cdp_port_from_url(effective_cdp_url)
    user_data_dir = resolve_launch_user_data_dir(options.user_data_dir, chrome_path=chrome_path)
    using_isolated_profile = not bool(options.user_data_dir)
    profile_directory = "Default" if using_isolated_profile else (options.profile_directory or "Default")
    user_data_dir.mkdir(parents=True, exist_ok=True)
    launch_extension_path = None
    rewritten_extension_path: tuple[bool, str | None] = (False, None)
    if options.extension_path and options.extension_path.exists():
        launch_extension_path = stage_extension_for_launch(options.extension_path, chrome_path=chrome_path)
        if app_id is not None:
            rewritten_extension_path = rewrite_installed_extension_path(
                user_data_dir,
                extension_id=CHROMEY_EXTENSION_ID,
                new_path=launch_extension_path,
                profile_directory=profile_directory,
            )

    command = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
    ]
    if launch_extension_path is not None:
        if app_id is None:
            command.append(f"--disable-extensions-except={launch_extension_path}")
            command.append(f"--load-extension={launch_extension_path}")
        else:
            # Flatpak Chrome can access the staged extension path, but unpacked extension
            # persistence is more reliable when the user loads that stable path once.
            pass

    if options.show_profile_picker and not using_isolated_profile:
        set_profile_picker(user_data_dir, True)
        labels = friendly_profile_labels(user_data_dir)
        if labels:
            print("Launching Chrome with the native profile picker. Available profiles: " + ", ".join(labels))
    else:
        set_profile_picker(user_data_dir, False)
        if options.profile_directory and not using_isolated_profile:
            command.append(f"--profile-directory={options.profile_directory}")
        command.extend(["--new-window", "chrome://newtab/"])

    launch_mode = "isolated Chromey profile" if using_isolated_profile else "selected Chrome profile"
    print(f"Launching {launch_mode} from {user_data_dir}")
    if launch_extension_path is not None:
        if app_id is None:
            print(f"Loading Chromey extension from {launch_extension_path}")
        else:
            print(
                "Staged Chromey extension for Flatpak Chrome at "
                f"{launch_extension_path}. Load it once via Load unpacked from that exact path in the Chromey profile."
            )
            if rewritten_extension_path[0]:
                print(
                    "Updated the saved Chromey extension path from "
                    f"{rewritten_extension_path[1]} to {launch_extension_path}."
                )
    print("Launch command: " + " ".join(shlex.quote(part) for part in command))

    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
