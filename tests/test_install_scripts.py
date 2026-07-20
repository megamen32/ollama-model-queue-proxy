import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_DIR / "scripts" / "install.sh"
UNINSTALL_SCRIPT = REPO_DIR / "scripts" / "uninstall.sh"


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
            "id",
            "#!/bin/sh\nif [ \"$1\" = \"-u\" ]; then echo 0; else echo 0; fi\n",
        )
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
                "OLLAMA_QUEUE_LIBEXEC_DIR": str(self.libexec_dir),
                "OLLAMA_QUEUE_SYSTEMCTL": str(self.fake_bin / "systemctl"),
                "OLLAMA_QUEUE_TEST_LOG": str(self.log_path),
            }
        )
        return environment

    def _run(self, script: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script)],
            cwd=REPO_DIR,
            env=self._environment(),
            check=True,
            capture_output=True,
            text=True,
        )

    def test_install_then_uninstall_restores_standard_port(self) -> None:
        install_result = self._run(INSTALL_SCRIPT)

        proxy_unit = self.systemd_dir / "ollama-model-queue-proxy.service"
        dropin = self.systemd_dir / "ollama.service.d" / "90-ollama-model-queue-proxy.conf"
        proxy_binary = self.libexec_dir / "ollama_model_queue_proxy.py"
        self.assertIn("127.0.0.1:11434", install_result.stdout)
        self.assertIn("127.0.0.1:11435", install_result.stdout)
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


if __name__ == "__main__":
    unittest.main()
