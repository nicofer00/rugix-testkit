"""Tests for QEMU VM process management (pure logic, no QEMU required)."""

import io
from pathlib import Path
from unittest.mock import patch


from rugix_testkit.qemu.config import Drive, Pflash, VMConfig
from rugix_testkit.qemu.vm import QemuVM, _parse_size


class TestParseSize:
    def test_kilobytes(self):
        assert _parse_size("4K") == 4 * 1024

    def test_megabytes(self):
        assert _parse_size("64M") == 64 * 1024**2

    def test_gigabytes(self):
        assert _parse_size("16G") == 16 * 1024**3

    def test_terabytes(self):
        assert _parse_size("2T") == 2 * 1024**4

    def test_lowercase(self):
        assert _parse_size("8m") == 8 * 1024**2

    def test_plain_bytes(self):
        assert _parse_size("512") == 512


class TestBuildCmd:
    """Test _build_cmd without running QEMU."""

    def _make_vm(self, **overrides) -> QemuVM:
        defaults = {"arch": "x86_64"}
        defaults.update(overrides)
        config = VMConfig(**defaults)
        vm = QemuVM(config, work_dir=Path("/tmp/test-work"))
        return vm

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_basic_x86(self, _mock_kvm):
        vm = self._make_vm()
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        assert cmd[0] == "qemu-system-x86_64"
        assert "-machine" in cmd
        idx = cmd.index("-machine")
        assert cmd[idx + 1] == "q35"
        assert "-nographic" in cmd
        assert "-serial" in cmd

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_interactive_no_serial_redirect(self, _mock_kvm):
        vm = self._make_vm()
        cmd = vm._build_cmd(ssh_port=2222, interactive=True)
        assert "-serial" not in cmd

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_non_interactive_has_serial(self, _mock_kvm):
        vm = self._make_vm()
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        idx = cmd.index("-serial")
        assert cmd[idx + 1] == "mon:stdio"

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=True)
    def test_kvm_enabled(self, _mock_kvm):
        vm = self._make_vm()
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        assert "-enable-kvm" in cmd
        idx = cmd.index("-cpu")
        assert cmd[idx + 1] == "host"

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_custom_cpu(self, _mock_kvm):
        vm = self._make_vm(cpu="cortex-a57")
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        idx = cmd.index("-cpu")
        assert cmd[idx + 1] == "cortex-a57"

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_no_cpu_without_kvm(self, _mock_kvm):
        vm = self._make_vm()
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        assert "-cpu" not in cmd

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_ssh_port_forwarding(self, _mock_kvm):
        vm = self._make_vm()
        cmd = vm._build_cmd(ssh_port=5555, interactive=False)
        nic_idx = cmd.index("-nic")
        nic_val = cmd[nic_idx + 1]
        assert "hostfwd=tcp:127.0.0.1:5555-:22" in nic_val

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_custom_net_with_hostfwd(self, _mock_kvm):
        vm = self._make_vm(net="user,model=virtio-net-pci,hostfwd=tcp::9999-:22")
        cmd = vm._build_cmd(ssh_port=5555, interactive=False)
        nic_idx = cmd.index("-nic")
        nic_val = cmd[nic_idx + 1]
        # Should not duplicate hostfwd.
        assert nic_val.count("hostfwd=") == 1
        assert "hostfwd=tcp::9999-:22" in nic_val

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_drive_no_overlay(self, _mock_kvm):
        vm = self._make_vm(
            drives=[Drive(file=Path("/images/disk.img"), format="raw")],
        )
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        drive_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-drive"]
        assert any(
            "file=/images/disk.img" in d and "format=raw" in d for d in drive_args
        )

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_drive_with_interface(self, _mock_kvm):
        vm = self._make_vm(
            drives=[Drive(file=Path("/disk.img"), interface="virtio")],
        )
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        drive_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-drive"]
        assert any("if=virtio" in d for d in drive_args)

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_drive_overlay_uses_work_dir(self, _mock_kvm):
        vm = self._make_vm(
            drives=[Drive(file=Path("/disk.img"), overlay=True)],
        )
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        drive_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-drive"]
        assert any("/tmp/test-work/drive0.qcow2" in d for d in drive_args)

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_pflash_readonly(self, _mock_kvm):
        vm = self._make_vm(
            pflash=[Pflash(file=Path("/fw/code.fd"), readonly=True)],
        )
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        drive_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-drive"]
        pf_drives = [d for d in drive_args if "pflash" in d]
        assert len(pf_drives) == 1
        assert "readonly=on" in pf_drives[0]
        assert "file=/fw/code.fd" in pf_drives[0]

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_pflash_mutable_uses_work_dir(self, _mock_kvm):
        vm = self._make_vm(
            pflash=[Pflash(file=Path("/fw/vars.fd"))],
        )
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        drive_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-drive"]
        pf_drives = [d for d in drive_args if "pflash" in d]
        assert len(pf_drives) == 1
        assert "/tmp/test-work/pflash0.raw" in pf_drives[0]
        assert "readonly=on" not in pf_drives[0]

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_extra_args(self, _mock_kvm):
        vm = self._make_vm(extra_args=["-device", "virtio-rng-pci"])
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        assert "-device" in cmd
        idx = cmd.index("-device")
        assert cmd[idx + 1] == "virtio-rng-pci"

    @patch("rugix_testkit.qemu.vm._kvm_available", return_value=False)
    def test_memory_and_smp(self, _mock_kvm):
        vm = self._make_vm(memory=2048, smp=8)
        cmd = vm._build_cmd(ssh_port=2222, interactive=False)
        m_idx = cmd.index("-m")
        assert cmd[m_idx + 1] == "2048"
        smp_idx = cmd.index("-smp")
        assert cmd[smp_idx + 1] == "8"


class TestSerial:
    def test_write_serial_writes_to_qemu_stdin(self):
        vm = QemuVM(VMConfig(arch="x86_64"))
        stdin = io.BytesIO()
        vm._process = _FakeProcess(stdin=stdin)

        vm.write_serial(b"x\n")

        assert stdin.getvalue() == b"x\n"

    def test_write_serial_requires_running_process(self):
        vm = QemuVM(VMConfig(arch="x86_64"))

        try:
            vm.write_serial(b"x")
        except RuntimeError as error:
            assert "not running" in str(error)
        else:
            raise AssertionError("expected RuntimeError")

    def test_wait_for_serial_returns_output(self):
        vm = QemuVM(VMConfig(arch="x86_64"))
        vm._serial_log.append("boot\nready\n")

        assert vm.wait_for_serial("ready", timeout=0.1) == "boot\nready\n"


class _FakeProcess:
    def __init__(self, *, stdin: io.BytesIO) -> None:
        self.stdin = stdin

    def poll(self) -> None:
        return None
