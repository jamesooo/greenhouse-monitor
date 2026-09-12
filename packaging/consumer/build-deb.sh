#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-1.0.0}"
REVISION="${2:-3}"
PACKAGE_NAME=greenhouse-datastore
PACKAGE_VERSION="${VERSION}-${REVISION}"
PACKAGE_ROOT="${ROOT_DIR}/build/consumer/${PACKAGE_NAME}_${PACKAGE_VERSION}_all"
OUTPUT_DIR="${ROOT_DIR}/dist"
OUTPUT_FILE="${OUTPUT_DIR}/${PACKAGE_NAME}_${PACKAGE_VERSION}_all.deb"

if ! command -v dpkg-deb >/dev/null 2>&1; then
	echo "dpkg-deb is required to build the package" >&2
	exit 1
fi

rm -rf "${ROOT_DIR}/build/consumer"
mkdir -p \
	"$PACKAGE_ROOT/DEBIAN" \
	"$PACKAGE_ROOT/etc/greenhouse-datastore" \
	"$PACKAGE_ROOT/lib/systemd/system" \
	"$PACKAGE_ROOT/opt/greenhouse-datastore/src" \
	"$PACKAGE_ROOT/usr/bin" \
	"$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME" \
	"$OUTPUT_DIR"

cp -R "$ROOT_DIR/consumer/greenhouse_datastore" \
	"$PACKAGE_ROOT/opt/greenhouse-datastore/src/"
cp "$ROOT_DIR/consumer/pyproject.toml" \
	"$PACKAGE_ROOT/opt/greenhouse-datastore/src/"
cp "$ROOT_DIR/consumer/README.md" \
	"$PACKAGE_ROOT/usr/share/doc/$PACKAGE_NAME/README.md"
cp "$ROOT_DIR/packaging/consumer/requirements.txt" \
	"$PACKAGE_ROOT/opt/greenhouse-datastore/src/requirements.txt"
cp "$ROOT_DIR/consumer/greenhouse-datastore.env" \
	"$PACKAGE_ROOT/etc/greenhouse-datastore/greenhouse-datastore.env"
cp "$ROOT_DIR/consumer/greenhouse-datastore.service" \
	"$PACKAGE_ROOT/lib/systemd/system/greenhouse-datastore.service"
cp "$ROOT_DIR/packaging/consumer/debian/"* "$PACKAGE_ROOT/DEBIAN/"

find "$PACKAGE_ROOT" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$PACKAGE_ROOT" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete

ln -s /opt/greenhouse-datastore/venv/bin/greenhouse-datastore \
	"$PACKAGE_ROOT/usr/bin/greenhouse-datastore"

chmod 600 "$PACKAGE_ROOT/etc/greenhouse-datastore/greenhouse-datastore.env"
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
Description: Greenhouse MQTT TimescaleDB consumer
 Stores greenhouse climate and light MQTT readings in PostgreSQL TimescaleDB
 hypertables using an administrator-provisioned database connection.
EOF

dpkg-deb --root-owner-group --build "$PACKAGE_ROOT" "$OUTPUT_FILE"
echo "Built ${OUTPUT_FILE}"