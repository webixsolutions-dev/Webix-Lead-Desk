"""PyInstaller launcher; never import the Vercel entrypoint."""

from app.desktop.main import main

if __name__ == "__main__":
    raise SystemExit(main())
