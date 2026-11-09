from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


def shell_path(path):
    value = path.as_posix()
    if len(value) > 2 and value[1] == ":":
        return "/" + value[0].lower() + value[2:]
    return value


def find_shell():
    for name in ("sh", "bash"):
        candidate = shutil.which(name)
        if candidate:
            return candidate
    git = shutil.which("git")
    if git:
        candidate = Path(git).parent.parent / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


class CpuProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = find_shell()
        if cls.shell is None:
            raise unittest.SkipTest("POSIX shell unavailable")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="multivideo_cpu_test_")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.policies = self.root / "cpufreq"
        self.state = self.root / "saved"
        self.uptime = self.root / "uptime"
        self.calls = self.root / "calls"
        self.mode = self.root / "mode"
        self.uptime.write_text("10.00 0.00\n", encoding="utf-8")
        self.calls.write_text("0\n", encoding="utf-8")
        self.mode.write_text("success\n", encoding="utf-8")
        for name, governor in (("policy0", "schedutil"), ("policy1", "powersave")):
            policy = self.policies / name
            policy.mkdir(parents=True)
            for field, value in {
                "scaling_governor": governor,
                "scaling_available_governors": "schedutil powersave performance",
                "scaling_cur_freq": "600000",
                "scaling_min_freq": "408000",
                "scaling_max_freq": "1800000",
            }.items():
                (policy / field).write_text(value + "\n", encoding="utf-8")
        self.server = self.root / "server"
        self.server.write_text(
            "#!/bin/sh\n"
            f"calls='{shell_path(self.calls)}'\n"
            f"mode_file='{shell_path(self.mode)}'\n"
            f"uptime_file='{shell_path(self.uptime)}'\n"
            f"governor_file='{shell_path(self.governor(0))}'\n"
            "phase=$(cat \"$calls\")\n"
            "phase=$((phase + 1))\n"
            "printf '%s\\n' \"$phase\" > \"$calls\"\n"
            "printf '%s.00 0.00\\n' \"$((10 + phase))\" > \"$uptime_file\"\n"
            "mode=$(cat \"$mode_file\")\n"
            "if [ \"$phase\" -eq 2 ] && [ \"$(cat \"$governor_file\")\" != performance ]\n"
            "then\n"
            "    exit 9\n"
            "fi\n"
            "case \"$mode:$phase\" in\n"
            "    fail_first:1|fail_second:2) exit 7 ;;\n"
            "    term_second:2) kill -TERM \"$PPID\"; exit 0 ;;\n"
            "    pipe_second:2) kill -PIPE \"$PPID\"; exit 0 ;;\n"
            "    fail_restore:2)\n"
            "        rm \"$governor_file\" || exit 8\n"
            "        mkdir \"$governor_file\" || exit 8\n"
            "        exit 7\n"
            "        ;;\n"
            "esac\n"
            "printf 'PASS: simulated stream check\\n'\n",
            encoding="utf-8", newline="\n")
        self.server.chmod(0o755)
        source = Path(__file__).with_name("check_multivideo_cpu.sh").read_text(encoding="utf-8")
        replacements = {
            "CPU_POLICY_ROOT=/sys/devices/system/cpu/cpufreq": self.policies,
            "CPU_STATE_DIR=/data/local/tmp/multivideo_cpu_profile.saved": self.state,
            "VIDEO_SERVER=/data/local/tmp/pcie_multivideo_server": self.server,
            "CPU_UPTIME=/proc/uptime": self.uptime,
        }
        for declaration, target in replacements.items():
            self.assertIn(declaration, source)
            source = source.replace(declaration, declaration.split("=")[0] + "='" + shell_path(target) + "'", 1)
        self.script = self.root / "check.sh"
        self.script.write_text(source, encoding="utf-8", newline="\n")

    def governor(self, number):
        return self.policies / f"policy{number}" / "scaling_governor"

    def run_script(self, *arguments):
        return subprocess.run([self.shell, shell_path(self.script), *arguments],
                              capture_output=True, text=True, timeout=60)

    def replace_command(self, name, body):
        source = self.script.read_text(encoding="utf-8")
        marker = "read_value()\n"
        self.assertIn(marker, source)
        source = source.replace(marker, name + "()\n{\n" + body + "\n}\n\n" + marker, 1)
        self.script.write_text(source, encoding="utf-8", newline="\n")

    def assert_restored(self):
        self.assertEqual(self.governor(0).read_text().strip(), "schedutil")
        self.assertEqual(self.governor(1).read_text().strip(), "powersave")
        self.assertFalse(self.state.exists())

    def test_success_restores_both_policies_and_reports_each_case(self):
        result = self.run_script("compare", "60", "9002")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CPU_CASE name=original frames=60 elapsed_seconds=1.00 fps=60.00", result.stdout)
        self.assertIn("CPU_CASE name=performance", result.stdout)
        self.assertIn("CPU_COMPARE_COMPLETE", result.stdout)
        self.assertIn("CPU_GOVERNORS_RESTORED", result.stdout)
        self.assert_restored()

    def test_stream_failure_restores_policies_in_either_case(self):
        for mode in ("fail_first", "fail_second"):
            with self.subTest(mode=mode):
                self.calls.write_text("0\n")
                self.uptime.write_text("10.00 0.00\n")
                self.mode.write_text(mode + "\n")
                result = self.run_script("compare")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CPU_CASE_FAILED", result.stderr)
                self.assert_restored()

    def test_unsupported_governor_does_not_start_capture(self):
        (self.policies / "policy1" / "scaling_available_governors").write_text("powersave\n")
        result = self.run_script("compare")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls.read_text().strip(), "0")
        self.assert_restored()

    def test_termination_after_governor_change_restores_policies(self):
        for mode in ("term_second", "pipe_second"):
            with self.subTest(mode=mode):
                self.calls.write_text("0\n")
                self.uptime.write_text("10.00 0.00\n")
                self.mode.write_text(mode + "\n")
                result = self.run_script("compare")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CPU_GOVERNORS_RESTORED", result.stdout)
                self.assert_restored()

    def test_already_performance_skips_duplicate_comparison(self):
        for number in (0, 1):
            self.governor(number).write_text("performance\n")
        result = self.run_script("compare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CPU_COMPARE_SKIPPED reason=already_performance", result.stdout)
        self.assertEqual(self.calls.read_text().strip(), "0")
        self.assertFalse(self.state.exists())

    def test_invalid_arguments_are_rejected_before_state_changes(self):
        for arguments in (("compare", "1:2"), ("compare", "08"), ("compare", "60", "65536"),
                          ("hold", "0"), ("hold", "060"), ("hold", "601"),
                          ("hold", "10", "9002")):
            with self.subTest(arguments=arguments):
                result = self.run_script(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assert_restored()

    def test_failed_restore_keeps_backup_for_manual_recovery(self):
        self.mode.write_text("fail_restore\n")
        result = self.run_script("compare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CPU_RESTORE_FAILED", result.stderr)
        self.assertEqual((self.state / "policy0").read_text().strip(), "schedutil")
        self.governor(0).rmdir()
        self.governor(0).write_text("performance\n")
        result = self.run_script("restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_restored()

    def test_matching_readback_accepts_write_that_reported_failure(self):
        self.replace_command("dd", '    command dd "$@" || return 1\n    return 1')
        result = self.run_script("compare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CPU_WRITE_WARNING", result.stderr)
        self.assertIn("CPU_COMPARE_COMPLETE", result.stdout)
        self.assert_restored()

    def test_rejected_change_stops_before_performance_capture(self):
        self.replace_command("dd", "    return 1")
        result = self.run_script("compare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CPU_WRITE_FAILED", result.stderr)
        self.assertNotIn("CPU_RESTORE_FAILED", result.stderr)
        self.assertEqual(self.calls.read_text().strip(), "1")
        self.assert_restored()

    def test_legacy_backup_already_restored_needs_no_sysfs_write(self):
        self.state.mkdir()
        (self.state / "policy0").write_text("schedutil\n")
        (self.state / "policy1").write_text("powersave\n")
        self.replace_command("dd", "    return 1")
        result = self.run_script("restore")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CPU_GOVERNORS_RESTORED", result.stdout)
        self.assert_restored()

    def test_hold_restores_without_starting_stream_check(self):
        self.server.unlink()
        self.replace_command("sleep",
                             '    [ "$1" = 180 ] || return 9\n'
                             f'    [ "$(cat \'{shell_path(self.governor(0))}\')" = performance ] || return 9\n'
                             f'    [ "$(cat \'{shell_path(self.governor(1))}\')" = performance ] || return 9')
        result = self.run_script("hold")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CPU_HOLD_READY seconds=180", result.stdout)
        self.assertIn("CPU_HOLD_COMPLETE", result.stdout)
        self.assertIn("CPU_GOVERNORS_RESTORED", result.stdout)
        self.assertEqual(self.calls.read_text().strip(), "0")
        self.assert_restored()

    def test_hold_signal_restores_original_policies(self):
        self.replace_command("sleep", '    kill -TERM "$$"')
        result = self.run_script("hold", "60")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CPU_HOLD_READY seconds=60", result.stdout)
        self.assertNotIn("CPU_HOLD_COMPLETE", result.stdout)
        self.assertIn("CPU_GOVERNORS_RESTORED", result.stdout)
        self.assert_restored()

    def test_hold_rejected_change_restores_without_ready(self):
        self.replace_command("dd", "    return 1")
        result = self.run_script("hold", "60")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CPU_WRITE_FAILED", result.stderr)
        self.assertNotIn("CPU_HOLD_READY", result.stdout)
        self.assertEqual(self.calls.read_text().strip(), "0")
        self.assert_restored()


if __name__ == "__main__":
    unittest.main()
