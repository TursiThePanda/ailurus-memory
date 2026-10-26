#!/usr/bin/env bash
# Build the portable archives for a release that has already been built.
#
# Portable is not a separate build: it is the SAME binary the installer ships, packed
# next to an empty `ai-memory.portable` marker. paths.py switches on that file and keeps
# the database, the config and the logs in a `data` folder beside the executable
# (paths.py:portable_dir). So this script compiles nothing -- it stages, documents and
# packs what releases/v<version>/ already contains, which is why it runs on Linux even
# for the Windows archive.
#
#   ./packaging/make-portable.sh [version]      default: the version in ai_memory/core/version.py
#
# The release workflow (.github/workflows/release.yml) runs this same script on the
# binaries CI just built, so a local pack and a CI pack cannot drift apart. Archive
# names carry the outside name, Ailurus Memory; the program inside stays `ai-memory`.
#
# It also rewrites SHA256SUMS for the whole release directory, because adding files to a
# release and leaving its checksum file describing the old contents is worse than having
# no checksum file at all.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."   # this script lives in packaging/

V="${1:-$(sed -n 's/^__version__ = "\(.*\)"/\1/p' ai_memory/core/version.py)}"
REL="releases/v$V"
[ -d "$REL" ] || { echo "no $REL -- build the release first"; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# The user README is markdown; the shipped README.txt is the same text with the bold
# markers taken out, the way the installed one is made.
md2txt() { sed -e 's/\*\*//g' -e '1s/^# /  /' "$1"; }

# ---- Windows -------------------------------------------------------------------
if [ -f "$REL/windows/ai-memory.exe" ]; then
    # One staging dir PER PLATFORM: both archives unpack to the same folder name, so a
    # shared one would put the .exe into the Linux tar.
    D="$STAGE/windows/ailurus-memory-$V-portable"
    mkdir -p "$D"
    cp "$REL/windows/ai-memory.exe" "$D/"
    : > "$D/ai-memory.portable"
    md2txt docs/install/windows-portable.md > "$D/README.txt"
    # -X: no .DS_Store-style extra attributes; the archive should be the same bytes on
    # any machine that packs the same files.
    (cd "$STAGE/windows" && zip -qrX "ailurus-memory-$V-windows-portable.zip" "ailurus-memory-$V-portable")
    mv "$STAGE/windows/ailurus-memory-$V-windows-portable.zip" "$REL/windows/"
    echo "windows/ailurus-memory-$V-windows-portable.zip"
else
    echo "skipped windows: no $REL/windows/ai-memory.exe"
fi

# ---- Linux ---------------------------------------------------------------------
if [ -f "$REL/linux/ai-memory" ]; then
    D="$STAGE/linux/ailurus-memory-$V-portable"
    mkdir -p "$D"
    cp "$REL/linux/ai-memory" "$D/"
    chmod 755 "$D/ai-memory"
    : > "$D/ai-memory.portable"
    sed "s/VERSION/$V/g" docs/install/linux-portable.txt > "$D/README.txt"
    tar czf "$REL/linux/ailurus-memory-$V-linux-portable.tar.gz" -C "$STAGE/linux" "ailurus-memory-$V-portable"
    echo "linux/ailurus-memory-$V-linux-portable.tar.gz"
else
    echo "skipped linux: no $REL/linux/ai-memory"
fi

# ---- Checksums for everything in the release ------------------------------------
# Only the directories that exist: `find` on a missing one exits non-zero, and under
# pipefail that killed the script after packing everything (CI has no source/ dir).
DIRS=()
for d in linux windows source; do [ -d "$REL/$d" ] && DIRS+=("$d"); done
(cd "$REL" && find "${DIRS[@]}" -type f ! -name SHA256SUMS -print0 \
    | sort -z | xargs -0 sha256sum > SHA256SUMS)
echo "$REL/SHA256SUMS rewritten ($(wc -l < "$REL/SHA256SUMS") files)"
