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
import tempfile

from math import ceil
from pathlib import Path

from build_metadata import emit_build_metadata
from target_qt6_version import qt_version, llvm_version, msvc_build, msvc_dir_name, vs_version, min_macos, qt_modules, pyside_modules


MAKE_CMD = "ninja"
CMAKE_GENERATOR = ["-G", "Ninja"]
DEFAULT_QT_MIRROR = "https://github.com/qt/"
QT_REPO_URL = "https://github.com/qt/qt5.git"
PYSIDE_REPO_URL = "https://codereview.qt-project.org/pyside/pyside-setup"
ICU_REPO_URL = "https://github.com/unicode-org/icu.git"
ICU_VERSION = "release-68-2"
QLITEHTML_REPO_URL = "https://code.qt.io/playground/qlitehtml.git"
WINDOWS_TIMESTAMP_SERVERS = ("http://timestamp.digicert.com", "http://timestamp.comodoca.com/rfc3161")
BUILD_RETRY_LIMIT = 5
ZIP_SYMLINK_ATTR = 0o120755 << 16
ZIP_EXECUTABLE_ATTR = 0o755 << 16 # -rwxr-xr-x
ZIP_REGULAR_FILE_ATTR = 0o644 << 16 # -rw-r--r--
MACOS_COMPILER = "clang_64"
LINUX_COMPILER = "gcc_64"
MACOS_PLUGIN_TYPES = ("platforms", "imageformats")
LINUX_PLUGIN_TYPES = (
	"platforms", "imageformats", "wayland-decoration-client", "wayland-graphics-integration-client",
	"wayland-shell-integration", "platforminputcontexts"
)
BASE_BUILD_OPTS = [
    "-no-static", "-release", "-opensource", "-confirm-license", "-nomake", "examples",
	"-nomake", "tests", "-no-feature-tuiotouch", "-qt-libpng", "-qt-libjpeg", "-qt-libb2", "-no-glib",
	"-qt-tiff", "-qt-webp", "-qt-pcre", "-no-feature-zstd", "-no-feature-brotli", "-no-feature-graphicseffect",
	"-no-feature-style-windowsvista", "-no-feature-style-windows11"
]


def step(name):
	print(f"\n=== Step: {name} ===")


def run_checked(cmd, error_message, cwd=None, shell=False):
	if subprocess.call(cmd, cwd=cwd, shell=shell) != 0:
		print(error_message)
		sys.exit(1)


def run_checked_with_retries(cmd, error_message, cwd=None, retry_limit=BUILD_RETRY_LIMIT):
    # Build is sometimes unreliable but continues without issue, try up to 5 times
	retry_count = 0
	while True:
		if subprocess.call(cmd, cwd=cwd) != 0:
			retry_count += 1
			if retry_count > retry_limit:
				print(error_message)
				sys.exit(1)
		else:
			break


build_opts = list(BASE_BUILD_OPTS)
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


def install_staged_output(staged_path, user_qt_path):
	user_qt_old_path = user_qt_path.parent / (user_qt_path.name + '-old')

	if user_qt_old_path.exists():
		print(f'Removing backup install at {user_qt_old_path}')
		remove_dir(user_qt_old_path)

	if user_qt_path.exists():
		print(f'Overwriting existing Qt at {user_qt_path} with {staged_path}')
		print(f'Moving {user_qt_path} to {user_qt_old_path} just in case')
		user_qt_path.rename(user_qt_old_path)
	else:
		print(f'Installing new Qt at {user_qt_path} with {staged_path}')

	user_qt_path.parent.mkdir(parents=True, exist_ok=True)
	shutil.copytree(staged_path, user_qt_path, symlinks=True)


def normalized_platform():
	if sys.platform == 'darwin':
		return 'macosx'
	if sys.platform.startswith('linux'):
		return 'linux-arm' if platform.machine().lower() in ('aarch64', 'arm64') else 'linux'
	if sys.platform.startswith('win'):
		return 'win64'
	return sys.platform


def should_package_file(file_name):
	return file_name != '.DS_Store'


def keychain_unlocker():
	keychain_unlocker = os.environ["HOME"] + "/unlock-keychain"
	if os.path.exists(keychain_unlocker):
		return subprocess.call([keychain_unlocker]) == 0
	return True


def mac_should_strip(file_path):
	"""Check if a file is a Mach-O binary that we should strip."""
	if os.path.islink(file_path) or not os.path.isfile(file_path):
		return False
	if file_path.endswith('.o'):
		return False
	header = open(file_path, 'rb').read(4)
	if header not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
		return False
	# Skip binaries that are already signed with a non-ad-hoc signature.
	# They were built by another project and it is that project's
	# responsibility to provide debug symbols for them.
	sig = subprocess.run(["codesign", "-d", "--verbose=2", file_path],
		capture_output=True, text=True)
	if sig.returncode == 0 and "Authority=" in sig.stderr:
		return False
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


