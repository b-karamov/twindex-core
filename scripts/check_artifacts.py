"""Verify publishable artifacts contain exact license text and all source modules."""

import tarfile
from pathlib import Path
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]
license_text = (root / "LICENSE").read_bytes()
for package, module in (
    ("twindex-core", "twindex_core"),
    ("twindex-cli", "twindex_cli"),
):
    wheels = list((root / "dist").glob(f"{module}-*.whl"))
    sdists = list((root / "dist").glob(f"{package}-*.tar.gz")) + list(
        (root / "dist").glob(f"{module}-*.tar.gz")
    )
    assert len(wheels) == len(sdists) == 1, (wheels, sdists)
    expected = {
        f"{module}/{p.name}"
        for p in (root / "packages" / package / "src" / module).glob("*.py")
    }
    with ZipFile(wheels[0]) as z:
        names = z.namelist()
        assert {
            n for n in names if n.startswith(module + "/") and n.endswith(".py")
        } == expected
        licenses = [n for n in names if n.endswith("/licenses/LICENSE")]
        assert len(licenses) == 1 and z.read(licenses[0]) == license_text
        assert any(n.endswith("/licenses/NOTICE") for n in names)
        metadata = z.read(
            next(n for n in names if n.endswith(".dist-info/METADATA"))
        ).decode()
        assert "License-Expression: Apache-2.0" in metadata
        assert "Version: 0.1.0a1" in metadata
    with tarfile.open(sdists[0]) as t:
        entry = next(n for n in t.getnames() if n.endswith("/LICENSE"))
        assert t.extractfile(entry).read() == license_text
        assert any(n.endswith("/NOTICE") for n in t.getnames())
    print(f"Verified {package}: modules, SPDX, exact LICENSE and NOTICE in wheel/sdist")
