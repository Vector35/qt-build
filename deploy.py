# Copy built Qt artifacts into worker node home for use in the binaryninja-dev projects.
#
# Generally speaking the rational is thus:
# - This script is not run automatically, so we can update Qt only when the test builds
#   are successful.
# - Dev builds will always want to use the latest compiled version of Qt
# - We already have a central Qt installation that all the builds pull from
# - Trying to use the same Groovy/Bash scripts from binaryninja-test for binarynina-dev
#   will need to be replicated across 6 different jobs, and the scripts are pretty brittle
#   and could easily break 6x, resulting in a mess to fix. That's acceptable for the
#   binaryninja-test jobs, but not dev.
# - binaryninja-test jobs will not be affected by this, as they will just pull Qt
#   via the aforementioned janky scripts.

import os
import platform
import shutil
import sys
import zipfile
from pathlib import Path

if "QT_INSTALL_DIR" in os.environ:
	user_qt_parent_path = Path(os.environ["QT_INSTALL_DIR"])
else:
	home_path = None
	if platform.system() == 'Windows':
		home_path = "{0}{1}".format(os.environ['HOMEDRIVE'], os.environ['HOMEPATH'])
	else:
		home_path = os.environ['HOME']

	user_qt_parent_path = Path(home_path) / 'Qt'

artifacts_path = Path(os.path.dirname(os.path.abspath(__file__))) / Path('artifacts')

qt_zip_path = None
for f in artifacts_path.glob('qt*.zip'):
	qt_zip_path = f
	break

tmp_extract_path = Path(os.path.dirname(os.path.abspath(__file__))) / Path('extract')

if tmp_extract_path.exists():
	print(f'Cleaning up old extract path at {tmp_extract_path}...')
	shutil.rmtree(tmp_extract_path)

print(f'Extracting {qt_zip_path} to {tmp_extract_path}...')

ZIP_UNIX_SYSTEM = 3

symlinks = []
with zipfile.ZipFile(qt_zip_path, 'r') as z:
	# https://stackoverflow.com/a/46837272
	for info in z.infolist():
		if info.create_system == ZIP_UNIX_SYSTEM:
			unix_attributes = info.external_attr >> 16
			if unix_attributes & 0o120000 == 0o120000:
				target = z.read(info)
				symlinks.append((target, os.path.join(tmp_extract_path, info.filename)))
				continue

		extracted_path = z.extract(info, tmp_extract_path)

		if info.create_system == ZIP_UNIX_SYSTEM:
			unix_attributes = info.external_attr >> 16
			if unix_attributes:
				os.chmod(extracted_path, unix_attributes)

for symlink in symlinks:
	os.symlink(symlink[0], symlink[1])

# Determine version from the directory within the Qt expansion
Qt_extract_path = None
for f in (tmp_extract_path / 'Qt').glob('*'):
	qt_extract_path = f
	break

if qt_extract_path is None:
	print("Failed to find Qt path")
	sys.exit(1)

print(f'Found Qt version: {qt_extract_path.name}')

user_qt_path = user_qt_parent_path / qt_extract_path.name
user_qt_old_path = user_qt_parent_path / (qt_extract_path.name + '-old')

# Cleanup backup install
if user_qt_old_path.exists():
	print(f'Removing backup install at {user_qt_old_path}')
	shutil.rmtree(user_qt_old_path)

# Check for user Qt of the same version
if user_qt_path.exists():
	print(f'Overwriting existing Qt at {user_qt_path} with {qt_extract_path}')
else:
	print(f'Installing new Qt at {user_qt_path} with {qt_extract_path}')

# Just in case
if user_qt_path.exists():
	print(f'Moving {user_qt_path} to {user_qt_old_path} just in case')
	os.makedirs(user_qt_old_path.parent, exist_ok=True)
	user_qt_path.rename(user_qt_old_path)

os.makedirs(user_qt_path.parent, exist_ok=True)
qt_extract_path.rename(user_qt_path)