def signWindowsFiles(path: str):
	for timeServer in WINDOWS_TIMESTAMP_SERVERS:
		proc = subprocess.run([
			"java", "-jar",
			"C:\\jenkins\\jsign.jar",
			"--name", "Binary Ninja",
			"--url", "https://binary.ninja/",
			"--storetype", "PIV",
			"--storepass", os.environ['YUBIKEY_PIN'],
			"--tsaurl", timeServer,
			"--tsmode", "RFC3161",
			"--alias", "AUTHENTICATION",
			"--certfile", "C:\\jenkins\\yubi-1-user.crt",
			path
		], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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


def parse_env_bool(name):
	value = os.environ.get(name)
	if value is None:
		return None
	value = value.strip().lower()
	if value in ("1", "true", "yes", "on", "y"):
		return True
	if value in ("0", "false", "no", "off", "n"):
		return False
	raise ValueError(f"Invalid boolean value for {name}: {os.environ[name]!r}")


def apply_env_defaults(args, parser):
	try:
		clean = parse_env_bool("CLEAN")
		sign = parse_env_bool("SIGN")
		no_install = parse_env_bool("NO_INSTALL")
		no_prompt = parse_env_bool("NO_PROMPT")
	except ValueError as e:
		parser.error(str(e))

	if args.clean is None:
		args.clean = True if clean is None else clean
	if args.sign is None:
		args.sign = False if sign is None else sign
	if args.install is None:
		args.install = True if no_install is None else not no_install
	if args.prompt is None:
		args.prompt = True if no_prompt is None else not no_prompt
	if args.mirror is None:
		args.mirror = os.environ.get("SOURCE_MIRROR")
	if args.build_dir is None:
		args.build_dir = os.environ.get("BUILD_DIR")
	if hasattr(args, "jobs") and args.jobs is None:
		args.jobs = os.environ.get("JOBS", ceil(os.cpu_count()*1.1))

	build_variant = os.environ.get("BUILD_VARIANT")
	if build_variant and not (args.debug or args.asan or args.tsan):
		build_variant = build_variant.strip().lower()
		if build_variant == "debug":
			args.debug = True
		elif build_variant == "asan":
			args.asan = True
		elif build_variant == "tsan":
			args.tsan = True
		elif build_variant != "release":
			parser.error("Invalid BUILD_VARIANT: expected release, debug, asan, or tsan")


step("validate/configure inputs")
parser = argparse.ArgumentParser(description = "Build and install Qt 6", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--no-clone", help="skip cloning the Qt 6 source code", action="store_true")
parser.add_argument("--no-clean", dest='clean', action='store_false', default=None, help="skip removing the Qt 6 source code")
parser.add_argument("--clean", dest='clean', action='store_true', help="remove the Qt 6 source code before building")
parser.add_argument("--no-prompt", dest='prompt', action='store_false', default=None, help="Don't wait for user prompt")
parser.add_argument("--prompt", dest='prompt', action='store_true', help="Wait for user prompt")
parser.add_argument("--no-install", dest='install', action='store_false', default=None, help="Don't install build products to your home folder")
parser.add_argument("--install", dest='install', action='store_true', help="Install build products to your home folder")
parser.add_argument("--no-pyside", dest='pyside', action='store_false', default=True, help="Don't build PySide")
parser.add_argument("--patch", help="patch the source before building")
variant_group = parser.add_mutually_exclusive_group()
variant_group.add_argument("--asan", help="build with ASAN", action="store_true")
variant_group.add_argument("--tsan", help="build with TSAN", action="store_true")
variant_group.add_argument("--debug", help="build a debug configuration", action="store_true")
parser.add_argument("--universal", help="build for both x86_64 and arm64 (arm64 Mac host only)", action="store_true")
parser.add_argument("--mirror", help="use source mirror", action="store")
parser.add_argument("--sign", dest='sign', help="sign all executables", action="store_true", default=None)
parser.add_argument("--no-sign", dest='sign', help="don't sign executables", action="store_false")
parser.add_argument("--qt-source", help="use Qt source directory", action="store")
parser.add_argument("--pyside-source", help="use PySide source directory", action="store")
parser.add_argument("--build-dir", dest="build_dir", help="Custom build directory to bypass windows PATH_MAX limits", action="store")
parser.add_argument("--symbols", help="extract debug symbols into a separate archive and strip debug info from binaries", action="store_true", default=True)
parser.add_argument("--no-symbols", dest="symbols", help="disable debug symbol extraction", action="store_false")

if not sys.platform.startswith("win"):
	parser.add_argument("-j", "--jobs", dest='jobs', default=None, help="Number of build threads (Defaults to 1.1*cpu_count)")

args = parser.parse_args()
apply_env_defaults(args, parser)

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

extra_cmake_args = []
if args.symbols:
	if sys.platform == 'win32':
		debug_flag = "/Zi"
		extra_cmake_args += ["-DCMAKE_EXE_LINKER_FLAGS=/DEBUG",
			"-DCMAKE_MODULE_LINKER_FLAGS=/DEBUG",
			"-DCMAKE_SHARED_LINKER_FLAGS=/DEBUG"]
	elif sys.platform == 'darwin':
		debug_flag = "-gline-tables-only"
	else:
		debug_flag = "-g1"
	extra_cmake_args += [f"-DCMAKE_C_FLAGS={debug_flag}",
		f"-DCMAKE_CXX_FLAGS={debug_flag}"]
configure_extra = ["--"] + extra_cmake_args if extra_cmake_args else []

mirror = []
if args.mirror:
	print(f"Using source mirror: {args.mirror}")
	mirror = ["--mirror", args.mirror]
else:
	mirror = ["--mirror", DEFAULT_QT_MIRROR]

if sys.version_info.major < 3:
	print('Please build Qt 6 with Python 3')
	exit(1)

if args.pyside and "VIRTUAL_ENV" not in os.environ:
    print('Running under a virtual environment is required to build PySide')
    exit(1)

if sys.platform.startswith("win"):
	make_cmd = MAKE_CMD
	parallel = []
	cmake_generator_array = CMAKE_GENERATOR

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
	make_cmd = MAKE_CMD
	parallel = ["-j", str(args.jobs)]
	cmake_generator_array = CMAKE_GENERATOR

if sys.platform == 'win32':
	os.environ["HOME"] = os.environ["HOMEDRIVE"] + os.environ["HOMEPATH"]

platform_name = normalized_platform()

# Copy libclang to the build directory
extern_libclang_artifact = Path("artifacts-extern") / "artifacts" / f"libclang_{platform_name}_{llvm_version}.zip"
if extern_libclang_artifact.exists():
	with zipfile.ZipFile(extern_libclang_artifact) as zf:
		zf.extractall('build')
		os.environ['LLVM_INSTALL_DIR'] = str((Path("build") / "libclang").resolve())

if "LLVM_INSTALL_DIR" in os.environ:
	llvm_dir = Path(os.environ["LLVM_INSTALL_DIR"]) / llvm_version
else:
	llvm_dir = Path(os.environ["HOME"]) / "libclang" / llvm_version
if not llvm_dir.exists():
	print("libclang needs to be installed.")
	print(f'Set LLVM_INSTALL_DIR, or install to {Path(os.environ["HOME"]) / "libclang" / llvm_version}')
	sys.exit(1)
os.environ["LLVM_INSTALL_DIR"] = str(llvm_dir)

base_dir = Path(__file__).resolve().parent
if args.build_dir is not None:
	qt_dir = Path(args.build_dir).expanduser().resolve()
else:
	qt_dir = base_dir / "build"

source_path = qt_dir / "src"
qt_source_path = source_path / "qt"
build_path = source_path / "build"
artifact_path = Path(os.environ["ARTIFACTS_DIR"]).expanduser().resolve() if "ARTIFACTS_DIR" in os.environ else base_dir / "artifacts"
if args.asan:
	qt_version_dir = qt_version + "-asan"
elif args.tsan:
	qt_version_dir = qt_version + "-tsan"
else:
	qt_version_dir = qt_version
qt_artifact_name = f'qt_{platform_name}_{qt_version}.zip'
qt_symbols_artifact_name = f'qt_symbols_{platform_name}_{qt_version}.zip'
if sys.platform == 'win32':
	compiler = msvc_dir_name
elif sys.platform == 'darwin':
	compiler = MACOS_COMPILER
else:
	compiler = LINUX_COMPILER
install_path = qt_dir / "install" / "Qt" / qt_version_dir / compiler
qt_archive_root = os.path.join('Qt', qt_version_dir)
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



step("print reproducibility metadata")
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

emit_build_metadata(
	repo_name="qt-build",
	artifact_path=artifact_path,
	paths={
		"repo_root": base_dir,
		"source_path": source_path,
		"qt_source_path": qt_source_path,
		"pyside_source_path": pyside_source_path,
		"build_path": build_path,
		"pyside_build_path": pyside_build_path,
		"install_path": install_path,
		"pyside_install_path": pyside_install_path,
		"artifact_path": artifact_path,
		"user_install_path": user_qt_parent_path if args.install else None,
		"llvm_path": llvm_dir,
	},
	versions={
		"qt_version": qt_version,
		"llvm_version": llvm_version,
		"msvc_build": msvc_build,
		"msvc_dir_name": msvc_dir_name,
		"vs_version": vs_version,
		"min_macos": min_macos,
		"qt_modules": qt_modules,
		"pyside_modules": pyside_modules,
	},
	options={
		"artifact_filenames": {
			"qt": qt_artifact_name,
			"qt_symbols": qt_symbols_artifact_name,
		},
		"archive_internal_roots": {
			"qt": qt_archive_root,
			"qt_symbols": ".",
		},
		"no_clone": args.no_clone,
		"clean": args.clean,
		"install": args.install,
		"prompt": args.prompt,
		"pyside": args.pyside,
		"patch": args.patch,
		"asan": args.asan,
		"tsan": args.tsan,
		"debug": args.debug,
		"universal": args.universal,
		"mirror": args.mirror,
		"sign": args.sign,
		"qt_source": args.qt_source,
		"pyside_source": args.pyside_source,
		"build_dir": args.build_dir,
		"symbols": args.symbols,
		"jobs": getattr(args, "jobs", None),
	},
	env_var_names=(
		"JOB_NAME", "BUILD_NUMBER", "BUILD_URL", "BRANCH_NAME", "CHANGE_ID", "WORKSPACE",
		"PYTHONUNBUFFERED", "BUILD_DIR", "ARTIFACTS_DIR", "SOURCE_MIRROR", "JOBS", "SIGN",
		"NO_INSTALL", "NO_PROMPT", "CLEAN", "BUILD_VARIANT", "QT_INSTALL_DIR", "LLVM_INSTALL_DIR",
		"YUBIKEY_PIN",
	),
)


qt_patches = []
for patch in sorted(qt_patches_path.iterdir()):
	if patch.suffix == '.patch':
		qt_patches.append(patch.resolve())

pyside_patches = []
for patch in sorted(pyside_patches_path.iterdir()):
	if patch.suffix == '.patch':
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

if sys.platform.startswith("win") and len(str(qt_dir)) > 40:
	# I cannot believe this is a real issue and yet there went 30 minutes of my life
	print()
	print("\xF0\x9F\x9A\xAB Your build directory is too long and Windows will probably give you weird errors about files not being found")
	print("\xF0\x9F\x9A\xAB You can try building anyway, though! Godspeed!")
	if not args.prompt:
		sys.exit(1)

if args.prompt:
	step("confirm prompt")
	if input("\nIs this correct (y/n)? ") != "y":
		print("Aborted")
		sys.exit(1)


step("prepare directories")
if not artifact_path.exists():
	artifact_path.mkdir(parents=True)


if args.clean:
	# Clean existing files
	for f in artifact_path.glob('*'):
		if f.name == 'build-metadata.json':
			continue
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
	step("fetch/copy source")
	if os.path.exists(source_path):
		remove_dir(source_path)

	if args.qt_source:
		print("\nCopying existing Qt source...")
		shutil.copytree(args.qt_source, qt_source_path)
	else:
		print("\nCloning Qt...")
		if args.mirror:
			run_checked(["git", "clone", f"{args.mirror}qt5.git", qt_source_path], "Failed to clone Qt git repository")
		else:
			run_checked(["git", "clone", QT_REPO_URL, qt_source_path], "Failed to clone Qt git repository")
		run_checked(["git", "checkout", qt_version], "Failed to check out branch/tag '{}'".format(qt_version), cwd=qt_source_path)

		init_repo_options = ["--module-subset=" + ",".join(qt_modules), "--no-update"]
		if sys.platform == 'win32':
			run_checked(["perl", qt_source_path / "init-repository.pl"] + init_repo_options + mirror, "Failed to initialize submodules", cwd=qt_source_path)
		else:
			run_checked([qt_source_path / "init-repository"] + init_repo_options + mirror, "Failed to initialize submodules", cwd=qt_source_path)

		# Check out submodules, but don't check out recursively until we've had a chance to patch
		# module paths
		run_checked(["git", "submodule", "update", "--init"] + qt_modules, "Failed to check out submodules", cwd=qt_source_path)

		if args.mirror:
			# Fix qttools .gitmodules to use mirror
			(qt_source_path / "qttools" / ".gitmodules").write_text(
				'[submodule "src/assistant/qlitehtml"]\n' +
				'    path = src/assistant/qlitehtml\n' +
				f'    url = {args.mirror}playground/qlitehtml.git',
				encoding='utf-8'
			)
		else:
			# Fix qttools to use absolute path since the relative path fails on anything that isn't the
			# official repo, which is so slow and unreliable it fails many builds.
			(qt_source_path / "qttools" / ".gitmodules").write_text(
				'[submodule "src/assistant/qlitehtml"]\n' +
				'    path = src/assistant/qlitehtml\n' +
				f'    url = {QLITEHTML_REPO_URL}',
				encoding='utf-8'
			)

		# Check out all submodules
		run_checked(["git", "submodule", "update", "--init", "--recursive"] + qt_modules, "Failed to check out submodules", cwd=qt_source_path)

		step("apply patches")
		for patch in qt_patches:
			print(f"\nApplying patch {patch}...")
			apply_patch(patch, qt_source_path)

		if args.patch:
			print("\nApplying user provided patch...")
			apply_patch(args.patch, qt_source_path)

	if sys.platform == 'linux':
		print("Cloning libicu")
		if args.mirror:
			run_checked(["git", "clone", f"{args.mirror}icu.git", qt_source_path / "icu"], "Failed to clone Qt git repository")
		else:
			run_checked(["git", "clone", ICU_REPO_URL, qt_source_path / "icu"], "Failed to clone Qt git repository")
		run_checked(["git", "checkout", ICU_VERSION], "Failed to check out branch '{}'".format(ICU_VERSION), cwd=qt_source_path / "icu")

	if args.pyside:
		if args.pyside_source:
			print("\nCopying existing PySide source...")
			shutil.copytree(args.pyside_source, pyside_source_path)
		else:
			print("\nCloning pyside-setup...")
			if args.mirror:
				run_checked(["git", "clone", "-b", qt_version, "--depth", "1",
					f"{args.mirror}pyside-setup", pyside_source_path], "Failed to clone PySide git repository")
			else:
				run_checked(["git", "clone", "-b", qt_version, "--depth", "1",
					PYSIDE_REPO_URL, pyside_source_path], "Failed to clone PySide git repository")

			step("apply patches")
			for patch in pyside_patches:
				print(f"\nApplying patch {patch}...")
				apply_patch(patch, pyside_source_path)

step("prepare directories")
if os.path.exists(build_path):
	remove_dir(build_path)

if sys.platform == 'darwin':
	build_opts += ['-qt-freetype']
	os.mkdir(build_path)

	if platform.processor() != 'arm' or args.universal:
		step("configure dependencies/toolchain")
		print("\nConfiguring Qt for x86_64...")
		(build_path / "x86_64").mkdir()
		os.environ["CMAKE_OSX_ARCHITECTURES"] = "x86_64"
		run_checked([qt_source_path / "configure"] + build_opts +
			["-prefix", build_path / "target_x86_64"] + configure_extra, "Failed to configure", cwd=build_path / "x86_64")

		step("build")
		print("\nBuilding Qt for x86_64...")
		run_checked_with_retries([make_cmd] + parallel, "Qt failed to build", cwd=build_path / "x86_64")

		step("install/stage")
		print("\nInstalling Qt for x86_64...")
		run_checked([make_cmd, "install"], "Qt failed to install", cwd=build_path / "x86_64")

	if platform.processor() == 'arm':
		step("configure dependencies/toolchain")
		print("\nConfiguring Qt for ARM64...")
		(build_path / "arm64").mkdir()
		os.environ["CMAKE_OSX_ARCHITECTURES"] = "arm64"
		run_checked([qt_source_path / "configure"] + build_opts +
			["-prefix", build_path / "target_arm64"] + configure_extra, "Failed to configure", cwd=build_path / "arm64")

		step("build")
		print("\nBuilding Qt for ARM64...")
		run_checked_with_retries([make_cmd] + parallel, "Qt failed to build", cwd=build_path / "arm64")

		step("install/stage")
		print("\nInstalling Qt for ARM64...")
		run_checked([make_cmd, "install"], "Qt failed to install", cwd=build_path / "arm64")

		if args.universal:
			print("\nCreating universal build...")
			if os.path.exists(install_path):
				remove_dir(install_path)
			os.makedirs(install_path)

			shutil.copytree(os.path.join(build_path, "target_arm64"), install_path, dirs_exist_ok=True, symlinks=True)

			for root, dirs, files in os.walk(install_path):
				rel_path = Path(root).resolve().relative_to(install_path.resolve())
				for filename in files:
					file_path = Path(root) / filename
					if file_path.is_symlink():
						continue
					if not file_path.is_file():
						continue
					header = file_path.open('rb').read(4)
					if header != b"\xcf\xfa\xed\xfe" and header != b"!<ar":
						continue
					subprocess.call(["lipo", "-create", build_path / "target_x86_64" / rel_path / filename,
						build_path / "target_arm64" / rel_path / filename, "-output", file_path])
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
		step("configure dependencies/toolchain")
		print("\n Configuring libicu...")

		icu_source_path = qt_source_path / "icu" / "icu4c" / "source"
		run_checked([icu_source_path / "configure",
			"--disable-draft", "--disable-extras", "--disable-icuio",
			"--disable-layoutex", "--disable-tools", "--disable-tests",
			"--disable-samples", "--prefix=" + str(install_path)],
			"Failed to configure", cwd=icu_source_path)

		step("build")
		print("\nBuilding libicu...")
		run_checked(["make"] + parallel, "libicu failed to build", cwd=icu_source_path)

		step("install/stage")
		print("\nInstalling Qt...")
		run_checked(["make", "install"], "Qt failed to install", cwd=icu_source_path)

		os.environ["ICU_PREFIX"] = str(install_path)

		build_opts += ['-bundled-xcb-xinput']

	step("configure dependencies/toolchain")
	print("\nConfiguring Qt...")
	if sys.platform == 'win32':
		build_opts += ["-directwrite"]  # use DirectWrite for font rendering on Windows
		run_checked([qt_source_path / "configure.bat"] + build_opts +
			["-prefix", install_path] + configure_extra, "Failed to configure", cwd=build_path)
	else:
		run_checked([qt_source_path / "configure"] + build_opts +
			["-prefix", install_path] + configure_extra, "Failed to configure", cwd=build_path)

	step("build")
	print("\nBuilding Qt...")
	run_checked_with_retries([make_cmd] + parallel, "Qt failed to build", cwd=build_path)

	step("install/stage")
	print("\nInstalling Qt...")
	run_checked([make_cmd, "install"], "Qt failed to install", cwd=build_path)

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
	step("build")
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
	if args.symbols:
		if sys.platform == 'win32':
			os.environ["CFLAGS"] = os.environ.get("CFLAGS", "") + " /Zi"
			os.environ["CXXFLAGS"] = os.environ.get("CXXFLAGS", "") + " /Zi"
		elif sys.platform == 'darwin':
			os.environ["CFLAGS"] = os.environ.get("CFLAGS", "") + " -gline-tables-only"
			os.environ["CXXFLAGS"] = os.environ.get("CXXFLAGS", "") + " -gline-tables-only"
		else:
			os.environ["CFLAGS"] = os.environ.get("CFLAGS", "") + " -g1"
			os.environ["CXXFLAGS"] = os.environ.get("CXXFLAGS", "") + " -g1"
	run_checked(["uv", "pip", "install", "--python", sys.executable, "-r", "requirements.txt"],
		"Python 3 bindings failed to install package dependencies", cwd=pyside_build_path)
	run_checked([sys.executable, "setup.py", "install", "--standalone", "--limited-api=yes", "--no-unity",
			"--module-subset=" + ",".join(pyside_modules),
			"--qt-target-path=" + str(install_path),
			"--qtpaths=" + str(qtpaths),
			"--macos-deployment-target=" + min_macos,
			"--prefix=" + str(pyside_install_path),
		] + parallel, "Python 3 bindings failed to build", cwd=pyside_build_path)

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

		# Newer versions of PySide don't link to libclang in a way that works after the build, copy over
		# the correct version of libclang
		shutil.copy(os.path.join(llvm_dir, "bin", "libclang.dll"), os.path.join(pyside_install_path, "site-packages", "shiboken6_generator", "libclang.dll"), follow_symlinks=False)
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


if args.symbols:
	if sys.platform == 'darwin':
		print("\nExtracting debug symbols...")
		dsym_files = []
		strip_files = []
		for root, dirs, files in os.walk(install_path):
			for file in files:
				file_path = os.path.join(root, file)
				if mac_should_strip(file_path):
					strip_files.append(file_path)
					if not file.endswith('.a'):
						dsym_files.append(file_path)

		with zipfile.ZipFile(artifact_path / qt_symbols_artifact_name, 'w', zipfile.ZIP_DEFLATED) as z:
			for f in dsym_files:
				print(f"Processing {f}...")
				dsym_path = f + ".dSYM"
				if subprocess.call(["dsymutil", "-o", dsym_path, f]) != 0:
					print(f"Failed to generate dSYM from {f}")
					sys.exit(1)
				for i in glob.glob(dsym_path + "/**/*", recursive=True):
					if os.path.isfile(i) and should_package_file(os.path.basename(i)):
						z.write(i, os.path.relpath(i, install_path))
				shutil.rmtree(dsym_path)

		print("\nStripping debug info...")
		for f in strip_files:
			if subprocess.call(["strip", "-S", f]) != 0:
				print(f"Failed to strip debug info from {f}")
				sys.exit(1)
			print(f"Stripped debug info from {f}")

	elif sys.platform == 'linux':
		print("\nExtracting debug symbols...")
		symbol_files = []
		strip_files = []
		for root, dirs, files in os.walk(install_path):
			for file in files:
				file_path = os.path.join(root, file)
				if os.path.islink(file_path):
					continue
				if not os.path.isfile(file_path):
					continue
				if file.endswith('.o'):
					continue
				header = open(file_path, 'rb').read(7)
				if header[:4] == b"\x7fELF":
					strip_files.append(file_path)
					symbol_files.append(file_path)
				elif header == b"!<arch>" and file.endswith('.a'):
					strip_files.append(file_path)

		with zipfile.ZipFile(artifact_path / qt_symbols_artifact_name, 'w', zipfile.ZIP_DEFLATED) as z:
			for f in symbol_files:
				debug_file = f + ".debug"
				if subprocess.call(["objcopy", "--only-keep-debug",
						"--compress-debug-sections=zlib", f, debug_file]) != 0:
					print(f"Failed to extract debug symbols from {f}")
					sys.exit(1)

				# Re-inject .eh_frame data from the original binary
				with tempfile.TemporaryDirectory() as tmp:
					remove_args = []
					add_args = []
					for section in [".eh_frame", ".eh_frame_hdr"]:
						dump = os.path.join(tmp, section.lstrip("."))
						subprocess.run(["objcopy", "--dump-section",
							f"{section}={dump}", f],
							capture_output=True)
						if not os.path.exists(dump):
							continue
						remove_args += ["--remove-section", section]
						add_args += ["--add-section", f"{section}={dump}"]
					if remove_args:
						subprocess.run(["objcopy"] + remove_args + [debug_file], check=True)
						subprocess.run(["objcopy"] + add_args + [debug_file], check=True)

				z.write(debug_file, os.path.relpath(debug_file, install_path))
				os.remove(debug_file)

		print("\nStripping debug info...")
		for f in strip_files:
			if subprocess.call(["strip", "--strip-debug", f]) != 0:
				print(f"Failed to strip debug info from {f}")
				sys.exit(1)
			print(f"Stripped debug info from {f}")

	elif sys.platform == 'win32':
		print("\nCollecting debug symbols...")
		with zipfile.ZipFile(artifact_path / qt_symbols_artifact_name, 'w', zipfile.ZIP_DEFLATED) as z:
			# PDBs from the build directory
			for pdb in glob.glob(str(build_path) + '/**/*.pdb', recursive=True):
				rel = os.path.relpath(pdb, build_path)
				parts = rel.replace('\\', '/').split('/')
				# Ignore intermediate PDBs that the compiler generates. We only care about linker PDBs.
				if 'CMakeFiles' in parts or 'config.tests' in parts or parts[:2] == ['qtbase', 'lib']:
					continue
				z.write(pdb, rel)
				print(f"Added {pdb}")
			# PDBs from the install directory (remove after archiving)
			for pdb in glob.glob(str(install_path) + '/**/*.pdb', recursive=True):
				z.write(pdb, os.path.relpath(pdb, install_path))
				os.remove(pdb)
				print(f"Added {pdb}")


# Create modified libraries that contain the correct rpath for bundling. These will be signed separately
# so that each bundle does not need to re-sign the libraries.
if sys.platform == 'darwin':
	os.mkdir(bundle_path)
	for plugin_type in MACOS_PLUGIN_TYPES:
		os.mkdir(os.path.join(bundle_path, plugin_type))
	if args.pyside:
		os.mkdir(os.path.join(bundle_path, "PySide6"))

	for plugin_type in MACOS_PLUGIN_TYPES:
		for f in glob.glob(os.path.join(install_path, "plugins", plugin_type, "*.dylib")):
			target = os.path.join(bundle_path, plugin_type, os.path.basename(f))
			shutil.copy(f, target)
			run_checked(["install_name_tool", "-delete_rpath", "@loader_path/../../lib", target], f"Failed to remove rpath from {target}")
			run_checked(["install_name_tool", "-add_rpath", "@loader_path/../../../Frameworks", target], f"Failed to add framework rpath to {target}")

	if args.pyside:
		for f in glob.glob(os.path.join(pyside_install_path, "site-packages", "PySide6", "*.so")):
			target = os.path.join(bundle_path, "PySide6", os.path.basename(f))
			shutil.copy(f, target)
			run_checked(["install_name_tool", "-delete_rpath", "@loader_path/Qt/lib", target], f"Failed to remove rpath from {target}")
			run_checked(["install_name_tool", "-add_rpath", "@loader_path/../../../Frameworks", target], f"Failed to add framework rpath to {target}")

		for f in glob.glob(os.path.join(pyside_install_path, "site-packages", "PySide6", "*.dylib")):
			target = os.path.join(bundle_path, "PySide6", os.path.basename(f))
			shutil.copy(f, target)
			run_checked(["install_name_tool", "-delete_rpath", "@loader_path/Qt/lib", target], f"Failed to remove rpath from {target}")
			run_checked(["install_name_tool", "-add_rpath", "@loader_path/../../../Frameworks", target], f"Failed to add framework rpath to {target}")
elif sys.platform == 'linux':
	os.mkdir(bundle_path)
	for plugin_type in LINUX_PLUGIN_TYPES:
		os.mkdir(os.path.join(bundle_path, plugin_type))
	if args.pyside:
		os.mkdir(os.path.join(bundle_path, "PySide6"))

	for plugin_type in LINUX_PLUGIN_TYPES:
		for f in glob.glob(os.path.join(install_path, "plugins", plugin_type, "*.so")):
			target = os.path.join(bundle_path, plugin_type, os.path.basename(f))
			shutil.copy(f, target)
			run_checked(["patchelf", "--set-rpath", "$ORIGIN/../..", target], f"ERROR: Failed to change rpath in {target}")

	if args.pyside:
		qt_major_minor_version = ".".join(qt_version.split(".")[0:2])
		for f in glob.glob(os.path.join(pyside_install_path, "site-packages", "PySide6", "*.abi3.so")):
			target = os.path.join(bundle_path, "PySide6", os.path.basename(f))
			shutil.copy(f, target)
			run_checked(["patchelf", "--set-rpath", "$ORIGIN:$ORIGIN/../shiboken6:$ORIGIN/../..", target], f"Failed to change rpath in {target}")

		for f in glob.glob(os.path.join(pyside_install_path, "site-packages", "PySide6", f"libpyside6*.so.{qt_major_minor_version}")):
			target = os.path.join(bundle_path, "PySide6", os.path.basename(f))
			shutil.copy(f, target)
			run_checked(["patchelf", "--set-rpath", "$ORIGIN:$ORIGIN/../shiboken6:$ORIGIN/../..", target], f"Failed to change rpath in {target}")


if args.sign:
	step("sign staged outputs")
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


step("package artifacts")
print("\nCreating archive...")
with zipfile.ZipFile(artifact_path / qt_artifact_name, 'w', zipfile.ZIP_DEFLATED) as z:
	for root, dirs, files in os.walk(install_path):
		relpath = Path(root).resolve().relative_to(install_path.resolve())
		relpath_parts = [] if relpath == Path('.') else [str(relpath)]
		for dir in dirs:
			file_path = Path(root) / dir
			arc_name = os.path.join(qt_archive_root, *relpath_parts, dir)
			if file_path.is_symlink():
				info = zipfile.ZipInfo(arc_name, datetime.datetime.now().timetuple())
				info.compress_type = zipfile.ZIP_DEFLATED
				info.external_attr = ZIP_SYMLINK_ATTR
				z.writestr(info, os.readlink(file_path))
		for file in files:
			if not should_package_file(file):
				continue
			print(f"Adding {relpath}/{file}...")
			file_path = Path(root) / file
			arc_name = os.path.join(qt_archive_root, *relpath_parts, file)
			info = zipfile.ZipInfo(arc_name, datetime.datetime.now().timetuple())
			info.compress_type = zipfile.ZIP_DEFLATED

			if file_path.is_symlink():
				info.external_attr = ZIP_SYMLINK_ATTR
				z.writestr(info, os.readlink(file_path))
			else:
				if os.access(file_path, os.X_OK):
					info.external_attr = ZIP_EXECUTABLE_ATTR
				else:
					info.external_attr = ZIP_REGULAR_FILE_ATTR

				with file_path.open('rb') as f:
					z.writestr(info, f.read())


if args.install:
	step("install locally/deploy if requested")
	install_staged_output(install_path, user_qt_parent_path)


if args.clean:
	step("cleanup")
	print("Cleaning up...")
	remove_dir(source_path)
