"""Security-boundary tests for the public source exporter, using invented data."""

import tempfile
import unittest
import struct
import zlib
from pathlib import Path

import public_source as public


class PublicSourceTests(unittest.TestCase):
    def test_private_directory_is_reported_without_opening_its_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "private").symlink_to(root / "missing-private-target", target_is_directory=True)
            names = public.candidate_names(root, tree=True)
            _, findings = public.inspect(root, names)
            self.assertTrue(any(f["path"] == "private" for f in findings))

    def test_secret_is_blocked_and_value_is_absent_from_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = "gh" + "p_" + "A" * 36
            (root / "README.md").write_text(token)
            _, findings = public.inspect(root, ["README.md"])
            self.assertTrue(any(f["rule"] == "provider-token" for f in findings))
            self.assertNotIn(token, str(findings))

    def test_source_symlink_cannot_escape_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            (root / "README.md").symlink_to(Path(tmp) / "unread-private-file")
            manifest, findings = public.inspect(root, ["README.md"])
            self.assertFalse(manifest)
            self.assertTrue(any(f["rule"] == "symlink" for f in findings))

    def test_export_refuses_changed_bytes_and_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "source", Path(tmp) / "export"
            root.mkdir()
            source = root / "README.md"
            source.write_text("Reviewed synthetic text.")
            manifest, _ = public.inspect(root, ["README.md"])
            source.write_text("Changed after review.")
            with self.assertRaises(ValueError):
                public.export(root, out, manifest)
            self.assertFalse(out.exists())
            out.mkdir()
            with self.assertRaises(ValueError):
                public.export(root, out, [])

    def test_symlink_parent_is_blocked_before_reading_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            other = Path(tmp) / "outside"
            root.mkdir()
            other.mkdir()
            (other / "README.md").write_text("Outside the release boundary.")
            (root / "engine").symlink_to(other, target_is_directory=True)
            manifest, findings = public.inspect(root, ["engine/README.md"])
            self.assertFalse(manifest)
            self.assertTrue(any(f["rule"] == "symlink-parent" for f in findings))

    def test_export_contains_only_reviewed_files_without_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, out = Path(tmp) / "source", Path(tmp) / "export"
            root.mkdir()
            (root / "README.md").write_text("Reviewed synthetic text.")
            (root / ".git").mkdir()
            manifest, _ = public.inspect(root, ["README.md"])
            public.export(root, out, manifest)
            self.assertEqual([p.name for p in out.iterdir()], ["README.md"])

    def test_unreviewed_documents_images_and_credentials_are_blocked(self):
        for name in ["engine/tests/real.docx", "examples/client.png", "docs/assets/not-reviewed.png", "engine/src/.env", "engine/src/private/profile.json"]:
            with self.subTest(name=name):
                self.assertIsNotNone(public.path_problem(name))
        self.assertIsNone(public.path_problem("docs/assets/app-ready.png"))

    def test_image_metadata_and_trailing_bytes_block_export(self):
        def chunk(kind, payload):
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
        header = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        image = chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        end = chunk(b"IEND", b"")
        self.assertIsNone(public.png_problem(header + image + end))
        self.assertEqual(public.png_problem(header + chunk(b"eXIf", b"Synthetic metadata") + image + end), "unreviewed-image-metadata")
        self.assertEqual(public.png_problem(header + image + end + b"extra"), "trailing-image-data")

    def test_contact_checks_are_generic_and_never_report_the_address(self):
        address = "synthetic-person" + "@" + "not-a-fixture" + ".com"
        findings = public.email_findings(address.encode(), "README.md")
        self.assertEqual(findings[0]["rule"], "non-fixture-email")
        self.assertNotIn(address, str(findings))
        self.assertFalse(public.email_findings(b"person@example.com", "examples/README.md"))
        self.assertFalse(public.email_findings(b"icon_16x16@2x.png", "app/Assets/Contents.json"))


if __name__ == "__main__":
    unittest.main()
