# Binary Ninja's Qt Builds

[Binary Ninja](https://binary.ninja/) uses Qt and PySide for its user interface and related plugins. This repository includes all of the patches and build infrastructure that we use to make the files we include with the product for all of our supported release platforms.


## Prerequisites

The build process is managed by *[mise-en-place](https://mise.en.dev/)* (`mise`) and is the primary requirement for builds. See [this page](https://mise.en.dev/installing-mise.html) for installation options.

You will also need `msvc` (Windows), `clang` (macOS), or `gcc` (Linux) to compile.

Finally, you will also need our custom copy of `libclang`, which can be build from [this repository](https://github.com/Vector35/llvm-build). If you are a Vector 35 employee, you can get a pre-built copy from our internal build server.


## Quick Start

Run the `mise` task, review the printed configuration, and confirm the prompt:

```sh
mise build
```

The `mise` task invokes `build.py` through `uv` using the managed Python toolchain. By default, installable output is copied to `$QT_INSTALL_DIR/<version>` or `~/Qt/<version>`. Use `--no-install` to skip the local install step.

You can pass build options after `--` so they are forwarded to `build.py` rather than parsed by `mise`:

```sh
mise build -- --no-prompt --no-install
```

### Supported Arguments

- `--no-clone`: Reuse existing source checkout
- `--clean` / `--no-clean`: Clean up before building
- `--prompt` / `--no-prompt`: Interactive confirmation
- `--install` / `--no-install`: Local installation
- `--sign` / `--no-sign`: Signing
- `--mirror <url>`: Use a source mirror
- `--build-dir <path>`: Use a custom build directory
- `-j, --jobs <n>`: Set POSIX build parallelism level
- `--debug`, `--asan`, `--tsan`: Select a build variant
- `--universal`: Build both x86_64 and arm64 on supported macOS hosts
- `--qt-source <path>` / `--pyside-source <path>`: Use provided source directories instead of cloning
- `--patch <path>`: Apply an additional patch
- `--no-pyside`: Skip building PySide
- `--symbols` / `--no-symbols`: Control symbol archive generation

### Environment Variables

CLI arguments override environment defaults. Boolean values accept `1`, `true`, `yes`, `on`, `y`, `0`, `false`, `no`, `off`, or `n` case-insensitively. Secret-like values are redacted in `artifacts/build-metadata.json`.

| Variable | Purpose |
| --- | --- |
| `BUILD_DIR` | Default for `--build-dir`. Defaults to `build` under the repo. |
| `ARTIFACTS_DIR` | Artifact output directory. Defaults to `artifacts` under the repo. |
| `SOURCE_MIRROR` | Default for `--mirror`. |
| `JOBS` | Default for `-j/--jobs` on POSIX. |
| `SIGN` | Default for `--sign`. Use `--no-sign` to override. |
| `NO_INSTALL` | Default equivalent of `--no-install`. Use `--install` to override. |
| `NO_PROMPT` | Default equivalent of `--no-prompt`. Use `--prompt` to override. |
| `CLEAN` | Sets whether to clean before building. Use `--clean` or `--no-clean` to override. |
| `BUILD_VARIANT` | Default build variant: `release`, `debug`, `asan`, or `tsan`. CLI variant flags override it. |
| `QT_INSTALL_DIR` | Local install destination parent for Qt when installation is enabled. |
| `LLVM_INSTALL_DIR` | Location of the `libclang` dependency used to build PySide. Default is `~/libclang` and files are expected in `~/libclang/<version>`. |
| `YUBIKEY_PIN` | Windows signing PIN used when signing is enabled. |


## Build Output

Artifacts are written under `artifacts/` unless `ARTIFACTS_DIR` overrides the directory.

| Artifact | Contents |
| --- | --- |
| `qt_<platform>_<version>.zip` | Qt tree rooted at `Qt/<version>` |
| `qt_symbols_<platform>_<version>.zip` | Separate debug symbols when symbol extraction is enabled |

Build metadata is written to `artifacts/build-metadata.json` and includes the resolved configuration, artifact names, internal roots, and redacted secret-like values.
