from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import urllib.request
from pathlib import Path


DEFAULT_RAW_BASE = "https://raw.githubusercontent.com/megamen32/ollama-model-queue-proxy/main"


def _load_common() -> tuple[object, tempfile.TemporaryDirectory[str] | None]:
    script_path = Path(globals().get("__file__", "<stdin>"))
    local_common = script_path.parent / "installer_common.py"
    if local_common.is_file():
        common_path = local_common
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory()
        common_path = Path(temporary.name) / "installer_common.py"
        raw_base = os.environ.get("OLLAMA_QUEUE_RAW_BASE", DEFAULT_RAW_BASE)
        common_path.write_bytes(
            urllib.request.urlopen(f"{raw_base}/scripts/installer_common.py", timeout=30).read()
        )

    spec = importlib.util.spec_from_file_location("ollama_queue_installer_common", common_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load installer library: {common_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, temporary


def main() -> int:
    module, temporary = _load_common()
    try:
        module.uninstall()
    except module.InstallerError as error:
        print(f"error: {error}")
        return 1
    finally:
        if temporary is not None:
            temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
