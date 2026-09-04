#!/usr/bin/env bash
set -euo pipefail

APP_ID="io.github.bruno.ZorinCopilot"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="$DATA_HOME/zorin-copilot"
VENV="$PREFIX/venv"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
DESKTOP_DIR="$DATA_HOME/applications"
DESKTOP="$DESKTOP_DIR/$APP_ID.desktop"

info() { printf '  %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

step "1/4 Configurando ambiente virtual"
rm -rf "$VENV"
mkdir -p "$PREFIX"
python3 -m venv --system-site-packages "$VENV"
info "Ambiente criado em $VENV"

step "2/4 Instalando pacote"
"$VENV/bin/pip" install --quiet -e "$ROOT"
info "Instalado em modo editável"

step "3/4 Criando atalhos de comando em $BIN"
mkdir -p "$BIN"
ln -sf "$VENV/bin/zorin-copilot" "$BIN/zorin-copilot"
ln -sf "$VENV/bin/zorin-copilot-cli" "$BIN/zorin-copilot-cli"
info "$BIN/zorin-copilot"
info "$BIN/zorin-copilot-cli"

step "4/4 Registrando no menu de aplicativos do Zorin"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Version=1.0
Name=Zorin Copilot
GenericName=Assistente de IA
Comment=Assistente de IA integrado ao desktop Zorin OS
Exec=$VENV/bin/zorin-copilot
Icon=system-help-symbolic
Terminal=false
Categories=Utility;GTK;GNOME;
Keywords=ia;ai;assistente;copilot;zorin;
StartupNotify=true
StartupWMClass=ZorinCopilot
DESK

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" || true
info "Atalho criado: $DESKTOP"

step "Instalação concluída!"
info "Você pode abrir pelo menu de aplicativos ou digitando 'zorin-copilot' no terminal."
