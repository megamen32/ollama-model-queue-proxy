from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


APP_NAME = "Ollama Model Queue Proxy"
TASK_NAME = "Ollama Model Queue Proxy"
LAUNCH_LABEL = "com.megamen32.ollama-model-queue-proxy"
DROPIN_MARKER = "# Managed by ollama-model-queue-proxy installer.py"
DEFAULT_RAW_BASE = "https://raw.githubusercontent.com/megamen32/ollama-model-queue-proxy/main"


class InstallerError(RuntimeError):
    """A user-actionable installation error."""


@dataclass(frozen=True)
class Target:
    platform: str
    home: Path
    username: str
    uid: int | None
    gid: int | None
    state_root: Path
    proxy_path: Path

    @property
    def state_path(self) -> Path:
        return self.state_root / "state.json"


def platform_name() -> str:
    override = os.environ.get("OLLAMA_QUEUE_PLATFORM")
    if override:
        return override.lower()
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    raise InstallerError(f"unsupported operating system: {sys.platform}")


def change_port_enabled(value: str | None = None) -> bool:
    raw_value = value if value is not None else os.environ.get("CHANGE_PORT", "TRUE")
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise InstallerError(f"CHANGE_PORT must be TRUE or FALSE, got: {raw_value}")


def port_settings(change_port: bool) -> dict[str, str]:
    if change_port:
        return {
            "proxy_port": "11434",
            "backend_port": "11435",
            "upstream_url": "http://127.0.0.1:11435",
        }
    return {
        "proxy_port": "11437",
        "backend_port": "11434",
        "upstream_url": "http://127.0.0.1:11434",
    }


def _target_identity() -> tuple[Path, str, int | None, int | None]:
    if os.name == "nt":
        home = Path(os.environ.get("USERPROFILE", Path.home()))
        return home, os.environ.get("USERNAME", "user"), None, None

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and hasattr(os, "geteuid") and os.geteuid() == 0:
        import pwd

        account = pwd.getpwnam(sudo_user)
        return Path(account.pw_dir), account.pw_name, account.pw_uid, account.pw_gid

    return Path.home(), os.environ.get("USER", "user"), os.getuid(), os.getgid()


def target_paths(platform: str | None = None) -> Target:
    selected_platform = platform or platform_name()
    home, username, uid, gid = _target_identity()

    if selected_platform == "linux":
        state_root = Path(
            os.environ.get("OLLAMA_QUEUE_STATE_DIR", "/var/lib/ollama-model-queue-proxy")
        )
        proxy_path = Path(
            os.environ.get(
                "OLLAMA_QUEUE_LIBEXEC_PROXY",
                "/usr/local/libexec/ollama_model_queue_proxy.py",
            )
        )
    elif selected_platform == "macos":
        state_root = Path(
            os.environ.get(
                "OLLAMA_QUEUE_STATE_DIR",
                home / "Library" / "Application Support" / "Ollama Model Queue",
            )
        )
        proxy_path = state_root / "ollama_model_queue_proxy.py"
    elif selected_platform == "windows":
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")
        )
        state_root = Path(
            os.environ.get("OLLAMA_QUEUE_STATE_DIR", local_app_data / "OllamaModelQueueProxy")
        )
        proxy_path = state_root / "ollama_model_queue_proxy.py"
    else:
        raise InstallerError(f"unsupported operating system: {selected_platform}")

    return Target(selected_platform, home, username, uid, gid, state_root, proxy_path)


