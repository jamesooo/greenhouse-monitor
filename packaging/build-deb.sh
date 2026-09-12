#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-$(tr -d '[:space:]' < "${ROOT_DIR}/VERSION")}"
REVISION="${2:-7}"
PACKAGE_NAME="greenhouse-monitor"
PACKAGE_VERSION="${VERSION}-${REVISION}"
BUILD_ROOT="${ROOT_DIR}/build/debian"
PACKAGE_ROOT="${BUILD_ROOT}/${PACKAGE_NAME}_${PACKAGE_VERSION}_all"
OUTPUT_DIR="${ROOT_DIR}/dist"
OUTPUT_FILE="${OUTPUT_DIR}/${PACKAGE_NAME}_${PACKAGE_VERSION}_all.deb"

if ! command -v dpkg-deb >/dev/null 2>&1; then
	echo "dpkg-deb is required to build the package" >&2
	exit 1
fi

if [[ ! "$VERSION" =~ ^[0-9]+([.][0-9]+)*([+~.-][A-Za-z0-9]+)*$ ]]; then
	echo "Invalid Debian version: ${VERSION}" >&2
	exit 1
fi

rm -rf "$ROOT_DIR/build"
mkdir -p \
	"$PACKAGE_ROOT/DEBIAN" \
	"$PACKAGE_ROOT/etc/greenhouse" \
	"$PACKAGE_ROOT/etc/logrotate.d" \
	"$PACKAGE_ROOT/etc/cron.daily" \
	"$PACKAGE_ROOT/lib/systemd/system" \
	"$PACKAGE_ROOT/opt/greenhouse/src" \
	"$PACKAGE_ROOT/opt/greenhouse/captures" \
	"$PACKAGE_ROOT/usr/bin" \
	"$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME" \
	"$PACKAGE_ROOT/var/lib/greenhouse/captures" \
	"$PACKAGE_ROOT/var/log/greenhouse" \
	"$OUTPUT_DIR"

cp -R "$ROOT_DIR/greenhouse" "$PACKAGE_ROOT/opt/greenhouse/src/"
cp "$ROOT_DIR/pyproject.toml" "$ROOT_DIR/VERSION" \
	"$PACKAGE_ROOT/opt/greenhouse/src/"
cp "$ROOT_DIR/packaging/requirements.txt" \
	"$PACKAGE_ROOT/opt/greenhouse/src/requirements.txt"
cp "$ROOT_DIR/greenhouse/greenhouse.env" \
	"$PACKAGE_ROOT/etc/greenhouse/greenhouse.env"
cp "$ROOT_DIR/greenhouse/greenhouse-monitor.service" \
	"$PACKAGE_ROOT/lib/systemd/system/greenhouse-monitor.service"
cp "$ROOT_DIR/packaging/debian/logrotate" \
	"$PACKAGE_ROOT/etc/logrotate.d/greenhouse-monitor"
cp "$ROOT_DIR/packaging/debian/greenhouse-cleanup" \
	"$PACKAGE_ROOT/etc/cron.daily/greenhouse-cleanup"
cp "$ROOT_DIR/packaging/debian/conffiles" "$PACKAGE_ROOT/DEBIAN/conffiles"
cp "$ROOT_DIR/packaging/debian/postinst" "$PACKAGE_ROOT/DEBIAN/postinst"
cp "$ROOT_DIR/packaging/debian/prerm" "$PACKAGE_ROOT/DEBIAN/prerm"
cp "$ROOT_DIR/packaging/debian/postrm" "$PACKAGE_ROOT/DEBIAN/postrm"

find "$PACKAGE_ROOT" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$PACKAGE_ROOT" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete

ln -s /opt/greenhouse/venv/bin/greenhouse-monitor \
	"$PACKAGE_ROOT/usr/bin/greenhouse-monitor"

chmod 600 "$PACKAGE_ROOT/etc/greenhouse/greenhouse.env"
chmod 755 \
	"$PACKAGE_ROOT/DEBIAN/postinst" \
	"$PACKAGE_ROOT/DEBIAN/prerm" \
	"$PACKAGE_ROOT/DEBIAN/postrm" \
	"$PACKAGE_ROOT/etc/cron.daily/greenhouse-cleanup"

INSTALLED_SIZE="$(du -sk "$PACKAGE_ROOT" | cut -f1)"
cat > "$PACKAGE_ROOT/DEBIAN/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${PACKAGE_VERSION}
Architecture: all
Maintainer: James Ooo
Installed-Size: ${INSTALLED_SIZE}
Depends: python3 (>= 3.11), python3-venv, python3-pip, ca-certificates, v4l-utils
Section: misc
Priority: optional
Description: BLE and optical camera greenhouse monitoring service
 Polls BLE climate sensors, captures optical camera light metrics, and
 publishes readings to MQTT. The service runs in an isolated Python virtual
 environment under /opt/greenhouse.
EOF

dpkg-deb --root-owner-group --build "$PACKAGE_ROOT" "$OUTPUT_FILE"
echo "Built ${OUTPUT_FILE}"
