import sys
import os
from site import check_enableusersite
from pathlib import Path

# Handle both normal environments and virtualenvs
try:
	from site import getusersitepackages, getsitepackages
except ImportError:
	from sysconfig import get_path
	getsitepackages = lambda: get_path('purelib')
	getusersitepackages = getsitepackages

base_dir = Path(__file__).resolve().parent
target_path = base_dir / "pyside" / "site-packages"

if (os.path.isdir(target_path)):
	print("Found install folder of {}".format(target_path))
else:
	print("Failed to find pyside installation expected at {}".format(target_path))
	sys.exit(1)

if check_enableusersite():
	install_path = getusersitepackages()
	if not os.path.exists(install_path):
		os.makedirs(install_path)
else:
	print("Warning, trying to write to user site packages, but check_enableusersite fails.")
	sys.exit(1)

pyside_pth_path = os.path.join(install_path, f'pyside6.pth')
with open(pyside_pth_path, 'wb') as pth_file:
	pth_file.write((str(target_path) + "\n").encode('charmap'))

print("PySide installed using {}".format(pyside_pth_path))
