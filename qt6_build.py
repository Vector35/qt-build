#!/usr/bin/env python3
import sys
import os
import subprocess
import shutil
import glob
import zipfile
import argparse
import platform
import datetime

from math import ceil
from pathlib import Path

from target_qt6_version import qt_version, llvm_version, msvc_build, msvc_dir_name, vs_version, min_macos, qt_modules, pyside_modules


build_opts = ["-no-static", "-release", "-opensource", "-confirm-license", "-nomake", "examples",
	"-nomake", "tests", "-no-feature-tuiotouch", "-qt-libpng", "-qt-libjpeg", "-qt-libb2", "-no-glib",
	"-qt-tiff", "-qt-webp", "-qt-pcre", "-no-feature-zstd", "-no-feature-brotli", "-no-feature-graphicseffect",
	"-no-feature-style-windowsvista", "-no-feature-style-windows11"]

if sys.platform == 'linux':
	build_opts += ["-xcb", "-xcb-xlib"]


def remove_dir(path):
	if sys.platform == 'win32':
		# Windows being Windows. Not doing this as a recursive delete from the shell will yield
		# "access denied" errors. Even deleting the individual files from the terminal does this.
		# Somehow, deleting this way works correctly.
		subprocess.call('rmdir /S /Q "' + str(path) + '"', shell=True)
	else:
		shutil.rmtree(path)


def keychain_unlocker():
	keychain_unlocker = os.environ["HOME"] + "/unlock-keychain"
	if os.path.exists(keychain_unlocker):
		return subprocess.call([keychain_unlocker]) == 0
	return True


def mac_sign(path):
	if not keychain_unlocker():
		return False

	args = ["codesign", "-f", "--options", "runtime", "--timestamp", "-s", "Developer ID"]
	if path.endswith(".dmg"):
		args.append(path)
	else:
		for f in glob.glob(path):
			args.append(f)
	return subprocess.call(args) == 0


