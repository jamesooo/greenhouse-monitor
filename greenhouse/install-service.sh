#!/bin/bash
# Greenhouse Monitor Service Installation Script
# Run as root: sudo ./install-service.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Greenhouse Monitor Service Installer ===${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    echo "Usage: sudo ./install-service.sh"
    exit 1
fi

# Configuration
INSTALL_DIR="/opt/greenhouse"
CONFIG_DIR="/etc/greenhouse"
VENV_DIR="${INSTALL_DIR}/venv"
CAPTURES_DIR="${INSTALL_DIR}/captures"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${SCRIPT_DIR}/.."

echo -e "${YELLOW}Installation directory: ${INSTALL_DIR}${NC}"
echo -e "${YELLOW}Config directory: ${CONFIG_DIR}${NC}"

# Create directories
echo -e "\n${GREEN}Creating directories...${NC}"
mkdir -p "${INSTALL_DIR}"
mkdir -p "${CONFIG_DIR}"
mkdir -p "${CAPTURES_DIR}"

# Create Python virtual environment if it doesn't exist
if [ ! -d "${VENV_DIR}" ]; then
    echo -e "${GREEN}Creating Python virtual environment...${NC}"
    python3 -m venv "${VENV_DIR}"
fi

# Install dependencies and the package
echo -e "${GREEN}Installing pysenxor and greenhouse-monitor...${NC}"
"${VENV_DIR}/bin/pip" install --upgrade pip

# Install the full package (includes senxor + greenhouse + console script)
if [ -f "${REPO_DIR}/setup.py" ]; then
    echo -e "${GREEN}Installing from local source...${NC}"
    "${VENV_DIR}/bin/pip" install -e "${REPO_DIR}"
else
    echo -e "${YELLOW}No setup.py found, installing dependencies only...${NC}"
    "${VENV_DIR}/bin/pip" install \
        bleak \
        paho-mqtt \
        opencv-python-headless \
        numpy \
        pyserial
fi

# Verify greenhouse-monitor is available
if "${VENV_DIR}/bin/greenhouse-monitor" --help > /dev/null 2>&1; then
    echo -e "${GREEN}greenhouse-monitor command installed successfully${NC}"
else
    echo -e "${YELLOW}Warning: greenhouse-monitor command not found in venv${NC}"
fi

# Install environment file (don't overwrite if exists)
if [ ! -f "${CONFIG_DIR}/greenhouse.env" ]; then
    echo -e "${GREEN}Installing default environment file...${NC}"
    cp "${SCRIPT_DIR}/greenhouse.env" "${CONFIG_DIR}/greenhouse.env"
    chmod 600 "${CONFIG_DIR}/greenhouse.env"
    echo -e "${YELLOW}>>> Edit ${CONFIG_DIR}/greenhouse.env with your settings${NC}"
else
    echo -e "${YELLOW}Environment file already exists, not overwriting${NC}"
    echo -e "${YELLOW}>>> Check ${SCRIPT_DIR}/greenhouse.env for new options${NC}"
fi

# Install systemd service
echo -e "${GREEN}Installing systemd service...${NC}"
cp "${SCRIPT_DIR}/greenhouse-monitor.service" /etc/systemd/system/
systemctl daemon-reload

# Set up log rotation
echo -e "${GREEN}Setting up log rotation...${NC}"
cat > /etc/logrotate.d/greenhouse-monitor << 'EOF'
/var/log/greenhouse/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    create 0640 root root
    sharedscripts
    postrotate
        systemctl reload greenhouse-monitor 2>/dev/null || true
    endscript
}
EOF

# Create a cleanup cron job for old images (optional)
echo -e "${GREEN}Setting up image cleanup cron job...${NC}"
cat > /etc/cron.daily/greenhouse-cleanup << EOF
#!/bin/bash
# Remove images older than 7 days
find ${CAPTURES_DIR} -name "*.jpg" -mtime +7 -delete 2>/dev/null || true
EOF
chmod +x /etc/cron.daily/greenhouse-cleanup

echo -e "\n${GREEN}=== Installation Complete ===${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. ${YELLOW}Edit configuration:${NC}"
echo -e "     sudo nano ${CONFIG_DIR}/greenhouse.env"
echo ""
echo -e "  2. ${YELLOW}Enable and start the service:${NC}"
echo -e "     sudo systemctl enable greenhouse-monitor"
echo -e "     sudo systemctl start greenhouse-monitor"
echo ""
echo -e "  3. ${YELLOW}Check service status:${NC}"
echo -e "     sudo systemctl status greenhouse-monitor"
echo -e "     sudo journalctl -u greenhouse-monitor -f"
echo ""
echo -e "  4. ${YELLOW}Test manually first (optional):${NC}"
echo -e "     ${VENV_DIR}/bin/python ${INSTALL_DIR}/greenhouse_monitor.py --help"
echo ""
echo -e "${GREEN}USB Device IDs:${NC}"
echo -e "  Find your camera USB IDs with: lsusb -t"
echo -e "  Update GREENHOUSE_OPTICAL_USB_ID and GREENHOUSE_THERMAL_USB_ID in the env file"
echo ""
echo -e "${GREEN}BLE Addresses:${NC}"
echo -e "  Find your BLE sensor addresses with: bluetoothctl scan on"
echo -e "  Update GREENHOUSE_BLE_ADDRESSES in the env file"
