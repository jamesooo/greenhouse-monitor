#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-1.0.0}"
REVISION="${2:-1}"
PACKAGE_NAME=greenhouse-analyzer
PACKAGE_VERSION="${VERSION}-${REVISION}"
PACKAGE_ROOT="${ROOT_DIR}/build/analyzer/${PACKAGE_NAME}_${PACKAGE_VERSION}_all"
OUTPUT_DIR="${ROOT_DIR}/dist"
OUTPUT_FILE="${OUTPUT_DIR}/${PACKAGE_NAME}_${PACKAGE_VERSION}_all.deb"

if ! command -v dpkg-deb >/dev/null 2>&1; then
	echo "dpkg-deb is required to build the package" >&2
	exit 1
fi

rm -rf "${ROOT_DIR}/build/analyzer"
mkdir -p \
	"$PACKAGE_ROOT/DEBIAN" \
	"$PACKAGE_ROOT/etc/greenhouse-analyzer" \
	"$PACKAGE_ROOT/etc/greenhouse-analyzer/skills/greenhouse-interpretation" \
	"$PACKAGE_ROOT/lib/systemd/system" \
	"$PACKAGE_ROOT/opt/greenhouse-analyzer/src" \
	"$PACKAGE_ROOT/usr/bin" \
	"$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME" \
	"$OUTPUT_DIR"

cp -R "$ROOT_DIR/analyzer/greenhouse_analyzer" \
	"$PACKAGE_ROOT/opt/greenhouse-analyzer/src/"
cp "$ROOT_DIR/analyzer/pyproject.toml" \
	"$PACKAGE_ROOT/opt/greenhouse-analyzer/src/"
cp "$ROOT_DIR/analyzer/README.md" \
	"$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/README.md"
cp "$ROOT_DIR/packaging/analyzer/requirements.txt" \
	"$PACKAGE_ROOT/opt/greenhouse-analyzer/src/requirements.txt"
cp "$ROOT_DIR/analyzer/greenhouse-analyzer.env" \
	"$PACKAGE_ROOT/etc/greenhouse-analyzer/greenhouse-analyzer.env"
cp "$ROOT_DIR/analyzer/prompt.txt" \
	"$PACKAGE_ROOT/etc/greenhouse-analyzer/prompt.txt"
cp "$ROOT_DIR/analyzer/skills/greenhouse-interpretation/SKILL.md" \
	"$PACKAGE_ROOT/etc/greenhouse-analyzer/skills/greenhouse-interpretation/SKILL.md"
cp "$ROOT_DIR/analyzer/pelicanconf.py" \
	"$PACKAGE_ROOT/etc/greenhouse-analyzer/pelicanconf.py"
cp -R "$ROOT_DIR/analyzer/site" \
	"$PACKAGE_ROOT/opt/greenhouse-analyzer/"
cp "$ROOT_DIR/analyzer/greenhouse-analyzer.service" \
	"$PACKAGE_ROOT/lib/systemd/system/greenhouse-analyzer.service"
cp "$ROOT_DIR/analyzer/greenhouse-analyzer.timer" \
	"$PACKAGE_ROOT/lib/systemd/system/greenhouse-analyzer.timer"
cp "$ROOT_DIR/analyzer/greenhouse-analysis-site.service" \
	"$PACKAGE_ROOT/lib/systemd/system/greenhouse-analysis-site.service"
cp "$ROOT_DIR/packaging/analyzer/debian/"* "$PACKAGE_ROOT/DEBIAN/"

find "$PACKAGE_ROOT" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$PACKAGE_ROOT" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete

ln -s /opt/greenhouse-analyzer/venv/bin/greenhouse-analyzer \
	"$PACKAGE_ROOT/usr/bin/greenhouse-analyzer"

chmod 640 "$PACKAGE_ROOT/etc/greenhouse-analyzer/greenhouse-analyzer.env"
chmod 640 \
	"$PACKAGE_ROOT/etc/greenhouse-analyzer/skills/greenhouse-interpretation/SKILL.md"
chmod 755 "$PACKAGE_ROOT/DEBIAN/postinst" "$PACKAGE_ROOT/DEBIAN/prerm" \
	"$PACKAGE_ROOT/DEBIAN/postrm"

INSTALLED_SIZE="$(du -sk "$PACKAGE_ROOT" | cut -f1)"
cat > "$PACKAGE_ROOT/DEBIAN/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${PACKAGE_VERSION}
Architecture: all
Maintainer: James Ooo
Installed-Size: ${INSTALLED_SIZE}
Depends: python3 (>= 3.11), python3-venv, python3-pip, ca-certificates
Section: misc
Priority: optional
Description: Daily AI analysis of the greenhouse dashboard
 Renders the greenhouse Grafana dashboard, analyzes it with a Deep Agent using
 a configurable Ollama model, and publishes the result as a Pelican static site.
EOF

dpkg-deb --root-owner-group --build "$PACKAGE_ROOT" "$OUTPUT_FILE"
echo "Built ${OUTPUT_FILE}"