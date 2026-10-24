#!/usr/bin/env bash
# Setup de permissoes uinput e daemon do ydotool para o Zorin Copilot
set -euo pipefail

echo "==> Configurando permissoes uinput e servico ydotoold..."

# 1. Verificar presenca do ydotool e ydotoold
if ! command -v ydotool >/dev/null 2>&1 || ! command -v ydotoold >/dev/null 2>&1; then
    echo "AVISO: ydotool ou ydotoold nao encontrados no PATH."
    echo "Instale via:"
    echo "  Ubuntu/Zorin OS: sudo apt install -y ydotool"
    echo "  Arch Linux:      sudo pacman -S ydotool"
fi

# 2. Configurar regra udev para /dev/uinput
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UDEV_SOURCE="${SCRIPT_DIR}/../config/udev/80-uinput.rules"

if [ -f "${UDEV_SOURCE}" ]; then
    echo "==> Instalando regra udev em /etc/udev/rules.d/80-uinput.rules (requer sudo)..."
    sudo cp "${UDEV_SOURCE}" /etc/udev/rules.d/80-uinput.rules
    sudo udevadm control --reload-rules
    sudo udevadm trigger
fi

# 3. Adicionar usuario ao grupo input
CURRENT_USER="${USER:-$(whoami)}"
if ! groups "${CURRENT_USER}" | grep -q "\binput\b"; then
    echo "==> Adicionando usuario '${CURRENT_USER}' ao grupo 'input'..."
    sudo usermod -aG input "${CURRENT_USER}"
    echo "IMPORTANTE: Faca logout e login novamente para aplicar a inclusao no grupo 'input'."
fi

# 4. Instalar Unit do ydotoold para sessao de usuario
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
mkdir -p "${SYSTEMD_USER_DIR}"

SERVICE_SOURCE="${SCRIPT_DIR}/../config/systemd/ydotoold.service"
if [ -f "${SERVICE_SOURCE}" ]; then
    cp "${SERVICE_SOURCE}" "${SYSTEMD_USER_DIR}/ydotoold.service"
    systemctl --user daemon-reload
    systemctl --user enable ydotoold.service
    systemctl --user restart ydotoold.service || echo "Aviso: ydotoold sera iniciado no proximo login com o grupo 'input' ativo."
    echo "==> Servico ydotoold configurado em: ${SYSTEMD_USER_DIR}/ydotoold.service"
fi

echo "==> Setup de uinput e ydotool finalizado."
