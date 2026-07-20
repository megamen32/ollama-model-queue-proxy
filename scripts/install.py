from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import urllib.request
from pathlib import Path


DEFAULT_RAW_BASE = "https://raw.githubusercontent.com/megamen32/ollama-model-queue-proxy/main"


def _load_common() -> tuple[object, tempfile.TemporaryDirectory[str] | None, Path]:
    script_path = Path(globals().get("__file__", "<stdin>"))
    local_common = script_path.parent / "installer_common.py"
    if local_common.is_file():
        workspace = script_path.parent.parent
        common_path = local_common
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory()
        workspace = Path(temporary.name)
        common_path = workspace / "installer_common.py"
        raw_base = os.environ.get("OLLAMA_QUEUE_RAW_BASE", DEFAULT_RAW_BASE)
        common_path.write_bytes(
            urllib.request.urlopen(f"{raw_base}/scripts/installer_common.py", timeout=30).read()
        )
        source_path = workspace / "ollama_model_queue_proxy.py"
        source_path.write_bytes(
            urllib.request.urlopen(f"{raw_base}/ollama_model_queue_proxy.py", timeout=30).read()
        )

    spec = importlib.util.spec_from_file_location("ollama_queue_installer_common", common_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load installer library: {common_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, temporary, workspace


def _parse_change_port(common: object) -> bool:
    parser = argparse.ArgumentParser(description="Install the Ollama model queue proxy")
    parser.add_argument("--change-port", dest="change_port", action="store_true")
    parser.add_argument("--no-change-port", dest="change_port", action="store_false")
    parser.set_defaults(change_port=None)
    arguments = parser.parse_args()
    explicit = None if arguments.change_port is None else str(arguments.change_port)
    return common.change_port_enabled(explicit)


def main() -> int:
    module, temporary, workspace = _load_common()
    try:
        module.install(workspace / "ollama_model_queue_proxy.py", _parse_change_port(module))
    except module.InstallerError as error:
        print(f"error: {error}")
        return 1
    finally:
        if temporary is not None:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
