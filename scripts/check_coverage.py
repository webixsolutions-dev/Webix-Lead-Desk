"""Enforce per-module coverage once the relevant modules contain executable code."""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    """Require at least 80 percent coverage for jobs and any implemented mail modules."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = ET.parse(Path("coverage.xml")).getroot()
    for module in ("jobs", "sending", "inbox"):
        lines = [
            line
            for item in report.findall(".//class")
            if item.attrib["filename"].startswith(f"{module}/")
            for line in item.findall("./lines/line")
        ]
        if not lines:
            if module == "jobs":
                logging.error("jobs coverage is missing")
                return 1
            logging.info("%s is not implemented in this phase", module)
            continue
        ratio = sum(int(line.attrib["hits"]) > 0 for line in lines) / len(lines)
        logging.info("%s coverage: %.1f%%", module, ratio * 100)
        if ratio < 0.8:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
