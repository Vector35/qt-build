qt-build
--

This repository contains scripts and patches for building Qt and related sub-projects for use in Binary Ninja.

## Build Process
Run the `./build_<platform>` that matches your host's platform. Verify that the build configuration is correct, and type Y to start.

The build will be installed in the directory specified by `$QT_INSTALL_DIR` (or `~/Qt` if unset) unless you pass `--no-install` to the script.

## Requirements

This build process uses [Poetry](https://python-poetry.org/) for dependency management. You'll need to `pip3 install poetry` (or equivalent for your system) to be able to build.

You will also require:

- Compiler
  - macOS: Xcode 13+ (tested, might work on older) 
  - Windows: VS 2019 Professional
  - Linux: GCC 9.4+ (tested, might work on older)
- CMake
- Ninja
- libclang (path can be set with `LLVM_INSTALL_DIR`)
- On Linux, you will need the following packages to build the UI components of Qt:
  - `libfontconfig1-dev libfreetype6-dev libx11-dev libx11-xcb-dev libxext-dev libxfixes-dev libxi-dev libxrender-dev libxcb1-dev libxcb-glx0-dev libxcb-keysyms1-dev libxcb-image0-dev libxcb-shm0-dev libxcb-icccm4-dev libxcb-sync0-dev libxcb-xfixes0-dev libxcb-shape0-dev libxcb-randr0-dev libxcb-render-util0-dev libxcb-xinerama0-dev libxkbcommon-dev libxkbcommon-x11-dev libwayland-dev`

## Using provided source bundle
You can also use the source bundle referenced in the [documentation](https://docs.binary.ninja/about/open-source.html#building-qt) to build Qt. Download and extract the source bundle referenced in the documentation under the "Building Qt" section, then run the following command:

`./build_<platform> --qt-source <qt-source-path> --pyside-source <pyside-source-path>`
