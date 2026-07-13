qt_version = "6.10.1"
# PySide is built from the 6.11.1 sources in order to add compatiblity
# with LLVM 22, but is patched to be compatible with Qt 6.10.1.
pyside_version = "6.11.1"
llvm_version = "22.1.8"
msvc_build = "14.34"
msvc_dir_name = "msvc2022_64"
vs_version = "2022"
min_macos = "13.0"
qt_modules = ["qtbase", "qtsvg", "qtwayland", "qtimageformats", "qtdeclarative", "qttools", "qttranslations", "qtlanguageserver", "qtshadertools"]
pyside_modules = ["Core", "Gui", "Widgets", "Svg", "DBus", "PrintSupport"]
