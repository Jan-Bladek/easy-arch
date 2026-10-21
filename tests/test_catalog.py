import io
import tarfile

import pytest

from arch_gui_installer.packages.catalog import Catalog


def test_official_repos_only(tmp_path):
    for repo in ["core", "omarchy"]:
        with tarfile.open(tmp_path / f"{repo}.db", "w:gz") as t:
            name = "base" if repo == "core" else "private"
            data = f"%NAME%\n{name}\n\n%VERSION%\n1.0\n\n%DESC%\nA package\n\n%GROUPS%\nexample\n\n%ISIZE%\n10\n".encode()
            info = tarfile.TarInfo(f"{name}-1/desc")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    catalog = Catalog(tmp_path)
    assert catalog.expand(["example"]) == ["base"]
    assert catalog.search("package")[0].version == "1.0"
    with pytest.raises(ValueError):
        catalog.expand(["private"])
    with pytest.raises(ValueError):
        catalog.expand(["does-not-exist"])


def test_iso_catalog_survives_archiso_sync_cleanup(tmp_path, monkeypatch):
    from arch_gui_installer.packages import catalog as module

    system = tmp_path / "cleaned-sync"
    bundle = tmp_path / "bundled"
    bundle.mkdir()
    for repository in ["core", "extra"]:
        with tarfile.open(bundle / f"{repository}.db", "w:gz"):
            pass
    monkeypatch.setattr(module, "SYSTEM_DATABASES", system)
    monkeypatch.setattr(module, "BUNDLED_DATABASES", bundle)
    assert module.Catalog().directory == bundle
