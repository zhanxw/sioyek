#!/usr/bin/env bash
# Build a native, self-contained macOS app and sign it BEFORE creating the DMG.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
ARCH="${SIOYEK_ARCH:-$(uname -m)}"
case "$ARCH" in arm64|x86_64) ;; *) echo "Unsupported architecture: $ARCH" >&2; exit 1;; esac
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-$(sw_vers -productVersion)}"
JOBS="${MAKE_PARALLEL:-$(sysctl -n hw.logicalcpu)}"
QMAKE="${QMAKE:-qmake}"
QT_BIN="$($QMAKE -query QT_INSTALL_BINS)"
case "$($QMAKE -query QT_VERSION)" in 6.*) ;; *) echo 'Qt 6 is required.' >&2; exit 1;; esac
[[ -f mupdf/thirdparty/freetype/Makefile ]] || { echo 'Run git submodule update --init --recursive first.' >&2; exit 1; }
# Separate MuPDF output by architecture; never accidentally link stale Intel objects.
make -C mupdf -j"$JOBS" build=release "OUT=build/mac-$ARCH" \
    "XCFLAGS=-arch $ARCH -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET" \
    HAVE_GLUT=no HAVE_X11=no HAVE_LIBCRYPTO=no USE_SYSTEM_LIBS=no libs libmupdf-threads
mkdir -p "build/mac-$ARCH"
cd "build/mac-$ARCH"
# Regenerate the bundle so old SDK libraries cannot survive an incremental build.
rm -rf sioyek.app
"$QMAKE" "$ROOT/pdf_viewer_build_config.pro" CONFIG+=release CONFIG+=non_portable \
    "QMAKE_APPLE_DEVICE_ARCHS=$ARCH" \
    "QMAKE_MACOSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET" \
    "MUPDF_LIB_DIR=$ROOT/mupdf/build/mac-$ARCH"
make -j"$JOBS"
APP="$PWD/sioyek.app"
cp -R "$ROOT/pdf_viewer/shaders" "$APP/Contents/Resources/"
for resource in prefs.config prefs_user.config keys.config keys_user.config; do
    cp "$ROOT/pdf_viewer/$resource" "$APP/Contents/Resources/"
done
cp "$ROOT/tutorial.pdf" "$APP/Contents/Resources/"
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion $MACOSX_DEPLOYMENT_TARGET" "$APP/Contents/Info.plist"
"$QT_BIN/macdeployqt" "$APP" -qmldir="$ROOT/pdf_viewer/touchui" \
    -libpath="$($QMAKE -query QT_INSTALL_LIBS)" -appstore-compliant -codesign=-
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
lipo "$APP/Contents/MacOS/sioyek" -verify_arch "$ARCH"
# Stage only the app and the Applications shortcut, not build intermediates.
STAGE=$(mktemp -d "$ROOT/build/dmg-stage.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/sioyek.app"
ln -s /Applications "$STAGE/Applications"
DMG="$ROOT/build/sioyek-macos-$ARCH.dmg"
hdiutil create -ov -volname Sioyek -srcfolder "$STAGE" -format UDZO "$DMG"
hdiutil verify "$DMG"
(cd "$ROOT/build" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
# Keep the archive expected by the existing upstream multi-platform workflows.
zip -j -FS "$ROOT/sioyek-release-mac.zip" "$DMG"
echo "Built $DMG"
