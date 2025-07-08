#!/usr/bin/env python3
import sys
import os
import subprocess
import shutil
import glob
import zipfile
import argparse
import platform

from math import ceil
from pathlib import Path

from target_qt6_version import qt_version, qt_modules


def apply_patch(path, qt_source_path):
	# On some Windows machines, git apply breaks. On others, patch breaks. Just try both, because
	# Windows environments are so hard to predict we can't rely on anything to be sane.
	if subprocess.call(["git", "apply", os.path.abspath(path)], cwd=qt_source_path) != 0:
		if subprocess.call(["patch", "-p1", "-i", os.path.abspath(path)], cwd=qt_source_path) != 0:
			print("Failed to patch source")
			sys.exit(1)


parser = argparse.ArgumentParser(description = "Create bundle for Qt 6 source code")
parser.add_argument("--skip-clone", help="skip cloning the Qt 6 source code", action="store_true")
parser.add_argument("--skip-clean", help="skip removing the Qt 6 source code", action="store_true")
parser.add_argument("--mirror", help="use source mirror", action="store")
args = parser.parse_args()

base_dir = Path(__file__).resolve().parent
qt_dir = base_dir / "build"
source_path = qt_dir / "src"
qt_source_path = source_path / f"qt{qt_version}"
artifact_path = base_dir / "artifacts"
qt_patches_path = base_dir / 'qt_patches'
pyside_patches_path = base_dir / 'pyside_patches'
pyside_source_path = source_path / f"pyside{qt_version}"

qt_patches = []
for patch in sorted(qt_patches_path.iterdir()):
	if patch.suffix == '.patch':
		resolved_path = patch.resolve()
		qt_patches.append(patch.resolve())

pyside_patches = []
for patch in sorted(pyside_patches_path.iterdir()):
	if patch.suffix == '.patch':
		resolved_path = patch.resolve()
		pyside_patches.append(patch.resolve())

mirror = []
if args.mirror:
	print(f"Using source mirror: {args.mirror}")
	mirror = ["--mirror", args.mirror]

if sys.version_info.major < 3:
	print('Please use Python 3')
	sys.exit(1)
if sys.platform == 'win32':
	print('Please use Linux or Mac')
	sys.exit(1)

if not os.path.exists(artifact_path):
	os.mkdir(artifact_path)
if os.path.exists(source_path):
	shutil.rmtree(source_path)
os.mkdir(source_path)
os.mkdir(qt_source_path)

print("\nActive Qt version is " + qt_version)

for patch in qt_patches:
	print(f"Apply Qt patch: {patch}")
for patch in pyside_patches:
	print(f"Apply PySide patch: {patch}")

if input("\nIs this correct (y/n)? ") != "y":
	print("Aborted")
	sys.exit(1)

if not args.skip_clone:
	print("\nCloning Qt...")
	if args.mirror:
		if subprocess.call(["git", "clone", f"{args.mirror}qt5.git", qt_source_path]) != 0:
			print("Failed to clone Qt git repository")
			sys.exit(1)
	else:
		if subprocess.call(["git", "clone", "https://codereview.qt-project.org/qt/qt5.git", qt_source_path]) != 0:
			print("Failed to clone Qt git repository")
			sys.exit(1)
	if subprocess.call(["git", "checkout", f"v{qt_version}"], cwd=qt_source_path) != 0:
		print("Failed to check out branch '{}'".format(qt_version))
		sys.exit(1)

	if subprocess.call(["./init-repository", "--module-subset=" + ",".join(qt_modules), "--no-update"] + mirror, cwd=qt_source_path) != 0:
		print("Failed to initialize submodules")
		sys.exit(1)

	# Check out submodules, but don't check out recursively until we've had a chance to patch
	# module paths
	if subprocess.call(["git", "submodule", "update", "--init"] + qt_modules, cwd=qt_source_path) != 0:
		print("Failed to check out submodules")
		sys.exit(1)

	if args.mirror:
		# Fix qttools .gitmodules to use mirror
		open(os.path.join(qt_source_path, "qttools", ".gitmodules"), 'w').write(
			'[submodule "src/assistant/qlitehtml"]\n' +
			'    path = src/assistant/qlitehtml\n' +
			f'    url = {args.mirror}playground/qlitehtml.git'
		)
	else:
		# Fix qttools to use absolute path since the relative path fails on anything that isn't the
		# official repo, which is so slow and unreliable it fails many builds.
		open(os.path.join(qt_source_path, "qttools", ".gitmodules"), 'w').write(
			'[submodule "src/assistant/qlitehtml"]\n' +
			'    path = src/assistant/qlitehtml\n' +
			f'    url = https://code.qt.io/playground/qlitehtml.git'
		)

	# Check out all submodules
	if subprocess.call(["git", "submodule", "update", "--init", "--recursive"] + qt_modules, cwd=qt_source_path) != 0:
		print("Failed to check out submodules")
		sys.exit(1)

	qt_patch_contents = ""
	for patch in qt_patches:
		print(f"\nApplying patch {patch}...")
		apply_patch(patch, qt_source_path)
		qt_patch_contents += open(patch).read()

	print("\nCloning pyside-setup...")
	if args.mirror:
		if subprocess.call(["git", "clone", f"{args.mirror}pyside-setup", pyside_source_path]) != 0:
			print("Failed to clone PySide git repository")
			sys.exit(1)
	else:
		if subprocess.call(["git", "clone", "https://codereview.qt-project.org/pyside/pyside-setup", pyside_source_path]) != 0:
			print("Failed to clone PySide git repository")
			sys.exit(1)
	if subprocess.call(["git", "checkout", qt_version], cwd=pyside_source_path) != 0:
		print("Failed to check out branch '{}'".format(qt_version))
		sys.exit(1)

	pyside_patch_contents = ""
	for patch in pyside_patches:
		print(f"\nApplying patch {patch}...")
		apply_patch(patch, pyside_source_path)
		pyside_patch_contents += open(patch).read()

	open(os.path.join(artifact_path, f"qt{qt_version}.patch"), 'w').write(qt_patch_contents)
	open(os.path.join(artifact_path, f"pyside{qt_version}.patch"), 'w').write(pyside_patch_contents)

	print("Removing .git directories...")
	if subprocess.call("find . -name .git | xargs rm -rf", shell=True, cwd=qt_source_path) != 0:
		print("Failed to remove .git directories")
		sys.exit(1)
	if subprocess.call("find . -name .gitmodules | xargs rm -rf", shell=True, cwd=qt_source_path) != 0:
		print("Failed to remove .gitmodules")
		sys.exit(1)
	if subprocess.call("find . -name .git | xargs rm -rf", shell=True, cwd=pyside_source_path) != 0:
		print("Failed to remove .git directories")
		sys.exit(1)
	if subprocess.call("find . -name .gitmodules | xargs rm -rf", shell=True, cwd=pyside_source_path) != 0:
		print("Failed to remove .gitmodules")
		sys.exit(1)

print("Compressing...")
if subprocess.call(["tar", "cJf", os.path.join(artifact_path, f"qt{qt_version}.tar.xz"), f"qt{qt_version}", f"pyside{qt_version}"], cwd=source_path) != 0:
	print("Failed to compress source")
	sys.exit(1)

if not args.skip_clean:
	print("Cleaning up...")
	shutil.rmtree(source_path)