def _run(
    command: Sequence[str], *, check: bool = True, capture_output: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(command),
            check=check,
            text=True,
            capture_output=capture_output,
        )
    except FileNotFoundError as error:
        raise InstallerError(f"required command is not installed: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        command_text = " ".join(shlex.quote(part) for part in command)
        raise InstallerError(f"command failed: {command_text}{suffix}") from error
    return result


def _systemctl() -> str:
    return os.environ.get("OLLAMA_QUEUE_SYSTEMCTL", "systemctl")


def _systemctl_run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run([_systemctl(), *arguments], check=check)


def _require_root(platform: str) -> None:
    if platform == "linux" and hasattr(os, "geteuid"):
        if os.geteuid() != 0 and os.environ.get("OLLAMA_QUEUE_TEST_MODE") != "1":
            raise InstallerError("Linux installation must be run with sudo")


def _write_executable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if os.name != "nt":
        path.chmod(0o755)


def _write_text(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None and os.name != "nt":
        path.chmod(mode)


def _chown_to_target(path: Path, target: Target) -> None:
    if target.uid is not None and target.gid is not None and hasattr(os, "geteuid"):
        if os.geteuid() == 0:
            os.chown(path, target.uid, target.gid)


def _read_state(target: Target) -> dict[str, Any]:
    if not target.state_path.is_file():
        return {}
    try:
        value = json.loads(target.state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallerError(f"cannot read installer state: {target.state_path}") from error
    if not isinstance(value, dict):
        raise InstallerError(f"invalid installer state: {target.state_path}")
    return value


def _write_state(target: Target, state: dict[str, Any]) -> None:
    target.state_root.mkdir(parents=True, exist_ok=True)
    _write_text(target.state_path, json.dumps(state, indent=2, ensure_ascii=False) + "\n", 0o600)
    _chown_to_target(target.state_path, target)


def _remove_empty_state_root(target: Target) -> None:
    try:
        target.state_path.unlink()
    except FileNotFoundError:
        pass
    try:
        target.state_root.rmdir()
    except OSError:
        pass


def _copy_proxy(source_path: Path, target: Target) -> None:
    _write_executable(target.proxy_path, source_path.read_bytes())
    _chown_to_target(target.proxy_path, target)


def _linux_paths() -> tuple[Path, Path]:
    systemd_dir = Path(os.environ.get("OLLAMA_QUEUE_SYSTEMD_DIR", "/etc/systemd/system"))
    dropin = systemd_dir / "ollama.service.d" / "90-ollama-model-queue-proxy.conf"
    unit = systemd_dir / "ollama-model-queue-proxy.service"
    return unit, dropin


def _linux_unit(target: Target, settings: dict[str, str]) -> str:
    python_path = shlex.quote(sys.executable)
    proxy_path = shlex.quote(str(target.proxy_path))
    return f"""[Unit]
Description=Model-affine queue proxy for Ollama
After=network-online.target ollama.service
Wants=network-online.target

[Service]
Type=simple
ExecStart={python_path} {proxy_path}
Restart=always
RestartSec=3
Environment=OLLAMA_QUEUE_LISTEN_HOST=127.0.0.1
Environment=OLLAMA_QUEUE_LISTEN_PORT={settings['proxy_port']}
Environment=OLLAMA_UPSTREAM_URL={settings['upstream_url']}
Environment=OLLAMA_MODEL_QUEUE_MAX=128
Environment=OLLAMA_QUEUE_BATCH_GRACE_S=0.25
Environment=OLLAMA_PROXY_FIRST_BYTE_TIMEOUT_S=180
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
"""


def _ensure_safe_dropin(dropin: Path) -> None:
    if dropin.exists() and DROPIN_MARKER not in dropin.read_text(encoding="utf-8"):
        raise InstallerError(f"refusing to overwrite unmanaged drop-in: {dropin}")


def _linux_install(source_path: Path, target: Target, settings: dict[str, str], change_port: bool) -> None:
    _require_root(target.platform)
    unit, dropin = _linux_paths()
    try:
        _systemctl_run("cat", "ollama.service")
    except InstallerError as error:
        raise InstallerError("ollama.service was not found; install Ollama first") from error

    _copy_proxy(source_path, target)
    _write_text(unit, _linux_unit(target, settings), 0o644)
    if change_port:
        _ensure_safe_dropin(dropin)
        _write_text(
            dropin,
            f"{DROPIN_MARKER}\n[Service]\nEnvironment=\"OLLAMA_HOST=127.0.0.1:11435\"\n",
            0o644,
        )
    elif dropin.is_file() and DROPIN_MARKER in dropin.read_text(encoding="utf-8"):
        dropin.unlink()

    state = {
        "platform": target.platform,
        "change_port": change_port,
        "proxy_path": str(target.proxy_path),
        "unit_path": str(unit),
        "dropin_path": str(dropin),
    }
    _write_state(target, state)
    _systemctl_run("daemon-reload")
    _systemctl_run("enable", "ollama.service")
    _systemctl_run("restart", "ollama.service")
    _systemctl_run("is-active", "--quiet", "ollama.service")
    _systemctl_run("enable", "--now", "ollama-model-queue-proxy.service")
    _systemctl_run("is-active", "--quiet", "ollama-model-queue-proxy.service")


def launch_agent_plist(target: Target, settings: dict[str, str]) -> dict[str, Any]:
    return {
        "Label": LAUNCH_LABEL,
        "ProgramArguments": [sys.executable, str(target.proxy_path)],
        "EnvironmentVariables": {
            "OLLAMA_QUEUE_LISTEN_HOST": "127.0.0.1",
            "OLLAMA_QUEUE_LISTEN_PORT": settings["proxy_port"],
            "OLLAMA_UPSTREAM_URL": settings["upstream_url"],
            "OLLAMA_MODEL_QUEUE_MAX": "128",
            "OLLAMA_QUEUE_BATCH_GRACE_S": "0.25",
            "OLLAMA_PROXY_FIRST_BYTE_TIMEOUT_S": "180",
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
    }


def _launchctl_prefix(target: Target) -> list[str]:
    if target.uid is not None and hasattr(os, "geteuid"):
        if os.geteuid() == 0 and target.uid != os.getuid():
            return ["launchctl", "asuser", str(target.uid), "launchctl"]
    return ["launchctl"]


def _launchctl(target: Target, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run([*_launchctl_prefix(target), *arguments], check=check)


def _launchctl_env(target: Target) -> tuple[bool, str]:
    result = _launchctl(target, "getenv", "OLLAMA_HOST", check=False)
    value = result.stdout.strip()
    return result.returncode == 0 and bool(value), value


def _set_launchctl_env(target: Target, value: str) -> None:
    _launchctl(target, "setenv", "OLLAMA_HOST", value)


def _restore_launchctl_env(target: Target, state: dict[str, Any]) -> None:
    if "original_ollama_host_present" not in state:
        return
    if state.get("original_ollama_host_present"):
        _set_launchctl_env(target, str(state["original_ollama_host"]))
    else:
        _launchctl(target, "unsetenv", "OLLAMA_HOST", check=False)


def _mac_user_command(target: Target, command: Sequence[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    if target.uid is not None and hasattr(os, "geteuid"):
        if os.geteuid() == 0 and target.uid != os.getuid():
            return _run(["sudo", "-u", target.username, *command], check=check)
    return _run(command, check=check)


def _restart_ollama_macos(target: Target) -> None:
    if _mac_user_command(target, ["open", "-Ra", "Ollama"], check=False).returncode != 0:
        print("warning: Ollama.app was not found; restart Ollama manually to apply OLLAMA_HOST")
        return
    _mac_user_command(
        target,
        ["osascript", "-e", 'tell application "Ollama" to quit'],
        check=False,
    )
    time.sleep(1)
    _mac_user_command(target, ["open", "-a", "Ollama"], check=False)


def _mac_install(source_path: Path, target: Target, settings: dict[str, str], change_port: bool) -> None:
    _copy_proxy(source_path, target)
    launch_agents = target.home / "Library" / "LaunchAgents"
    plist_path = launch_agents / f"{LAUNCH_LABEL}.plist"
    launch_agents.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(launch_agent_plist(target, settings)))
    _chown_to_target(plist_path, target)

    state = _read_state(target)
    if change_port and "original_ollama_host_present" not in state:
        present, value = _launchctl_env(target)
        state["original_ollama_host_present"] = present
        state["original_ollama_host"] = value
        _set_launchctl_env(target, "127.0.0.1:11435")
    _launchctl(target, "bootout", f"gui/{target.uid}/{LAUNCH_LABEL}", check=False)
    _launchctl(target, "bootstrap", f"gui/{target.uid}", str(plist_path))
    state.update(
        {
            "platform": target.platform,
            "change_port": change_port,
            "proxy_path": str(target.proxy_path),
            "plist_path": str(plist_path),
        }
    )
    _write_state(target, state)
    if change_port:
        _restart_ollama_macos(target)


def windows_launcher_text(target: Target, settings: dict[str, str]) -> str:
    python_path = str(Path(sys.executable))
    proxy_path = str(target.proxy_path)
    return (
        "@echo off\n"
        "set \"OLLAMA_QUEUE_LISTEN_HOST=127.0.0.1\"\n"
        f"set \"OLLAMA_QUEUE_LISTEN_PORT={settings['proxy_port']}\"\n"
        f"set \"OLLAMA_UPSTREAM_URL={settings['upstream_url']}\"\n"
        f"\"{python_path}\" \"{proxy_path}\"\n"
    )


def _windows_registry_value() -> tuple[bool, str]:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "OLLAMA_HOST")
            return True, str(value)
    except FileNotFoundError:
        return False, ""


def _windows_set_registry_value(value: str) -> None:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, "OLLAMA_HOST", 0, winreg.REG_SZ, value)


def _windows_restore_registry(state: dict[str, Any]) -> None:
    import winreg

    if "original_ollama_host_present" not in state:
        return

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        if state.get("original_ollama_host_present"):
            winreg.SetValueEx(
                key,
                "OLLAMA_HOST",
                0,
                winreg.REG_SZ,
                str(state.get("original_ollama_host", "")),
            )
        else:
            try:
                winreg.DeleteValue(key, "OLLAMA_HOST")
            except FileNotFoundError:
                pass


def _windows_task(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["schtasks.exe", *arguments], check=check)


def _restart_ollama_windows() -> None:
    _run(["taskkill.exe", "/IM", "Ollama.exe", "/F"], check=False)
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "Ollama.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "Ollama.exe",
    ]
    executable = next((candidate for candidate in candidates if candidate.is_file()), None)
    if executable is None:
        print("warning: Ollama.exe was not found; restart Ollama manually to apply OLLAMA_HOST")
        return
    subprocess.Popen([str(executable)], close_fds=True)


def _windows_install(source_path: Path, target: Target, settings: dict[str, str], change_port: bool) -> None:
    _copy_proxy(source_path, target)
    launcher = target.state_root / "run-proxy.cmd"
    _write_text(launcher, windows_launcher_text(target, settings))
    state = _read_state(target)
    if change_port and "original_ollama_host_present" not in state:
        present, value = _windows_registry_value()
        state["original_ollama_host_present"] = present
        state["original_ollama_host"] = value
        _windows_set_registry_value("127.0.0.1:11435")
    _windows_task("/Delete", "/TN", TASK_NAME, "/F", check=False)
    _windows_task(
        "/Create",
        "/TN",
        TASK_NAME,
        "/SC",
        "ONLOGON",
        "/TR",
        f'"{launcher}"',
        "/F",
    )
    _windows_task("/Run", "/TN", TASK_NAME)
    state.update(
        {
            "platform": target.platform,
            "change_port": change_port,
            "proxy_path": str(target.proxy_path),
            "launcher_path": str(launcher),
        }
    )
    _write_state(target, state)
    if change_port:
        _restart_ollama_windows()


def install(source_path: Path, change_port: bool) -> None:
    target = target_paths()
    settings = port_settings(change_port)
    if not source_path.is_file():
        raise InstallerError(f"proxy source was not downloaded: {source_path}")

    if target.platform == "linux":
        _linux_install(source_path, target, settings, change_port)
    elif target.platform == "macos":
        _mac_install(source_path, target, settings, change_port)
    elif target.platform == "windows":
        _windows_install(source_path, target, settings, change_port)
    else:
        raise InstallerError(f"unsupported operating system: {target.platform}")

    print(f"Installed {APP_NAME} on {target.platform}.")
    print(f"Proxy port: {settings['proxy_port']}; Ollama backend: {settings['backend_port']}.")


def _linux_uninstall(target: Target, state: dict[str, Any]) -> None:
    unit = Path(state.get("unit_path", _linux_paths()[0]))
    dropin = Path(state.get("dropin_path", _linux_paths()[1]))
    _systemctl_run("disable", "--now", "ollama-model-queue-proxy.service", check=False)
    if dropin.is_file() and DROPIN_MARKER not in dropin.read_text(encoding="utf-8"):
        raise InstallerError(f"refusing to remove unmanaged drop-in: {dropin}")
    for path in (unit, target.proxy_path, dropin):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    try:
        dropin.parent.rmdir()
    except OSError:
        pass
    _systemctl_run("daemon-reload")
    if _systemctl_run("cat", "ollama.service", check=False).returncode == 0:
        _systemctl_run("enable", "ollama.service")
        _systemctl_run("restart", "ollama.service")


def _mac_uninstall(target: Target, state: dict[str, Any]) -> None:
    plist_path = Path(state.get("plist_path", target.home / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"))
    _launchctl(target, "bootout", f"gui/{target.uid}/{LAUNCH_LABEL}", check=False)
    _restore_launchctl_env(target, state)
    for path in (plist_path, target.proxy_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _restart_ollama_macos(target)


def _windows_uninstall(target: Target, state: dict[str, Any]) -> None:
    _windows_task("/End", "/TN", TASK_NAME, check=False)
    _windows_task("/Delete", "/TN", TASK_NAME, "/F", check=False)
    _windows_restore_registry(state)
    for path in (Path(state.get("launcher_path", target.state_root / "run-proxy.cmd")), target.proxy_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _restart_ollama_windows()


def uninstall() -> None:
    target = target_paths()
    state = _read_state(target)
    if state and state.get("platform") not in {None, target.platform}:
        raise InstallerError(
            f"installer state belongs to {state.get('platform')}, not {target.platform}"
        )

    if target.platform == "linux":
        _require_root(target.platform)
        _linux_uninstall(target, state)
    elif target.platform == "macos":
        _mac_uninstall(target, state)
    elif target.platform == "windows":
        _windows_uninstall(target, state)
    else:
        raise InstallerError(f"unsupported operating system: {target.platform}")

    _remove_empty_state_root(target)
    print(f"Removed {APP_NAME} from {target.platform}.")
