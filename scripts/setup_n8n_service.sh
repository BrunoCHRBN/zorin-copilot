#!/usr/bin/env bash
# Setup do servico systemd do n8n para o Zorin Copilot
set -euo pipefail

echo "==> Configurando o servico n8n para o Zorin Copilot..."

# 1. Verificar Node.js e npm
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "ERRO: Node.js e npm sao necessarios. Instale via gerenciador de pacotes da sua distro:"
    echo "  Ubuntu/Zorin OS: sudo apt install -y nodejs npm"
    echo "  Arch Linux:      sudo pacman -S nodejs npm"
    exit 1
fi

echo "==> Node.js ($(node -v)) e npm ($(npm -v)) encontrados."

# 2. Verificar n8n instalado
if ! command -v n8n >/dev/null 2>&1; then
    echo "==> n8n nao encontrado globalmente. Instalando via npm..."
    if [ -w "$(npm root -g 2>/dev/null || echo '/usr/lib/node_modules')" ]; then
        npm install -g n8n
    else
        echo "==> Instalando n8n no diretorio do usuario (~/.npm-global ou ~/.local)..."
        mkdir -p "${HOME}/.npm-global"
        npm config set prefix "${HOME}/.npm-global"
        export PATH="${HOME}/.npm-global/bin:${PATH}"
        npm install -g n8n
    fi
fi

echo "==> n8n localizado em: $(command -v n8n)"

# 3. Criar diretorio de dados do n8n para o Zorin Copilot
N8N_DIR="${HOME}/.config/zorin-copilot/n8n"
mkdir -p "${N8N_DIR}"
chmod 700 "${N8N_DIR}"

# 4. Instalar Unit file de usuario no systemd
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
mkdir -p "${SYSTEMD_USER_DIR}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SOURCE="${SCRIPT_DIR}/../config/systemd/zorin-copilot-n8n.service"

if [ -f "${SERVICE_SOURCE}" ]; then
    cp "${SERVICE_SOURCE}" "${SYSTEMD_USER_DIR}/zorin-copilot-n8n.service"
    echo "==> Copiado zorin-copilot-n8n.service para ${SYSTEMD_USER_DIR}/"
else
    echo "ERRO: Arquivo ${SERVICE_SOURCE} nao encontrado!"
    exit 1
fi

# 5. Ativar e Iniciar o Servico
systemctl --user daemon-reload
systemctl --user enable zorin-copilot-n8n.service
systemctl --user restart zorin-copilot-n8n.service

echo "==> Servico n8n iniciado com sucesso!"
echo "==> Verifique o status com: systemctl --user status zorin-copilot-n8n.service"
echo "==> Interface local disponivel em: http://127.0.0.1:5678"
