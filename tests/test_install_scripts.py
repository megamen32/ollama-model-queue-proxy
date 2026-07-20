import os
import importlib.util
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_DIR / "scripts" / "install.py"
UNINSTALL_SCRIPT = REPO_DIR / "scripts" / "uninstall.py"


COMMON_SPEC = importlib.util.spec_from_file_location(
    "installer_common_for_tests", REPO_DIR / "scripts" / "installer_common.py"
)
assert COMMON_SPEC is not None and COMMON_SPEC.loader is not None
INSTALLER_COMMON = importlib.util.module_from_spec(COMMON_SPEC)
sys.modules[COMMON_SPEC.name] = INSTALLER_COMMON
COMMON_SPEC.loader.exec_module(INSTALLER_COMMON)


class InstallScriptIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.systemd_dir = self.root / "systemd"
        self.libexec_dir = self.root / "libexec"
        self.log_path = self.root / "systemctl.log"
        self._write_fake_command(
            "systemctl",
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$OLLAMA_QUEUE_TEST_LOG\"\n"
            "exit 0\n",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_fake_command(self, name: str, content: str) -> None:
        path = self.fake_bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.fake_bin}{os.pathsep}{environment['PATH']}",
                "OLLAMA_QUEUE_SYSTEMD_DIR": str(self.systemd_dir),
                "OLLAMA_QUEUE_LIBEXEC_PROXY": str(self.libexec_dir / "ollama_model_queue_proxy.py"),
                "OLLAMA_QUEUE_STATE_DIR": str(self.root / "state"),
                "OLLAMA_QUEUE_SYSTEMCTL": str(self.fake_bin / "systemctl"),
                "OLLAMA_QUEUE_TEST_LOG": str(self.log_path),
                "OLLAMA_QUEUE_PLATFORM": "linux",
                "OLLAMA_QUEUE_TEST_MODE": "1",
                "CHANGE_PORT": "TRUE",
            }
        )
        return environment

    def _run(self, script: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(script)],
            cwd=REPO_DIR,
            env=self._environment(),
            check=True,
            capture_output=True,
            text=True,
        )

    def _run_piped(self, script: Path) -> subprocess.CompletedProcess[str]:
        environment = self._environment()
        environment.update(
            {
                "CHANGE_PORT": "TRUE",
                "OLLAMA_QUEUE_RAW_BASE": f"file://{REPO_DIR}",
            }
        )
        return subprocess.run(
            ["python3", "-"],
            cwd=REPO_DIR,
            env=environment,
            input=script.read_text(encoding="utf-8"),
            check=True,
            capture_output=True,
            text=True,
        )

    def _run_exec(self, script: Path) -> subprocess.CompletedProcess[str]:
        environment = self._environment()
        environment["OLLAMA_QUEUE_RAW_BASE"] = f"file://{REPO_DIR}"
        source = script.read_text(encoding="utf-8")
        code = f"import os; os.environ['CHANGE_PORT']='TRUE'; exec({source!r})"
        return subprocess.run(
            ["python3", "-c", code],
            cwd=REPO_DIR,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_install_then_uninstall_restores_standard_port(self) -> None:
        install_result = self._run(INSTALL_SCRIPT)

        proxy_unit = self.systemd_dir / "ollama-model-queue-proxy.service"
        dropin = self.systemd_dir / "ollama.service.d" / "90-ollama-model-queue-proxy.conf"
        proxy_binary = self.libexec_dir / "ollama_model_queue_proxy.py"
        self.assertIn("Proxy port: 11434; Ollama backend: 11435.", install_result.stdout)
        self.assertTrue(proxy_unit.is_file())
        self.assertTrue(proxy_binary.is_file())
        self.assertIn("OLLAMA_QUEUE_LISTEN_PORT=11434", proxy_unit.read_text(encoding="utf-8"))
        self.assertIn("OLLAMA_UPSTREAM_URL=http://127.0.0.1:11435", proxy_unit.read_text(encoding="utf-8"))
        self.assertIn("OLLAMA_HOST=127.0.0.1:11435", dropin.read_text(encoding="utf-8"))

        self._run(UNINSTALL_SCRIPT)

        self.assertFalse(proxy_unit.exists())
        self.assertFalse(proxy_binary.exists())
        self.assertFalse(dropin.exists())
        commands = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("restart ollama.service", commands)
        self.assertIn("enable --now ollama-model-queue-proxy.service", commands)

    def test_piped_install_and_uninstall_bootstrap_from_raw_base(self) -> None:
        install_result = self._run_piped(INSTALL_SCRIPT)
        self.assertIn("Proxy port: 11434; Ollama backend: 11435.", install_result.stdout)
        self.assertTrue((self.systemd_dir / "ollama-model-queue-proxy.service").is_file())
        self.assertTrue((self.libexec_dir / "ollama_model_queue_proxy.py").is_file())

        uninstall_result = self._run_piped(UNINSTALL_SCRIPT)
        self.assertIn("Removed Ollama Model Queue Proxy from linux.", uninstall_result.stdout)
        self.assertFalse((self.systemd_dir / "ollama-model-queue-proxy.service").exists())
        self.assertFalse((self.libexec_dir / "ollama_model_queue_proxy.py").exists())

    def test_macos_and_windows_artifact_builders(self) -> None:
        target = INSTALLER_COMMON.Target(
            "macos",
            Path("/Users/example"),
            "example",
            501,
            20,
            Path("/Users/example/Library/Application Support/Ollama Model Queue"),
            Path("/Users/example/Library/Application Support/Ollama Model Queue/ollama_model_queue_proxy.py"),
        )
        settings = INSTALLER_COMMON.port_settings(True)
        plist = INSTALLER_COMMON.launch_agent_plist(target, settings)
        self.assertEqual(plist["Label"], "com.megamen32.ollama-model-queue-proxy")
        self.assertEqual(plistlib.loads(plistlib.dumps(plist))["Label"], plist["Label"])

        launcher = INSTALLER_COMMON.windows_launcher_text(target, settings)
        self.assertIn("OLLAMA_QUEUE_LISTEN_PORT=11434", launcher)
        self.assertIn("OLLAMA_UPSTREAM_URL=http://127.0.0.1:11435", launcher)

    def test_exec_style_bootstrap_used_by_windows_one_liner(self) -> None:
        install_result = self._run_exec(INSTALL_SCRIPT)
        self.assertIn("Proxy port: 11434; Ollama backend: 11435.", install_result.stdout)
        uninstall_result = self._run_exec(UNINSTALL_SCRIPT)
        self.assertIn("Removed Ollama Model Queue Proxy from linux.", uninstall_result.stdout)


if __name__ == "__main__":
    unittest.main()
