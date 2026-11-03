"""Standalone finite file preflight regressions; no pytest/app/Docker/provider."""
import argparse
import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "artifacts/next-release/m7-stream-latency-20261005/preflight-hardening-first"
OUT = ROOT / "artifacts/next-release/m7-stream-latency-20261005/preflight-hardening-second"
SCRIPT = ROOT / "scripts/next-stream-latency-probe.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.ROOT = ROOT
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf8")


def main():
    global OUT
    parser = argparse.ArgumentParser(); parser.add_argument("--before-only", action="store_true")
    parser.add_argument("--attempt", default="preflight-hardening-third")
    args = parser.parse_args()
    if not re.fullmatch("[a-z0-9-]{1,32}", args.attempt):
        raise ValueError("Invalid owned finite preflight attempt")
    OUT = ROOT / "artifacts/next-release/m7-stream-latency-20261005" / args.attempt
    probe = load(SCRIPT, "next_stream_latency_precheck_current")
    OUT.mkdir(parents=True, exist_ok=False)
    source_before = digest(SCRIPT)
    before = load(HISTORY / "before-script.py", "next_stream_latency_precheck_old")
    tree = ast.parse((HISTORY / "before-script.py").read_text("utf8"))
    original = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "host_probe")
    prefix = []
    for node in original.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "project" for target in node.targets):
            break
        prefix.append(node)
    function = ast.FunctionDef(name="old_precheck", args=original.args, body=prefix + [ast.Return(value=ast.Name(id="entries", ctx=ast.Load()))], decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "actual-old-preflight-prefix", "exec"), before.__dict__)
    # Retained synthetic fixtures: a SUCCESSFUL full gate below is deliberately
    # fabricated ONLY to test metadata acceptance. It is never a product result.
    base = OUT / "fixtures" / "synthetic-full822"; base.mkdir(parents=True, exist_ok=False)
    frozen = base / "frozen-server"; frozen.mkdir()
    old_core = ROOT / "artifacts/next-release/m0-short-secret-backend-20261005/final-core"
    old_manifest = json.loads((old_core / "source-hashes.json").read_text("utf8"))
    manifest = {}
    for name in old_manifest:
        relative = probe.public_source_relative(name)
        path = frozen / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# synthetic metadata-only fixture; NOT executable backend\n")
        manifest[name] = {"sha256": digest(path), "origin": "synthetic-preflight-only"}
    xml = ET.fromstring((old_core / "junit.xml").read_bytes())
    for suite in xml.iter("testsuite"):
        suite.set("failures", "0")
    for case in xml.iter("testcase"):
        for failure in list(case.findall("failure")):
            case.remove(failure)
    ET.ElementTree(xml).write(base / "junit.xml", encoding="utf8", xml_declaration=True)
    put(base / "source-hashes.json", manifest)
    execution = {"project": "synthetic-preflight-only", "sourceStable": True, "sourceCount": 222,
                 "scope": "current-next-candidate-core", "terminalExitCode": 0, "old789Subset": True, "old771Subset": True,
                 "newCaseCount": 51, "sourceManifestSha256": digest(base / "source-hashes.json"),
                 "junit": {"tests": 822, "errors": 0, "failures": 0, "skipped": 0, "sha256": digest(base / "junit.xml")}}
    cleanup = {"project": "synthetic-preflight-only", "quietCommandExitCode": 0, "observed": "0|0", "cleaned": True,
               "nonterminalOrRecoverable": 0, "unsettledProviderIntents": 0, "after": {"containers": [], "networks": [], "volumes": []}}
    def refresh():
        put(base / "execution.json", execution); put(base / "cleanup.json", cleanup)
        gate = {"verified": True, "runnerExitCode": 0, "pytestExitCode": 0,
                "executionSha256": digest(base / "execution.json"), "cleanupSha256": digest(base / "cleanup.json")}
        put(base / "final-gate.json", gate)
    refresh()
    def params(folder=base, diagnostic=False, old=False):
        gate = folder / ("execution.json" if old else "final-gate.json")
        return SimpleNamespace(run=True, confirm_final_source_stop=True, backend_root=str(folder / "frozen-server"),
                               manifest=str(folder / "source-hashes.json"), gate=str(gate), manifest_sha256=digest(folder / "source-hashes.json"),
                               gate_sha256=digest(gate), expected_full_tests=822, diagnostic_before=diagnostic,
                               runtime_image="sha256:" + "1" * 64, pg_image="sha256:" + "2" * 64, attempt="precheck-only")
    outside = ROOT / "artifacts" / ("m7-stream-latency-owned-outside-boundary-20261005-" + args.attempt + ".json")
    with outside.open("x", encoding="utf8") as stream:
        json.dump(manifest, stream)
    junction = OUT / "fixtures" / "owned-junction"
    def psquote(value): return "'" + str(value).replace("'", "''") + "'"
    if os.name == "nt":
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "New-Item -ItemType Junction -Path " + psquote(junction) + " -Target " + psquote(frozen) + " -ErrorAction Stop | Out-Null"],
                       check=True, capture_output=True, creationflags=0x08000000)
    else:
        junction.symlink_to(frozen, target_is_directory=True)
    rows_before, rows_after = [], []
    def accepts(function, value):
        try: function(value); return True
        except (ValueError, AssertionError, FileNotFoundError): return False
    try:
        focused = params(old=True)
        focused_record = deepcopy(execution); focused_record["junit"]["tests"] = 19
        put(base / "execution.json", focused_record); focused.gate_sha256 = digest(base / "execution.json")
        rows_before.append({"case": "focused19-execution-instead-of-final-gate", "expectedAccepted": False,
                            "actualAccepted": accepts(before.old_precheck, focused)})
        refresh()
        raw = params(old=True); raw.manifest = str(outside); raw.manifest_sha256 = digest(outside)
        rows_before.append({"case": "outside-next-release-manifest", "expectedAccepted": False,
                            "actualAccepted": accepts(before.old_precheck, raw)})
        linked = params(old=True); linked.backend_root = str(junction)
        rows_before.append({"case": "backend-ancestor-junction", "expectedAccepted": False,
                            "actualAccepted": accepts(before.old_precheck, linked)})
        write_name = OUT / "before-red.json"
        with write_name.open("x", encoding="utf8") as stream:
            json.dump({"scope": "Actual old host precheck prefix, bounded own synthetic files only", "oldScriptSha256": digest(HISTORY / "before-script.py"),
                       "checks": rows_before, "failed": sum(r["actualAccepted"] != r["expectedAccepted"] for r in rows_before),
                       "dockerStarted": False, "providerCalls": 0}, stream, indent=2)
        if args.before_only:
            return 1 if any(r["actualAccepted"] != r["expectedAccepted"] for r in rows_before) else 0
        def check(name, function, expect):
            actual = accepts(lambda _: function(), None)
            rows_after.append({"case": name, "expectedAccepted": expect, "actualAccepted": actual})
        check("synthetic-full822-metadata-acceptance-not-a-real-gate", lambda: probe.preflight_inputs(params()), True)
        focused = params(old=True)
        check("execution-json-cannot-be-gate", lambda: probe.preflight_inputs(focused), False)
        raw = params(); raw.manifest = str(outside); raw.manifest_sha256 = digest(outside)
        check("outside-next-release-rejected", lambda: probe.preflight_inputs(raw), False)
        linked = params(); linked.backend_root = str(junction)
        check("ancestor-junction-rejected", lambda: probe.preflight_inputs(linked), False)
        check("source-parent-junction-rejected", lambda: probe.safe_artifact_path(junction / "app/providers.py"), False)
        check("source-traversal-rejected", lambda: probe.public_source_relative("server/app/../../owned.txt"), False)
        check("hidden-env-name-rejected", lambda: probe.public_source_relative("server/app/.env"), False)
        check("windows-device-name-rejected", lambda: probe.public_source_relative("server/app/NUL.py"), False)
        check("windows-ADS-name-rejected", lambda: probe.public_source_relative("server/app/models.py:owned"), False)
        original = (base / "execution.json").read_bytes()
        (base / "execution.json").write_bytes(original + b"\n")
        check("sibling-execution-hash-tamper", lambda: probe.preflight_inputs(params()), False)
        (base / "execution.json").write_bytes(original)
        cleanup["observed"] = "1|0"; refresh()
        check("nonquiet-cleanup-rejected", lambda: probe.preflight_inputs(params()), False)
        cleanup["observed"] = "0|0"; refresh()
        execution["scope"] = "focused-nineteen"; refresh()
        check("focused-scope-rejected", lambda: probe.preflight_inputs(params()), False)
        execution["scope"] = "current-next-candidate-core"; refresh()
        execution["sourceCount"] = 221; refresh()
        check("wrong-source-count-rejected", lambda: probe.preflight_inputs(params()), False)
        execution["sourceCount"] = 222; refresh()
        wrong = params(); wrong.expected_full_tests = 19
        check("focused-declared-count-rejected", lambda: probe.preflight_inputs(wrong), False)
        wrong = params(); wrong.expected_full_tests = 825
        check("actual-XML-not-declared825-rejected", lambda: probe.preflight_inputs(wrong), False)
        oversized = OUT / "fixtures/oversized.json"; oversized.write_bytes(b"x" * (probe.MAX_JSON_BYTES + 1))
        check("oversized-input-rejected", lambda: probe.bounded_artifact_read(oversized, probe.MAX_JSON_BYTES), False)
        # Prove lexical rejection precedes ALL lstat/resolve/open operations.
        saved_lstat = probe.os.lstat
        def impossible_lstat(*args, **kwargs): raise AssertionError("Input reached metadata access")
        probe.os.lstat = impossible_lstat
        try:
            try: probe.safe_artifact_path(outside)
            except ValueError: lexical_first = True
            else: lexical_first = False
        finally: probe.os.lstat = saved_lstat
        rows_after.append({"case": "outside-boundary-rejected-before-lstat-resolve-read", "expectedAccepted": True, "actualAccepted": lexical_first})
        check("actual-old822-failed-wholegate-rejected-final", lambda: probe.preflight_inputs(params(old_core)), False)
        check("actual-pinned-old822-diagnostic-only-accepted", lambda: probe.preflight_inputs(params(old_core, diagnostic=True)), True)
        diagnostic = params(diagnostic=True)
        check("synthetic-green-cannot-use-diagnostic-exception", lambda: probe.preflight_inputs(diagnostic), False)
        case = "a" * 32 + ".jsonl"
        rows_after.append({"case": "owned-receipt-filename-literal-dot", "expectedAccepted": True,
                           "actualAccepted": bool(re.fullmatch(r"[0-9a-f]{32}[.]jsonl", case))})
        failures = sum(r["expectedAccepted"] != r["actualAccepted"] for r in rows_after)
        report = {"scope": "Standalone pure public-file preflight; synthetic success metadata is NOT a product pass",
                  "scriptSha256Before": source_before, "scriptSha256After": digest(SCRIPT), "sourceStable": source_before == digest(SCRIPT),
                  "checks": rows_after, "passed": len(rows_after)-failures, "failed": failures, "actualExitCode": int(bool(failures)),
                  "dockerStarted": False, "devicesStarted": False, "modelCalls": 0, "paidCalls": 0,
                  "actualPerformanceSamples": 0, "oldFinalCoreGateUnmodifiedSha256": digest(old_core / "final-gate.json")}
        with (OUT / "after-green.json").open("x", encoding="utf8") as stream: json.dump(report, stream, indent=2)
        print(json.dumps({"checks": len(rows_after), "passed": report["passed"], "failed": failures, "actualExitCode": report["actualExitCode"]}))
        return report["actualExitCode"]
    finally:
        # Remove only the exact owned link, never target data. No recursive delete.
        assert junction.parent == OUT / "fixtures" and junction.resolve() == frozen.resolve()
        if os.name == "nt":
            assert os.lstat(junction).st_file_attributes & 0x400
            junction.rmdir()  # single verified reparse directory; never recursive
        else: junction.unlink()
        assert not junction.exists() and frozen.is_dir()
        with (OUT / "link-cleanup.json").open("x", encoding="utf8") as stream:
            json.dump({"exactLinkRemoved": True, "recursiveDelete": False, "targetRetained": True}, stream)


if __name__ == "__main__":
    raise SystemExit(main())
