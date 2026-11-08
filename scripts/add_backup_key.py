"""Add a separate backup key to an existing private environment file without displaying it."""

import argparse
import logging
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet


def add_key(path: Path) -> bool:
    """Preserve existing values and atomically fill only a missing or empty BACKUP_KEY."""
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    matches = [i for i, line in enumerate(lines) if line.strip().startswith("BACKUP_KEY=")]
    if len(matches) > 1:
        raise ValueError("Remove duplicate BACKUP_KEY fields first")
    if matches and lines[matches[0]].split("=", 1)[1].strip():
        return False
    value = "BACKUP_KEY=" + Fernet.generate_key().decode()
    if matches:
        lines[matches[0]] = value
    else:
        lines.append(value)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".env-backup-", delete=False
        ) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        if path.read_text(encoding="utf-8") != original:
            raise ValueError("Environment file changed; retry after other edits finish")
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return True


def main() -> None:
    """Write only to the explicitly selected private file and log no key material."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        changed = add_key(args.env_file)
        logging.info("Backup key added privately." if changed else "Existing backup key preserved.")
    except (ValueError, OSError):
        logging.error("Could not update the private environment file; check its path and fields.")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
