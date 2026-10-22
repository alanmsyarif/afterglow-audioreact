"""Packaging checks that run without Blender: python tools/test_build_editions.py."""

from pathlib import Path
import tempfile
import tomllib
import unittest
import zipfile

from build_editions import FREE_EXCLUDES, ROOT, build


class EditionPackages(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "afterglow"
        self.source.mkdir()
        (self.root / "LICENSE").write_text("GPL-3.0-or-later\n")
        (self.source / "blender_manifest.toml").write_text(
            'id = "afterglow"\nversion = "1.2.3"\nname = "Afterglow"\n'
            'tagline = "Original"\nlicense = ["SPDX:GPL-3.0-or-later"]\n'
        )
        (self.source / "edition.py").write_text(
            'IS_PRO = True\nNAME = "Afterglow Pro"\n\ndef allowed():\n    return IS_PRO\n'
        )
        (self.source / "__init__.py").write_text("# addon entry point\n")
        (self.source / "core.py").write_text("# object reaction\n")
        for name in FREE_EXCLUDES:
            (self.source / name).write_text("# environment code\n")
        cache = self.source / "__pycache__"
        cache.mkdir()
        (cache / "core.pyc").write_bytes(b"cache")
        (self.source / ".local-secret").write_text("ignored")

    def contents(self, edition):
        path = build(edition, self.root / "output", self.source)
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            return path, {name: archive.read(name) for name in archive.namelist()}

    def test_free_excludes_generators_and_keeps_core(self):
        path, files = self.contents("free")
        self.assertEqual(path.name, "afterglow-free-1.2.3.zip")
        self.assertFalse(set(files) & FREE_EXCLUDES)
        self.assertIn("core.py", files)
        self.assertIn("__init__.py", files)
        self.assertIn("LICENSE", files)
        self.assertFalse(any("__pycache__" in name or name.startswith(".") for name in files))
        metadata = tomllib.loads(files["blender_manifest.toml"].decode())
        self.assertEqual(metadata["id"], "afterglow")
        self.assertEqual(metadata["name"], "Afterglow Free")
        namespace = {}
        exec(files["edition.py"], namespace)
        self.assertFalse(namespace["IS_PRO"])
        self.assertEqual(namespace["NAME"], metadata["name"])
        self.assertFalse(namespace["allowed"]())

    def test_pro_contains_free_and_environment(self):
        path, files = self.contents("pro")
        self.assertEqual(path.name, "afterglow-pro-1.2.3.zip")
        self.assertTrue(FREE_EXCLUDES.issubset(files))
        self.assertIn("core.py", files)
        metadata = tomllib.loads(files["blender_manifest.toml"].decode())
        self.assertEqual(metadata["id"], "afterglow")
        self.assertEqual(metadata["name"], "Afterglow Pro")
        namespace = {}
        exec(files["edition.py"], namespace)
        self.assertTrue(namespace["IS_PRO"])

    def test_build_is_reproducible_and_does_not_change_source(self):
        source_before = {p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        first = build("free", self.root / "first", self.source)
        second = build("free", self.root / "second", self.source)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(source_before, {p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()})

    def test_pro_cannot_be_built_in_public_dist(self):
        with self.assertRaisesRegex(ValueError, "public dist"):
            build("pro", ROOT / "dist", self.source)
        with self.assertRaisesRegex(ValueError, "public dist"):
            build("pro", ROOT / "dist" / "nested", self.source)


if __name__ == "__main__":
    unittest.main()
