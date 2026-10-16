"""Exercise the promotion CLI against a process-level fake Snapcraft store."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github" / "scripts" / "promote-snap.py"
VERSION = "1.12.3"
ARCHITECTURES = ("amd64", "arm64")
REVISIONS = {"amd64": "101", "arm64": "202"}


def status(arch, candidate_version=VERSION, candidate_revision=None,
           stable_version="1.12.2", stable_revision="90", track="latest"):
    candidate_revision = candidate_revision or REVISIONS[arch]
    return (
        "Track    Arch    Channel    Version    Revision    Progress\n"
        f"{track} {arch} stable {stable_version} {stable_revision} -\n"
        f"       {arch} candidate {candidate_version} {candidate_revision} -\n"
        f"       {arch} beta ↑ ↑ -\n"
        f"       {arch} edge ↑ ↑ -\n"
    )


FAKE_SNAPCRAFT = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

scenario = json.loads(Path(os.environ["SNAP_TEST_SCENARIO"]).read_text())
log = Path(os.environ["SNAP_TEST_CALLS"])
calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
args = sys.argv[1:]
with log.open("a") as stream:
    stream.write(json.dumps(args) + "\n")
if len(args) == 4 and args[:3] == ["status", "metanorma", "--arch"]:
    responses = scenario["status"][args[3]]
    index = sum(call == args for call in calls)
    response = responses[min(index, len(responses) - 1)]
elif len(args) == 4 and args[:2] == ["release", "metanorma"] and args[3] == "stable":
    response = scenario.get("release", {}).get(args[2], {})
else:
    print("Unexpected Snapcraft arguments: " + repr(args), file=sys.stderr)
    sys.exit(99)
print(response.get("stdout", ""), end="")
print(response.get("stderr", ""), end="", file=sys.stderr)
sys.exit(response.get("returncode", 0))
'''


class PromoteSnapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        executable = self.directory / "snapcraft"
        executable.write_text(FAKE_SNAPCRAFT)
        executable.chmod(0o755)
        self.calls_path = self.directory / "calls.jsonl"
        self.scenario = {"status": {}}
        for arch in ARCHITECTURES:
            self.scenario["status"][arch] = [
                {"stdout": status(arch)},
                {"stdout": status(arch, stable_version=VERSION,
                                  stable_revision=REVISIONS[arch])},
            ]

    def run_promotion(self, extra=(), arguments=None):
        if self.calls_path.exists():
            self.calls_path.unlink()
        scenario_path = self.directory / "scenario.json"
        scenario_path.write_text(json.dumps(self.scenario))
        environment = dict(os.environ)
        environment.update(
            PATH=str(self.directory) + os.pathsep + environment.get("PATH", ""),
            SNAP_TEST_SCENARIO=str(scenario_path),
            SNAP_TEST_CALLS=str(self.calls_path),
        )
        if arguments is None:
            arguments = ["--version", VERSION, "--architectures", ",".join(ARCHITECTURES)]
        result = subprocess.run(
            [sys.executable, str(HELPER), *arguments, *extra],
            capture_output=True, text=True, env=environment, timeout=15,
        )
        calls = ([json.loads(line) for line in self.calls_path.read_text().splitlines()]
                 if self.calls_path.exists() else [])
        return result, calls

    def assert_preflight_failure(self, extra=(), arguments=None):
        result, calls = self.run_promotion(extra, arguments)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertTrue(result.stderr.strip(), "Failure must explain itself on stderr")
        self.assertFalse([call for call in calls if call[0] == "release"], calls)
        return result, calls

    def test_two_architectures_resolved_before_release_and_verified_after(self):
        result, calls = self.run_promotion()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [
            ["status", "metanorma", "--arch", "amd64"],
            ["status", "metanorma", "--arch", "arm64"],
            ["release", "metanorma", "101", "stable"],
            ["release", "metanorma", "202", "stable"],
            ["status", "metanorma", "--arch", "amd64"],
            ["status", "metanorma", "--arch", "arm64"],
        ])

    def test_stale_or_missing_second_candidate_releases_nothing(self):
        for output in (status("arm64", candidate_version="1.12.2"),
                       status("arm64").replace(f"       arm64 candidate {VERSION} 202 -\n", "")):
            with self.subTest(output=output):
                self.scenario["status"]["arm64"][0] = {"stdout": output}
                self.assert_preflight_failure()

    def test_probe_failure_releases_nothing_even_with_parseable_stdout(self):
        self.scenario["status"]["arm64"][0].update(
            returncode=1, stderr="store temporarily unavailable\n")
        result, _ = self.assert_preflight_failure()
        self.assertIn("store temporarily unavailable", result.stderr)

    def test_candidate_from_another_track_cannot_satisfy_latest(self):
        self.scenario["status"]["arm64"][0]["stdout"] = status("arm64", track="2.0")
        self.assert_preflight_failure()

    def test_latest_track_selected_when_another_track_has_same_arch(self):
        self.scenario["status"]["arm64"][0]["stdout"] += (
            f"2.0 arm64 candidate 9.9.9 999 -\n")
        result, calls = self.run_promotion()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[2] for call in calls if call[0] == "release"], ["101", "202"])

    def test_status_accepts_full_rows_and_risk_only_continuations(self):
        original = json.loads(json.dumps(self.scenario))
        for shape in ("full", "risk-only"):
            with self.subTest(shape=shape):
                self.scenario = json.loads(json.dumps(original))
                for arch in ARCHITECTURES:
                    for response in self.scenario["status"][arch]:
                        replacement = f"latest {arch} " if shape == "full" else "       "
                        response["stdout"] = response["stdout"].replace(
                            f"       {arch} ", replacement)
                result, calls = self.run_promotion()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([call[2] for call in calls if call[0] == "release"],
                                 ["101", "202"])

    def test_inherited_unavailable_or_progressive_candidate_cannot_be_promoted(self):
        for candidate in ("↑ ↑ -", "- - -", f"{VERSION} 202 10%"):
            with self.subTest(candidate=candidate):
                self.scenario["status"]["arm64"][0]["stdout"] = status("arm64").replace(
                    f"candidate {VERSION} 202 -", f"candidate {candidate}")
                self.assert_preflight_failure()

    def test_unavailable_stable_does_not_block_valid_candidate(self):
        for arch in ARCHITECTURES:
            self.scenario["status"][arch][0]["stdout"] = status(
                arch, stable_version="-", stable_revision="-")
        result, calls = self.run_promotion()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[2] for call in calls if call[0] == "release"], ["101", "202"])

    def test_branch_expiry_columns_preserve_actual_candidate_and_stable(self):
        original = json.loads(json.dumps(self.scenario))
        for expiry in ("2026-11-01T00:00:00Z", "2026-11-01 00:00:00"):
            with self.subTest(expiry=expiry):
                self.scenario = json.loads(json.dumps(original))
                for arch in ARCHITECTURES:
                    for response in self.scenario["status"][arch]:
                        lines = response["stdout"].splitlines()
                        response["stdout"] = "\n".join(
                            [lines[0] + " Expires at"] + [line + " -" for line in lines[1:]]
                            + [f"       {arch} edge/testing 9.9.9 999 - {expiry}"]
                        ) + "\n"
                result, calls = self.run_promotion()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([call[2] for call in calls if call[0] == "release"],
                                 ["101", "202"])

    def test_branch_candidate_cannot_replace_actual_candidate(self):
        response = self.scenario["status"]["arm64"][0]
        lines = response["stdout"].replace(" candidate ", " candidate/testing ").splitlines()
        response["stdout"] = "\n".join(
            [lines[0] + " Expires at"] + [line + " -" for line in lines[1:]]
        ) + "\n"
        self.assert_preflight_failure()

    def test_malformed_branch_expiry_row_fails_closed(self):
        response = self.scenario["status"]["arm64"][0]
        lines = response["stdout"].splitlines()
        response["stdout"] = "\n".join(
            [lines[0] + " Expires at"] + [line + " -" for line in lines[1:]]
            + [f"       arm64 edge/testing {VERSION} 303 - 2026-11-01T00:00:00Z unexpected"]
        ) + "\n"
        self.assert_preflight_failure()

    def test_malformed_and_ambiguous_candidate_tables_fail_closed(self):
        outputs = (
            "not a status table\n",
            status("arm64").replace(f"candidate {VERSION} 202", f"candidate {VERSION} invalid"),
            status("arm64") + f"latest arm64 candidate {VERSION} 303 -\n",
            status("arm64") + f"latest arm64 candidate {VERSION} 202 -\n",
            status("amd64"),
        )
        for output in outputs:
            with self.subTest(output=output):
                self.scenario["status"]["arm64"][0]["stdout"] = output
                self.assert_preflight_failure()

    def test_manual_revision_set_must_be_complete_and_match_discovery(self):
        for revisions in ("101", "101,303", "101,202,303", "101,101", "abc,202", "101,0", ""):
            with self.subTest(revisions=revisions):
                self.assert_preflight_failure(extra=["--revisions", revisions])

    def test_manual_revision_set_is_unordered_but_release_order_is_architecture_order(self):
        result, calls = self.run_promotion(extra=["--revisions", "202,101"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[2] for call in calls if call[0] == "release"], ["101", "202"])

    def test_shared_revision_is_released_once_and_verified_for_both_architectures(self):
        for response in self.scenario["status"]["arm64"]:
            response["stdout"] = response["stdout"].replace(" 202 ", " 101 ")
        result, calls = self.run_promotion(extra=["--revisions", "101"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[2] for call in calls if call[0] == "release"], ["101"])
        self.assertEqual(calls[-2:], [
            ["status", "metanorma", "--arch", "amd64"],
            ["status", "metanorma", "--arch", "arm64"],
        ])

    def test_required_inputs_reject_before_snapcraft(self):
        for arguments in ([], ["--version", VERSION], ["--architectures", "amd64"],
                          ["--version", "", "--architectures", "amd64"]):
            with self.subTest(arguments=arguments):
                _, calls = self.assert_preflight_failure(arguments=arguments)
                self.assertEqual(calls, [])

    def test_release_failure_reports_completed_revision_and_stops(self):
        self.scenario["release"] = {"202": {"returncode": 1, "stderr": "release refused\n"}}
        result, calls = self.run_promotion()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("101", result.stderr)
        self.assertIn("release refused", result.stderr)
        self.assertEqual(len(calls), 4, calls)

    def test_first_release_failure_does_not_attempt_second(self):
        self.scenario["release"] = {"101": {"returncode": 1, "stderr": "release refused\n"}}
        result, calls = self.run_promotion()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([call[2] for call in calls if call[0] == "release"], ["101"])

    def test_stable_readback_checks_frozen_revision_and_version(self):
        for version, revision in ((VERSION, "999"), ("1.12.2", "202")):
            with self.subTest(version=version, revision=revision):
                self.scenario["status"]["arm64"][1]["stdout"] = status(
                    "arm64", stable_version=version, stable_revision=revision)
                result, calls = self.run_promotion()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("101", result.stderr)
                self.assertIn("202", result.stderr)
                self.assertIn("amd64", result.stderr)
                self.assertEqual(len([call for call in calls if call[0] == "release"]), 2)

    def test_candidate_change_after_release_does_not_change_frozen_revision(self):
        for arch in ARCHITECTURES:
            self.scenario["status"][arch][1]["stdout"] = status(
                arch, candidate_version="1.12.4", candidate_revision="999",
                stable_version=VERSION, stable_revision=REVISIONS[arch])
        result, calls = self.run_promotion()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[2] for call in calls if call[0] == "release"], ["101", "202"])

    def test_readback_probe_failure_is_reported_after_release(self):
        self.scenario["status"]["arm64"][1].update(returncode=1, stderr="readback unavailable\n")
        result, calls = self.run_promotion()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("readback unavailable", result.stderr)
        self.assertIn("101", result.stderr)
        self.assertIn("202", result.stderr)
        self.assertEqual(len([call for call in calls if call[0] == "release"]), 2)


if __name__ == "__main__":
    unittest.main()
