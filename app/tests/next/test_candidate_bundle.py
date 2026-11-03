"""Public archive integrity and exclusion boundaries; no product acceptance claim."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
loader = importlib.util.spec_from_file_location("candidate_bundle", ROOT / "scripts/next-candidate-bundle.py")
product = importlib.util.module_from_spec(loader); loader.loader.exec_module(product)


class CandidateBundleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan = {"schemaVersion": 1, "buildId": "candidate-public-test", "specSha256": product.SPEC_SHA, "files": [], "evidence": []}
        for name in ["compose.yaml", "server/Dockerfile", "server/requirements.lock", "server/pyproject.toml", "server/app/main.py", "client/Dockerfile", "client/package-lock.json", "client/nginx.conf", "client/dist/index.html", "scripts/start.sh", ".env.example"]:
            self.add(name, b"#!/bin/sh\ntrue\n" if name.endswith(".sh") else b"synthetic-public-input\n")
        self.add("reader-world-next-execution-spec.md", (ROOT / "reader-world-next-execution-spec.md").read_bytes())

    def tearDown(self):
        self.temporary.cleanup()

    def add(self, name, raw, source=None):
        source = source or "frozen/" + name
        path = self.root / source; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        entry = {"archivePath": name, "file": source, "sha256": product.sha(raw)}
        self.plan["files"].append(entry); return entry

    def reject_without_output(self):
        with self.assertRaises(ValueError): product.bundle(self.root, self.plan, "a" * 64)
        self.assertFalse((self.root / "artifacts/next-release/candidate-public-test").exists())

    def test_archives_are_exact_and_reproducible(self):
        result = product.bundle(self.root, self.plan, "a" * 64)
        folder = self.root / "artifacts/next-release/candidate-public-test"
        with zipfile.ZipFile(folder / "reader-world-next-source.zip") as archive:
            self.assertEqual(set(archive.namelist()), {row["archivePath"] for row in self.plan["files"]})
            for row in self.plan["files"]:
                self.assertEqual(product.sha(archive.read(row["archivePath"])), row["sha256"])
            self.assertTrue(archive.getinfo("scripts/start.sh").external_attr >> 16 & 0o111)
        with zipfile.ZipFile(folder / "reader-world-next-web.zip") as archive:
            self.assertEqual(archive.namelist(), ["index.html"])
        second = copy.deepcopy(self.plan); second["buildId"] = "candidate-public-second"
        result2 = product.bundle(self.root, second, "b" * 64)
        self.assertEqual([row["sha256"] for row in result["artifacts"]], [row["sha256"] for row in result2["artifacts"]])
        self.assertFalse(result["completeAcceptance"])
        self.assertEqual(result["providerCalls"], 0)

    def test_tamper_is_rejected_before_output(self):
        (self.root / self.plan["files"][0]["file"]).write_bytes(b"changed")
        self.reject_without_output()

    def test_duplicate_and_windows_case_collision_are_rejected(self):
        duplicate = copy.deepcopy(next(row for row in self.plan["files"] if row["archivePath"] == "client/Dockerfile"))
        duplicate["archivePath"] = "client/dockerfile"; self.plan["files"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "Duplicate archive input"):
            product.bundle(self.root, self.plan, "a" * 64)
        self.assertFalse((self.root / "artifacts").exists())

    def test_missing_deployment_file_is_rejected(self):
        self.plan["files"] = [row for row in self.plan["files"] if row["archivePath"] != "server/app/main.py"]
        self.reject_without_output()

    def test_incomplete_static_build_is_rejected_before_zip(self):
        self.plan["files"] = [row for row in self.plan["files"] if row["archivePath"] != "client/dist/index.html"]
        self.add("client/dist/app.js", b"public"); self.reject_without_output()

    def test_private_paths_and_archive_traversal_are_rejected_without_reading(self):
        for name in ["../README.md", "/README.md", "C:/README.md", "client\\README.md", ".", "docs/.env", "docs/.Env", "server/local.properties", "server/data/a.txt", "client/sign.p12", "docs/private.srbkup"]:
            with self.subTest(path=name), self.assertRaises(ValueError): product.archive_name(name)
        for name in ["frozen/.env", "frozen/.Env.local", "frozen/data/account.txt", "frozen/signer.key"]:
            with self.subTest(path=name), patch.object(Path, "read_bytes", side_effect=AssertionError("private bytes read")), self.assertRaises(ValueError):
                product.checked_file(self.root, name, "a" * 64)

    def test_symlink_parent_is_rejected_without_reading(self):
        target = self.root / "own-private-fixture"; target.mkdir(); (target / "public.txt").write_bytes(b"synthetic-only")
        (self.root / "apparently-public").symlink_to(target, target_is_directory=True)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("linked bytes read")), self.assertRaises(ValueError):
            product.checked_file(self.root, "apparently-public/public.txt", product.sha(b"synthetic-only"))

    def test_credential_shape_is_rejected_without_echoing_value(self):
        synthetic = b"oc_sk_" + b"SYNTHETIC" * 5
        self.add("docs/public.txt", synthetic)
        with self.assertRaisesRegex(ValueError, "Credential-shaped bytes") as caught:
            product.bundle(self.root, self.plan, "a" * 64)
        self.assertNotIn(synthetic.decode(), str(caught.exception))
        self.assertFalse((self.root / "artifacts").exists())

    def test_evidence_hash_is_bound_without_inflating_acceptance(self):
        file = "frozen/evidence.json"; raw = json.dumps({"cases": 771, "kind": "mock-only"}).encode()
        (self.root / file).write_bytes(raw)
        self.plan["evidence"] = [{"file": file, "sha256": product.sha(raw), "kind": "mock-runtime-receipt"}]
        result = product.bundle(self.root, self.plan, "a" * 64)
        self.assertEqual(result["evidenceReferences"][0]["sha256"], product.sha(raw))
        self.assertFalse(result["completeAcceptance"])

    def test_existing_candidate_cannot_be_overwritten(self):
        product.bundle(self.root, self.plan, "a" * 64)
        with self.assertRaisesRegex(ValueError, "output exists"):
            product.bundle(self.root, self.plan, "a" * 64)


if __name__ == "__main__":
    unittest.main()
