import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from core.errors import CliError
from sources.mirror import _extract_skill


class MirrorArchiveSecurityTest(unittest.TestCase):
    def test_rejects_absolute_member_after_skill_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tarball = root / "bad.tar.gz"
            escaped = root / "escaped.txt"
            payload = b"bad"
            with tarfile.open(tarball, "w:gz") as tar:
                info = tarfile.TarInfo(f"top/demo/{escaped.as_posix()}")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            with self.assertRaises(CliError):
                _extract_skill(tarball, "demo", root / "dest")
            self.assertFalse(escaped.exists())


if __name__ == "__main__":
    unittest.main()
