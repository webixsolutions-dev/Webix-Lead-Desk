"""Create icon resources and build a bundled Windows executable from the locked environment."""

import subprocess
import sys
from pathlib import Path

from PIL import Image


def main() -> None:
    """Reuse the official logo and call PyInstaller without downloading runtime packages."""
    root = Path(__file__).resolve().parents[1]
    with Image.open(root / "app/ui/static/webix-logo.png") as image:
        # Preserve the logo; only pad and resize for Windows' square icon container.
        image = image.convert("RGBA")
        image.thumbnail((240, 240))
        canvas = Image.new("RGBA", (256, 256), (255, 255, 255, 255))
        canvas.alpha_composite(image, ((256 - image.width) // 2, (256 - image.height) // 2))
        canvas.save(root / "packaging/windows/webix.ico", sizes=[(32, 32), (48, 48), (256, 256)])
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "packaging/windows/desktop.spec",
        ],
        cwd=root,
        check=True,
    )


if __name__ == "__main__":
    main()
