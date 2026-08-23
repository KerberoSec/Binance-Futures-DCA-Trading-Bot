#!/usr/bin/env bash
# ====================================================================================================
# Futures DCA Trading Bot - Automated VPS Deployment & Systemd Setup Script
# Author: Arun Kumar
# LinkedIn: https://www.linkedin.com/in/arunkumar31072006/
# GitHub: https://github.com/KerberoSec
# Instagram: https://www.instagram.com/so_far_from_your_heart/
# X / Twitter: https://x.com/ArunKumar310706
# ====================================================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

SERVICE_NAME="dca-bot"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$(whoami)"

echo -e "${CYAN}========================================================================${NC}"
echo -e "${CYAN}  Futures DCA Trading Bot - Automated VPS & Systemd Setup Engine        ${NC}"
echo -e "${CYAN}========================================================================${NC}"
echo ""

# 1. Root / Sudo Check
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}[!] Note: Running as non-root user (${CURRENT_USER}). Sudo privileges will be used for system configuration.${NC}"
    SUDO="sudo"
else
    SUDO=""
    if [ -n "$SUDO_USER" ]; then
        CURRENT_USER="$SUDO_USER"
    fi
fi

# 2. Update System Packages
echo -e "${CYAN}[1/6] Updating system repositories & installing prerequisites...${NC}"
$SUDO apt-get update -y
$SUDO apt-get install -y python3 python3-pip python3-venv git curl ufw fail2ban tzdata

# 3. Virtual Environment Setup
echo -e "${CYAN}[2/6] Setting up Python virtual environment in ${CURRENT_DIR}/venv...${NC}"
if [ ! -d "${CURRENT_DIR}/venv" ]; then
    python3 -m venv "${CURRENT_DIR}/venv"
fi

# Activate and install dependencies
source "${CURRENT_DIR}/venv/bin/activate"
pip install --upgrade pip
pip install -r "${CURRENT_DIR}/requirements.txt"

# 4. Environment Configuration Check
echo -e "${CYAN}[3/6] Verifying environment configuration (.env)...${NC}"
if [ ! -f "${CURRENT_DIR}/.env" ]; then
    if [ -f "${CURRENT_DIR}/.env.example" ]; then
        cp "${CURRENT_DIR}/.env.example" "${CURRENT_DIR}/.env"
        echo -e "${YELLOW}[!] Created .env from .env.example. Please ensure your BINANCE_API_KEY and BINANCE_API_SECRET are configured!${NC}"
    else
        echo -e "${RED}[ERROR] Neither .env nor .env.example found in ${CURRENT_DIR}!${NC}"
        exit 1
    fi
fi

# Ensure appropriate permissions on .env
chmod 600 "${CURRENT_DIR}/.env"

# 5. Create Systemd Service (Logs directly to bot.log)
echo -e "${CYAN}[4/6] Creating Systemd service definition (/etc/systemd/system/${SERVICE_NAME}.service)...${NC}"
SERVICE_FILE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

$SUDO bash -c "cat <<EOF > ${SERVICE_FILE_PATH}
[Unit]
Description=Futures DCA Trading Bot Background Daemon
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${CURRENT_DIR}
EnvironmentFile=${CURRENT_DIR}/.env
ExecStart=${CURRENT_DIR}/venv/bin/python ${CURRENT_DIR}/code.py
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=30
StandardOutput=append:${CURRENT_DIR}/bot.log
StandardError=append:${CURRENT_DIR}/bot.log

[Install]
WantedBy=multi-user.target
EOF"

# 6. Reload Systemd, Enable and Start Service
echo -e "${CYAN}[5/6] Registering, enabling, and starting ${SERVICE_NAME} daemon...${NC}"
$SUDO systemctl daemon-reload
$SUDO systemctl enable "${SERVICE_NAME}"
$SUDO systemctl restart "${SERVICE_NAME}"

# 7. Verification & Health Output
echo -e "${CYAN}[6/6] Checking service status...${NC}"
sleep 2

if systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo -e "${GREEN}========================================================================${NC}"
    echo -e "${GREEN}  SUCCESS: Futures DCA Trading Bot is now running via Systemd!          ${NC}"
    echo -e "${GREEN}========================================================================${NC}"
    echo ""
    echo -e "Systemd Control Commands:"
    echo -e "  - ${CYAN}Check Status:${NC}       sudo systemctl status ${SERVICE_NAME}"
    echo -e "  - ${CYAN}Restart Bot:${NC}        sudo systemctl restart ${SERVICE_NAME}"
    echo -e "  - ${CYAN}Stop Bot:${NC}           sudo systemctl stop ${SERVICE_NAME}"
    echo -e "  - ${CYAN}Start Bot:${NC}          sudo systemctl start ${SERVICE_NAME}"
    echo ""
    echo -e "Live Log Monitoring (without journalctl):"
    echo -e "  - ${CYAN}View Live Logs:${NC}     tail -f ${CURRENT_DIR}/bot.log"
    echo ""
else
    echo -e "${RED}[ERROR] Service failed to start.${NC}"
    echo -e "Check log output: tail -n 50 ${CURRENT_DIR}/bot.log"
    exit 1
fi