def signWindowsFiles(path):
	timeServers = [r"http://timestamp.digicert.com", r"http://timestamp.comodoca.com/rfc3161"]
	signTool = r"c:\Program Files (x86)\Windows Kits\10\bin\10.0.18362.0\x64\signtool.exe"
	signingCert = os.path.expandvars(r"%USERPROFILE%\signingcerts\codesign.pfx")
	for timeServer in timeServers:
		proc = subprocess.run([signTool, "sign", "/fd", "sha256", "/f", signingCert, "/tr", timeServer, "/td", "sha256", path], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
		if proc.returncode == 0:
			print("Signed {}".format(path))
			return True
		else:
			print("Signing {} with timeserver: {} failed. Trying next server. {}".format(path, timeServer, proc.stdout.decode('charmap')))
	print("Failed to sign file %s" % path)
	return False


def apply_patch(path, qt_source_path):
	# On some Windows machines, git apply breaks. On others, patch breaks. Just try both, because
	# Windows environments are so hard to predict we can't rely on anything to be sane.
	if subprocess.call(["git", "apply", os.path.abspath(path)], cwd=qt_source_path) != 0:
		if subprocess.call(["patch", "-p1", "-i", os.path.abspath(path)], cwd=qt_source_path) != 0:
			print("Failed to patch source")
			sys.exit(1)


parser = argparse.ArgumentParser(description = "Build and install Qt 6", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--no-clone", help="skip cloning the Qt 6 source code", action="store_true")
parser.add_argument("--no-clean", dest='clean', action='store_false', default=True, help="skip removing the Qt 6 source code")
parser.add_argument("--no-prompt", dest='prompt', action='store_false', default=True, help="Don't wait for user prompt")
parser.add_argument("--no-install", dest='install', action='store_false', default=True, help="Don't install build products to your home folder")
parser.add_argument("--no-pyside", dest='pyside', action='store_false', default=True, help="Don't build PySide")
parser.add_argument("--patch", help="patch the source before building")
parser.add_argument("--asan", help="build with ASAN", action="store_true")
parser.add_argument("--tsan", help="build with TSAN", action="store_true")
parser.add_argument("--debug", help="build a debug configuration", action="store_true")
parser.add_argument("--universal", help="build for both x86_64 and arm64 (arm64 Mac host only)", action="store_true")
parser.add_argument("--mirror", help="use source mirror", action="store")
parser.add_argument("--sign", dest='sign', help="sign all executables", action="store_true")
parser.add_argument("--qt-source", help="use Qt source directory", action="store")
parser.add_argument("--pyside-source", help="use PySide source directory", action="store")
parser.add_argument("--build-dir", dest="build_dir", help="Custom build directory to bypass windows PATH_MAX limits", action="store")

if not sys.platform.startswith("win"):
	parser.add_argument("-j", "--jobs", dest='jobs', default=ceil(os.cpu_count()*1.1), help="Number of build threads (Defaults to 1.1*cpu_count)")

args = parser.parse_args()

if args.patch:
	args.patch = os.path.abspath(args.patch)

if args.asan:
	print("Building with ASAN")
	build_opts.remove("-release")
	build_opts += ["-debug", "-sanitize", "address"]
	args.pyside = False

if args.tsan:
	print("Building with TSAN")
	build_opts.remove("-release")
	build_opts += ["-debug", "-sanitize", "thread"]
	args.pyside = False

if args.debug:
	print("Building debug")
	build_opts.remove("-release")
	build_opts += ["-debug"]

mirror = []
if args.mirror:
	print(f"Using source mirror: {args.mirror}")
	mirror = ["--mirror", args.mirror]
else:
	mirror = ["--mirror", "https://github.com/qt/"]

if sys.version_info.major < 3:
	print('Please build Qt 6 with Python 3')
	exit(1)

if sys.platform.startswith("win"):
	make_cmd = "ninja"
	parallel = []
	cmake_generator_array = ["-G", "Ninja"]
	python3_cmd = "py"

	# Import vcvars from Visual Studio
	vcvars = subprocess.check_output(fR"""call "C:\Program Files\Microsoft Visual Studio\{vs_version}\Professional\VC\Auxiliary\Build\vcvars64.bat" -vcvars_ver={msvc_build} && set""", shell=True)
	for line in vcvars.split(b'\r\n'):
		line = line.strip()
		if b'=' not in line:
			continue
		parts = line.split(b'=')
		key = parts[0].decode()
		value = b'='.join(parts[1:]).decode()
		os.environ[key] = value
else:
	make_cmd = "ninja"
	parallel = ["-j", str(args.jobs)]
	cmake_generator_array = ["-G", "Ninja"]
	python3_cmd = sys.executable

if sys.platform == 'win32':
	os.environ["HOME"] = os.environ["HOMEDRIVE"] + os.environ["HOMEPATH"]

if "LLVM_INSTALL_DIR" in os.environ:
	llvm_dir = os.path.join(os.environ["LLVM_INSTALL_DIR"], llvm_version)
else:
	llvm_dir = os.path.join(os.environ["HOME"], "libclang", llvm_version)
if not os.path.exists(llvm_dir):
	print("libclang needs to be installed.")
	print(f'Set LLVM_INSTALL_DIR, or install to f{os.path.join(os.environ["HOME"], "libclang", llvm_version)}')
	sys.exit(1)
os.environ["LLVM_INSTALL_DIR"] = llvm_dir

base_dir = Path(__file__).resolve().parent
if args.build_dir is not None:
	qt_dir = Path(args.build_dir).resolve()
else:
	qt_dir = base_dir / "build"

source_path = qt_dir / "src"
qt_source_path = source_path / "qt"
build_path = source_path / "build"
artifact_path = base_dir / "artifacts"
if args.asan:
	qt_version_dir = qt_version + "-asan"
elif args.tsan:
	qt_version_dir = qt_version + "-tsan"
else:
	qt_version_dir = qt_version
if sys.platform == 'win32':
	compiler = msvc_dir_name
elif sys.platform == 'darwin':
	compiler = "clang_64"
else:
	compiler = "gcc_64"
install_path = qt_dir / "install" / "Qt" / qt_version_dir / compiler
qt_patches_path = base_dir / 'qt_patches'
pyside_patches_path = base_dir / 'pyside_patches'
if sys.platform == 'win32':
	qtpaths = install_path / 'bin' / 'qtpaths.exe'
else:
	qtpaths = install_path / 'bin' / 'qtpaths'
pyside_source_path = source_path / "pyside-setup"
pyside_build_path = source_path / "pyside-build"
pyside_install_path = install_path / "pyside"
bundle_path = install_path / "bundle"



print(f"Target Qt version is         {qt_version}")
print(f"Build path will be           {build_path}")
print(f"Build products path will be  {install_path}")

if args.install:
	# From deploy.py
	if "QT_INSTALL_DIR" in os.environ:
		user_qt_parent_path = Path(os.environ["QT_INSTALL_DIR"]) / qt_version_dir
	else:
		user_qt_parent_path = Path.home() / 'Qt' / qt_version_dir
	print(f"Install path will be         {user_qt_parent_path}")


print(f"LLVM path is                 {llvm_dir}")
print(f"Clean build directory:       {'YES' if args.clean else 'NO'}")
print(f"Universal build:             {'YES' if args.universal else 'NO'}")
print(f"Install to home directory:   {'YES' if args.install else 'NO'}")
print(f"Codesigning:                 {'YES' if args.sign else 'NO'}")
print(f"PySide:                      {'YES' if args.pyside else 'NO'}")
print("")


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

if args.qt_source:
	print(f"Use existing Qt source directory at {args.qt_source}")
else:
	for patch in qt_patches:
		print(f"Apply Qt patch: {patch}")

if args.pyside:
	if args.pyside_source:
		print(f"Use existing PySide source directory at {args.pyside_source}")
	else:
		for patch in pyside_patches:
			print(f"Apply PySide patch: {patch}")

if sys.platform.startswith("win") and len(str(qt_dir)) > 30:
	# I cannot believe this is a real issue and yet there went 30 minutes of my life
	print()
	print("\u26a0\ufe0f Your build directory is too long and Windows will probably give you weird errors about files not being found")
	print("\u26a0\ufe0f You can try building anyway, though! Godspeed!")

if args.prompt and input("\nIs this correct (y/n)? ") != "y":
	print("Aborted")
	sys.exit(1)


if not artifact_path.exists():
	artifact_path.mkdir(parents=True)


if args.clean:
	# Clean existing files
	for f in artifact_path.glob('*'):
		f.unlink()

	if build_path.exists():
		remove_dir(build_path)

	if (base_dir / "CMakeCache.txt").exists():
		(base_dir / "CMakeCache.txt").unlink()


if args.install and user_qt_parent_path.exists():
	if args.prompt and input("\nAn install already exists at the target location. Overwrite? ") != "y":
		print("Aborted")
		sys.exit(1)


if not args.no_clone:
	if os.path.exists(source_path):
		remove_dir(source_path)

	if args.qt_source:
		print("\nCopying existing Qt source...")
		shutil.copytree(args.qt_source, qt_source_path)
	else:
		print("\nCloning Qt...")
		if args.mirror:
			if subprocess.call(["git", "clone", f"{args.mirror}qt5.git", qt_source_path]) != 0:
				print("Failed to clone Qt git repository")
				sys.exit(1)
		else:
			if subprocess.call(["git", "clone", "https://github.com/qt/qt5.git", qt_source_path]) != 0:
				print("Failed to clone Qt git repository")
				sys.exit(1)
		if subprocess.call(["git", "checkout", qt_version], cwd=qt_source_path) != 0:
			print("Failed to check out branch/tag '{}'".format(qt_version))
			sys.exit(1)

		init_repo_options = ["--module-subset=" + ",".join(qt_modules), "--no-update"]
		if sys.platform == 'win32':
			if subprocess.call(["perl", os.path.join(qt_source_path, "init-repository.pl")] + init_repo_options + mirror, cwd=qt_source_path) != 0:
				print("Failed to initialize submodules")
				sys.exit(1)
		else:
			if subprocess.call([os.path.join(qt_source_path, "init-repository")] + init_repo_options + mirror, cwd=qt_source_path) != 0:
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
				f'    url = {args.mirror}qlitehtml.git'
			)

		# Check out all submodules
		if subprocess.call(["git", "submodule", "update", "--init", "--recursive"] + qt_modules, cwd=qt_source_path) != 0:
			print("Failed to check out submodules")
			sys.exit(1)

		for patch in qt_patches:
			print(f"\nApplying patch {patch}...")
			apply_patch(patch, qt_source_path)

		if args.patch:
			print("\nApplying user provided patch...")
			apply_patch(args.patch, qt_source_path)

	if sys.platform == 'linux':
		print("Cloning libicu")
		if args.mirror:
			if subprocess.call(["git", "clone", f"{args.mirror}icu.git",
				os.path.join(qt_source_path, "icu")]) != 0:
				print("Failed to clone Qt git repository")
				sys.exit(1)
		else:
			if subprocess.call(["git", "clone", "https://github.com/unicode-org/icu.git",
				os.path.join(qt_source_path, "icu")]) != 0:
				print("Failed to clone Qt git repository")
				sys.exit(1)
		icu_version = "release-68-2"
		if subprocess.call(["git", "checkout", icu_version], cwd=os.path.join(qt_source_path, "icu")) != 0:
			print("Failed to check out branch '{}'".format(icu_version))
			sys.exit(1)

	if args.pyside:
		if args.pyside_source:
			print("\nCopying existing PySide source...")
			shutil.copytree(args.pyside_source, pyside_source_path)
		else:
			print("\nCloning pyside-setup...")
			if args.mirror:
				if subprocess.call(["git", "clone", "-b", qt_version, "--depth", "1",
									f"{args.mirror}pyside-setup", pyside_source_path]) != 0:
					print("Failed to clone PySide git repository")
					sys.exit(1)
			else:
				if subprocess.call(["git", "clone", "-b", qt_version, "--depth", "1",
									"https://codereview.qt-project.org/pyside/pyside-setup", pyside_source_path]) != 0:
					print("Failed to clone PySide git repository")
					sys.exit(1)

			for patch in pyside_patches:
				print(f"\nApplying patch {patch}...")
				apply_patch(patch, pyside_source_path)

if os.path.exists(build_path):
	remove_dir(build_path)

if sys.platform == 'darwin':
	build_opts += ['-qt-freetype']
	os.mkdir(build_path)

	if platform.processor() != 'arm' or args.universal:
		print("\nConfiguring Qt for x86_64...")
		os.mkdir(os.path.join(build_path, "x86_64"))
		os.environ["CMAKE_OSX_ARCHITECTURES"] = "x86_64"
		if subprocess.call([os.path.join(qt_source_path, "configure")] + build_opts +
			["-prefix", os.path.join(build_path, "target_x86_64")], cwd=os.path.join(build_path, "x86_64")) != 0:
			print("Failed to configure")
			sys.exit(1)

		print("\nBuilding Qt for x86_64...")
		# Build is unreliable but continues without issue, so try up to 5 times
		retry_count = 0
		while True:
			if subprocess.call([make_cmd] + parallel, cwd=os.path.join(build_path, "x86_64")) != 0:
				retry_count += 1
				if retry_count > 5:
					print("Qt failed to build")
					sys.exit(1)
			else:
				break

		print("\nInstalling Qt for x86_64...")
		if subprocess.call([make_cmd, "install"], cwd=os.path.join(build_path, "x86_64")) != 0:
			print("Qt failed to install")
			sys.exit(1)

	if platform.processor() == 'arm':
		print("\nConfiguring Qt for ARM64...")
		os.mkdir(os.path.join(build_path, "arm64"))
		os.environ["CMAKE_OSX_ARCHITECTURES"] = "arm64"
		if subprocess.call([os.path.join(qt_source_path, "configure")] + build_opts +
			["-prefix", os.path.join(build_path, "target_arm64")], cwd=os.path.join(build_path, "arm64")) != 0:
			print("Failed to configure")
			sys.exit(1)

		print("\nBuilding Qt for ARM64...")
		# Build is unreliable but continues without issue, so try up to 5 times
		retry_count = 0
		while True:
			if subprocess.call([make_cmd] + parallel, cwd=os.path.join(build_path, "arm64")) != 0:
				retry_count += 1
				if retry_count > 5:
					print("Qt failed to build")
					sys.exit(1)
			else:
				break

		print("\nInstalling Qt for ARM64...")
		if subprocess.call([make_cmd, "install"], cwd=os.path.join(build_path, "arm64")) != 0:
			print("Qt failed to install")
			sys.exit(1)

		if args.universal:
			print("\nCreating universal build...")
			if os.path.exists(install_path):
				remove_dir(install_path)
			os.makedirs(install_path)

			shutil.copytree(os.path.join(build_path, "target_arm64"), install_path, dirs_exist_ok=True, symlinks=True)

			for root, dirs, files in os.walk(install_path):
				rel_path = root.replace(str(install_path), "")
				rel_path = rel_path.strip('\/')
				for filename in files:
					if os.path.islink(os.path.join(root, filename)):
						continue
					if not os.path.isfile(os.path.join(root, filename)):
						continue
					header = open(os.path.join(root, filename), 'rb').read(4)
					if header != b"\xcf\xfa\xed\xfe" and header != b"!<ar":
						continue
					subprocess.call(["lipo", "-create", os.path.join(build_path, "target_x86_64", rel_path, filename),
						os.path.join(build_path, "target_arm64", rel_path, filename), "-output", os.path.join(root, filename)])
		else:
			if os.path.exists(install_path):
				remove_dir(install_path)
			os.makedirs(install_path)
			shutil.copytree(os.path.join(build_path, "target_arm64"), install_path, dirs_exist_ok=True, symlinks=True)
	else:
		if os.path.exists(install_path):
			remove_dir(install_path)
		os.makedirs(install_path)
		shutil.copytree(os.path.join(build_path, "target_x86_64"), install_path, dirs_exist_ok=True, symlinks=True)
else:
	if os.path.exists(install_path):
		remove_dir(install_path)
	os.mkdir(build_path)

	if sys.platform == 'linux':
		print("\n Configuring libicu...")

		if subprocess.call([os.path.join(qt_source_path, "icu", "icu4c", "source", "configure"),
			"--disable-draft", "--disable-extras", "--disable-icuio",
			"--disable-layoutex", "--disable-tools", "--disable-tests",
			"--disable-samples", "--prefix=" + str(install_path)],
			cwd=os.path.join(qt_source_path, "icu", "icu4c", "source")) != 0:
			print("Failed to configure")
			sys.exit(1)

		print("\nBuilding libicu...")
		if subprocess.call(["make"] + parallel, cwd=os.path.join(qt_source_path, "icu", "icu4c", "source")) != 0:
			print("libicu failed to build")
			sys.exit(1)

		print("\nInstalling Qt...")
		if subprocess.call(["make", "install"], cwd=os.path.join(qt_source_path, "icu", "icu4c", "source")) != 0:
			print("Qt failed to install")
			sys.exit(1)

		os.environ["ICU_PREFIX"] = str(install_path)

		build_opts += ['-bundled-xcb-xinput']

	print("\nConfiguring Qt...")
	if sys.platform == 'win32':
		build_opts += ["-directwrite"]  # use DirectWrite for font rendering on Windows
		if subprocess.call([os.path.join(qt_source_path, "configure.bat")] + build_opts +
			["-prefix", install_path], cwd=build_path) != 0:
			print("Failed to configure")
			sys.exit(1)
	else:
		if subprocess.call([os.path.join(qt_source_path, "configure")] + build_opts +
			["-prefix", install_path], cwd=build_path) != 0:
			print("Failed to configure")
			sys.exit(1)

	print("\nBuilding Qt...")
	# Build is unreliable but continues without issue, so try up to 5 times
	retry_count = 0
	while True:
		if subprocess.call([make_cmd] + parallel, cwd=build_path) != 0:
			retry_count += 1
			if retry_count > 5:
				print("Qt failed to build")
				sys.exit(1)
		else:
			break

	print("\nInstalling Qt...")
	if subprocess.call([make_cmd, "install"], cwd=build_path) != 0:
		print("Qt failed to install")
		sys.exit(1)

	if sys.platform == 'linux':
		# Older compilers don't seem to want to take libicu built above, just make sure we
		# bundle the one that is actually used
		deps = subprocess.Popen(["ldd", os.path.join(install_path, "lib/libQt6Core.so")], stdout=subprocess.PIPE).communicate()[0]
		deps = deps.decode('charmap').strip().split("\n")
		deps = [line.split("=>") for line in deps]
		for dep in deps:
			if len(dep) < 2:
				continue
			name, path = dep[0].strip(), dep[1].split("(")[0].strip()
			if "libicu" in path and "Qt" not in path:
				shutil.copyfile(path, os.path.join(install_path, "lib", name))
				subprocess.call(f'patchelf --set-rpath \\$ORIGIN {install_path}/lib/{name}', shell=True)


if args.pyside:
	print("\nBuilding Python 3 bindings...")
	if sys.platform == 'win32':
		os.environ["PATH"] = f'{str(install_path / "bin")};{os.environ["PATH"]}'
	if os.path.exists(pyside_build_path):
		remove_dir(pyside_build_path)
	shutil.copytree(pyside_source_path, pyside_build_path)
	if os.path.exists(pyside_install_path):
		remove_dir(pyside_install_path)
	if sys.platform == 'darwin':
		if platform.processor() == 'arm' and args.universal:
			os.environ["CMAKE_OSX_ARCHITECTURES"] = "arm64;x86_64"
	if subprocess.call([python3_cmd, "-m", "pip", "install", "-r", "requirements.txt"], cwd=pyside_build_path) != 0:
		print("Python 3 bindings failed to install package dependencies")
		sys.exit(1)
	if subprocess.call([python3_cmd, "setup.py", "install", "--standalone", "--limited-api=yes", "--no-unity",
			"--module-subset=" + ",".join(pyside_modules),
			"--qt-target-path=" + str(install_path),
			"--qtpaths=" + str(qtpaths),
			"--macos-deployment-target=" + min_macos,
			"--prefix=" + str(pyside_install_path),
		] + parallel, cwd=pyside_build_path) != 0:
		print("Python 3 bindings failed to build")
		sys.exit(1)

	if sys.platform.startswith("win"):
		# pyside/Lib/site-packages -> pyside/site-packages
		# For compatibility with our previous build format
		shutil.move(pyside_install_path / 'Lib' / 'site-packages', pyside_install_path)

		# pyside/Scripts/shiboken6-genpyi.exe -> pyside/site-packages/shiboken6_generator/
		# pyside/Scripts/shiboken6-genpyi-script.py -> pyside/site-packages/shiboken6_generator/
		shutil.move(pyside_install_path / 'Scripts' / 'shiboken6-genpyi.exe', pyside_install_path / 'site-packages' / 'shiboken6_generator')
		shutil.move(pyside_install_path / 'Scripts' / 'shiboken6-genpyi-script.py', pyside_install_path / 'site-packages' / 'shiboken6_generator')

		# And we don't care about the rest of the Scripts folder
		shutil.rmtree(pyside_install_path / 'Scripts')
	else:
		# pyside/lib/python3.9/site-packages -> pyside/site-packages
		# For compatibility with our previous build format
		for pydir in (pyside_install_path / 'lib').glob('python3.*'):
			shutil.move(pydir / 'site-packages', pyside_install_path)
			shutil.rmtree(pydir)
			break

		# pyside/bin/shiboken6-genpyi -> pyside/site-packages/shiboken6_generator/
		shutil.move(pyside_install_path / 'bin' / 'shiboken6-genpyi', pyside_install_path / 'site-packages' / 'shiboken6_generator')

		# And we don't care about the rest of the bin folder
		shutil.rmtree(pyside_install_path / 'bin')

		# Replace shebang (which is like /Users/jenkins/etc) with a real python
		with open(pyside_install_path / 'site-packages' / 'shiboken6_generator' / 'shiboken6-genpyi', 'r') as genpyi_f:
			conts = "#!/usr/bin/env python3\n"
			genpyi_f.readline()
			conts += genpyi_f.read()
		with open(pyside_install_path / 'site-packages' / 'shiboken6_generator' / 'shiboken6-genpyi', 'w') as genpyi_f:
			genpyi_f.write(conts)

	if sys.platform == 'linux' and llvm_dir:
		# Newer versions of PySide don't link to libclang in a way that works after the build, copy over
		# the correct version of libclang
		for f in glob.glob(os.path.join(llvm_dir, "lib", "libclang.so*")):
			shutil.copy(f, os.path.join(pyside_install_path, "site-packages", "shiboken6_generator", os.path.basename(f)), follow_symlinks=False)

	# Add PySide installer to place it into Python path
	shutil.copy(os.path.join(base_dir, "install_pyside_pth.py"), os.path.join(install_path, "install_pyside_pth.py"))


# Create modified libraries that contain the correct rpath for bundling. These will be signed separately
# so that each bundle does not need to re-sign the libraries.
if sys.platform == 'darwin':
	plugin_types = ["platforms", "imageformats"]
	os.mkdir(bundle_path)
	for plugin_type in plugin_types:
		os.mkdir(os.path.join(bundle_path, plugin_type))
	if args.pyside:
		os.mkdir(os.path.join(bundle_path, "PySide6"))

	for plugin_type in plugin_types:
		for f in glob.glob(os.path.join(install_path, "plugins", plugin_type, "*.dylib")):
			target = os.path.join(bundle_path, plugin_type, os.path.basename(f))
			shutil.copy(f, target)
			if subprocess.call(["install_name_tool", "-delete_rpath", "@loader_path/../../lib", target]) != 0:
				print(f"Failed to remove rpath from {target}")
				sys.exit(1)
			if subprocess.call(["install_name_tool", "-add_rpath", "@loader_path/../../../Frameworks", target]) != 0:
				print(f"Failed to add framework rpath to {target}")
				sys.exit(1)

	if args.pyside:
		for f in glob.glob(os.path.join(pyside_install_path, "site-packages", "PySide6", "*.so")):
			target = os.path.join(bundle_path, "PySide6", os.path.basename(f))
			shutil.copy(f, target)
			if subprocess.call(["install_name_tool", "-delete_rpath", "@loader_path/Qt/lib", target]) != 0:
				print(f"Failed to remove rpath from {target}")
				sys.exit(1)
			if subprocess.call(["install_name_tool", "-add_rpath", "@loader_path/../../../Frameworks", target]) != 0:
				print(f"Failed to add framework rpath to {target}")
				sys.exit(1)

		for f in glob.glob(os.path.join(pyside_install_path, "site-packages", "PySide6", "*.dylib")):
			target = os.path.join(bundle_path, "PySide6", os.path.basename(f))
			shutil.copy(f, target)
			if subprocess.call(["install_name_tool", "-delete_rpath", "@loader_path/Qt/lib", target]) != 0:
				print(f"Failed to remove rpath from {target}")
				sys.exit(1)

			if subprocess.call(["install_name_tool", "-add_rpath", "@loader_path/../../../Frameworks", target]) != 0:
				print(f"Failed to add framework rpath to {target}")
				sys.exit(1)
elif sys.platform == 'linux':
	plugin_types = ["platforms", "imageformats", "wayland-decoration-client", "wayland-graphics-integration-client",
		"wayland-shell-integration"]
	os.mkdir(bundle_path)
	for plugin_type in plugin_types:
		os.mkdir(os.path.join(bundle_path, plugin_type))
	if args.pyside:
		os.mkdir(os.path.join(bundle_path, "PySide6"))

	for plugin_type in plugin_types:
		for f in glob.glob(os.path.join(install_path, "plugins", plugin_type, "*.so")):
			target = os.path.join(bundle_path, plugin_type, os.path.basename(f))
			shutil.copy(f, target)
			if subprocess.call(["patchelf", "--set-rpath", "$ORIGIN/../..", target]) != 0:
				print(f"ERROR: Failed to change rpath in {target}")
				sys.exit(1)

	if args.pyside:
		qt_major_minor_version = ".".join(qt_version.split(".")[0:2])
		for f in glob.glob(os.path.join(pyside_install_path, "site-packages", "PySide6", "*.abi3.so")):
			target = os.path.join(bundle_path, "PySide6", os.path.basename(f))
			shutil.copy(f, target)
			if subprocess.call(["patchelf", "--set-rpath", "$ORIGIN:$ORIGIN/../shiboken6:$ORIGIN/../..", target]) != 0:
				print(f"Failed to change rpath in {target}")
				sys.exit(1)

		pyside_module = f"libpyside6.abi3.so.{qt_major_minor_version}"
		target = os.path.join(bundle_path, "PySide6", pyside_module)
		shutil.copy(os.path.join(pyside_install_path, "site-packages", "PySide6", pyside_module), target)
		if subprocess.call(["patchelf", "--set-rpath" ,"$ORIGIN:$ORIGIN/../shiboken6:$ORIGIN/../..", target]) != 0:
			print("Failed to change rpath in libpyside")
			sys.exit(1)


if args.sign:
	if sys.platform == 'darwin':
		# Sign all Mach-O files in the installation
		for root, dirs, files in os.walk(install_path):
			for file in files:
				file_path = os.path.join(root, file)
				if not os.access(file_path, os.X_OK):
					continue

				# Check for Mach-O signature
				header = open(file_path, 'rb').read(4)
				if header != b"\xca\xfe\xba\xbe" and header != b"\xcf\xfa\xed\xfe":
					continue

				if not mac_sign(file_path):
					print(f"Failed to sign {file_path}")
					sys.exit(1)

		# Sign all frameworks and applications in the installation
		for root, dirs, files in os.walk(install_path):
			for dir in dirs:
				if ".framework" in dir or ".app" in dir:
					dir_path = os.path.join(root, dir)

					if not mac_sign(dir_path):
						print(f"Failed to sign {dir_path}")
						sys.exit(1)
	elif sys.platform.startswith("win"):
		# Look for all exe/dll files in the installation
		for root, dirs, files in os.walk(install_path):
			for file in files:
				if file.endswith(".exe") or file.endswith(".dll") or file.endswith(".pyd"):
					file_path = os.path.join(root, file)
					if not signWindowsFiles(file_path):
						print(f"Failed to sign {file_path}")
						sys.exit(1)


print("\nCreating archive...")
with zipfile.ZipFile(artifact_path / f'qt{qt_version}.zip', 'w', zipfile.ZIP_DEFLATED) as z:
	for root, dirs, files in os.walk(install_path):
		relpath = root.replace(str(install_path), "")
		relpath = relpath.strip('\/')
		for dir in dirs:
			file_path = os.path.join(root, dir)
			arc_name = os.path.join("Qt", qt_version_dir, compiler, relpath, dir)
			if os.path.islink(file_path):
				info = zipfile.ZipInfo(arc_name, datetime.datetime.now().timetuple())
				info.compress_type = zipfile.ZIP_DEFLATED
				info.external_attr = 0o120755 << 16
				z.writestr(info, os.readlink(file_path))
		for file in files:
			print(f"Adding {relpath}/{file}...")
			file_path = os.path.join(root, file)
			arc_name = os.path.join("Qt", qt_version_dir, compiler, relpath, file)
			info = zipfile.ZipInfo(arc_name, datetime.datetime.now().timetuple())
			info.compress_type = zipfile.ZIP_DEFLATED

			if os.path.islink(file_path):
				info.external_attr = 0o120755 << 16
				z.writestr(info, os.readlink(file_path))
			else:
				if os.access(file_path, os.X_OK):
					info.external_attr = 0o755 << 16 # -rwxr-xr-x
				else:
					info.external_attr = 0o644 << 16 # -rwxr--r--

				with open(file_path, 'rb') as f:
					z.writestr(info, f.read())


if args.install:
	import deploy


if args.clean:
	print("Cleaning up...")
	remove_dir(source_path)
