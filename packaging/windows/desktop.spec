# Build on Windows with the locked packaging dependencies.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

root = Path(SPECPATH).parent.parent
hidden = collect_submodules("app", filter=lambda name: name != "app.vercel")
data = collect_data_files("app", include_py_files=True)
binaries = []
for package in ("psycopg", "psycopg_binary", "alembic", "tzdata"):
    package_data, package_binaries, package_hidden = collect_all(package)
    data += package_data
    binaries += package_binaries
    hidden += package_hidden
analysis = Analysis(
    [str(root / "packaging/windows/launcher.py")], pathex=[str(root)],
    binaries=binaries, datas=data, hiddenimports=hidden,
    excludes=["app.vercel", "pytest", "mypy", "ruff"], noarchive=False,
)
archive = PYZ(analysis.pure)
exe = EXE(archive, analysis.scripts, [], exclude_binaries=True,
          name="WebixLeadDesk", debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, icon=str(root / "packaging/windows/webix.ico"))
bundle = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="WebixLeadDesk")
