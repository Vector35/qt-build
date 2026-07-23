#!/usr/bin/env python3
"""Upload Qt/PySide debug symbols to Sentry."""

import subprocess
import sys
from pathlib import Path

SENTRY_ORG = "vector35"
SENTRY_PROJECT = "binaryninja"

ARTIFACTS_DIR = Path("artifacts")


def find_uploads() -> list[Path]:
	uploads = sorted(ARTIFACTS_DIR.glob("qt_symbols_*.zip"))
	if sys.platform == "win32":
		# The stripped-into-PDB approach loses the unwind info on Windows; the PE
		# binaries in the main archive carry the unwind tables and code IDs.
		uploads += sorted(
			p for p in ARTIFACTS_DIR.glob("qt_*.zip")
			if not p.name.startswith("qt_symbols_")
		)
	return uploads


def main() -> int:
	uploads = find_uploads()
	if not uploads:
		print(f"No debug symbol archives found in {ARTIFACTS_DIR}/", file=sys.stderr)
		return 1

	print("Uploading to Sentry:")
	for path in uploads:
		print(f"  {path}")

	cmd = [
		"sentry-cli", "debug-files", "upload",
		"--org", SENTRY_ORG,
		"--project", SENTRY_PROJECT,
	] + [str(path) for path in uploads]

	try:
		return subprocess.call(cmd)
	except FileNotFoundError:
		print("sentry-cli not found on PATH.", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())
