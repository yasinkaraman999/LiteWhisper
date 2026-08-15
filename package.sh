#!/bin/bash
#
# Builds LiteWhisper.app and wraps it in a drag-to-Applications disk image.
#
#   ./package.sh
#
# Output: dist/LiteWhisper-<version>.dmg
#
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "venv/ not found. Create it and install requirements.txt first." >&2
    exit 1
fi

source venv/bin/activate

VERSION=$(python -c "
import re, pathlib
text = pathlib.Path('setup.py').read_text()
print(re.search(r'\"CFBundleShortVersionString\":\s*\"([^\"]+)\"', text).group(1))
")
echo "==> Building LiteWhisper $VERSION"

echo "==> Cleaning"
# A disk image from an earlier build keeps its own file busy while it stays
# mounted, and Finder recreates .DS_Store inside a folder even as it is being
# deleted. Either one makes a plain "rm -rf dist" fail with
# "Directory not empty", so unmount first and give the retry a moment.
for volume in /Volumes/LiteWhisper*; do
    [ -d "$volume" ] && hdiutil detach "$volume" -force >/dev/null 2>&1 || true
done
for _ in 1 2 3; do
    rm -rf build dist 2>/dev/null && break
    sleep 1
done
# Last attempt without the guard, so a genuine failure stops the build.
rm -rf build dist

# py2app reuses stale intermediates, which is how half-updated bundles and
# bogus codesign failures happen. Always start clean.
python setup.py py2app >/dev/null

echo "==> Signing"
# Ad-hoc signature. Enough for this machine; see README for what other Macs need.
codesign --force --deep --sign - dist/LiteWhisper.app

echo "==> Staging disk image"
STAGE=build/dmg
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R dist/LiteWhisper.app "$STAGE/"
# The Applications symlink is what makes the window a drag-and-drop target.
ln -s /Applications "$STAGE/Applications"

DMG="dist/LiteWhisper-$VERSION.dmg"
rm -f "$DMG"
hdiutil create \
    -volname "LiteWhisper" \
    -srcfolder "$STAGE" \
    -ov -format UDZO \
    "$DMG" >/dev/null

rm -rf "$STAGE"

echo "==> Done: $DMG ($(du -h "$DMG" | cut -f1))"
