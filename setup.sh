#!/usr/bin/env bash
# =============================================================================
# Zorin Copilot — Script de Setup Completo do Sistema
# Configura dependências do sistema, ambiente virtual, atalhos globais
# no GNOME/Zorin OS e inicialização automática com o sistema (Autostart).
# =============================================================================

set -euo pipefail

APP_ID="io.github.bruno.ZorinCopilot"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
PREFIX="$DATA_HOME/zorin-copilot"
VENV="$PREFIX/venv"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
DESKTOP_DIR="$DATA_HOME/applications"
AUTOSTART_DIR="$CONFIG_HOME/autostart"
DESKTOP="$DESKTOP_DIR/$APP_ID.desktop"
AUTOSTART_FILE="$AUTOSTART_DIR/$APP_ID.desktop"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info() { printf "  ${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "  ${YELLOW}⚠${NC} %s\n" "$*"; }
step() { printf "\n${BOLD}${BLUE}==>${NC} ${BOLD}%s${NC}\n" "$*"; }

printf "${BOLD}======================================================${NC}\n"
printf "${BOLD}   Zorin Copilot — Assistente Nativo do Zorin OS       ${NC}\n"
printf "${BOLD}======================================================${NC}\n"

# -----------------------------------------------------------------------------
# 1. Verificação de Dependências do Sistema (multi-distribuição)
# -----------------------------------------------------------------------------
step "1/6 Verificando dependências do sistema operacional"

# Detecta o gerenciador de pacotes
PM="unknown"
if command -v apt-get >/dev/null 2>&1; then PM="apt"
elif command -v pacman >/dev/null 2>&1; then PM="pacman"
elif command -v dnf >/dev/null 2>&1; then PM="dnf"
fi

if [ "$PM" = "unknown" ]; then
    warn "Gerenciador de pacotes não reconhecido (apt/pacman/dnf). Pulei a instalação automática."
    warn "Instale manualmente: python3, pip, poppler, evince, ydotool e libayatana-appindicator."
else
    info "Gerenciador de pacotes detectado: $PM"
fi

# Mapeia cada capacidade -> pacote no gerenciador atual.
# 'AUR:' prefixa um pacote que mora no AUR (instalado via helper, não via pacman direto).
resolve_pkg() {
    local cap="$1"
    case "$PM" in
        apt) case "$cap" in
                python3) echo python3 ;;
                pip3) echo "python3-pip python3-venv" ;;
                pdftotext) echo poppler-utils ;;
                evince) echo evince ;;
                ydotool) echo ydotool ;;
                appindicator) echo gir1.2-ayatanaappindicator3-0.1 ;;
            esac ;;
        pacman) case "$cap" in
                python3) echo python ;;
                pip3) echo python-pip ;;
                pdftotext) echo poppler ;;
                evince) echo evince ;;
                ydotool) echo "AUR:ydotool" ;;
                appindicator) echo libayatana-appindicator ;;
            esac ;;
        dnf) case "$cap" in
                python3) echo python3 ;;
                pip3) echo python3-pip ;;
                pdftotext) echo poppler-utils ;;
                evince) echo evince ;;
                ydotool) echo ydotool ;;
                appindicator) echo libayatana-appindicator-gtk3 ;;
            esac ;;
    esac
}

pkg_present() {
    local p="$1"
    case "$PM" in
        apt) dpkg -s "$p" >/dev/null 2>&1 ;;
        pacman) pacman -Q "$p" >/dev/null 2>&1 ;;
        dnf) rpm -q "$p" >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
}

cap_present() {
    local cap="$1"
    case "$cap" in
        python3) command -v python3 >/dev/null ;;
        pip3) command -v pip3 >/dev/null || command -v pip >/dev/null ;;
        pdftotext) command -v pdftotext >/dev/null ;;
        evince) command -v evince >/dev/null ;;
        ydotool) command -v ydotool >/dev/null ;;
        appindicator) pkg_present "$(resolve_pkg appindicator)" ;;
        *) return 1 ;;
    esac
}

OFFICIAL_DEPS=()
AUR_DEPS=()
for cap in python3 pip3 pdftotext evince ydotool appindicator; do
    if ! cap_present "$cap"; then
        resolved="$(resolve_pkg "$cap")"
        for p in $resolved; do
            if [[ "$p" == AUR:* ]]; then
                AUR_DEPS+=("${p#AUR:}")
            else
                OFFICIAL_DEPS+=("$p")
            fi
        done
    fi
done

if [ "$PM" != "unknown" ] && { [ ${#OFFICIAL_DEPS[@]} -gt 0 ] || [ ${#AUR_DEPS[@]} -gt 0 ]; }; then
    warn "Pacotes recomendados não encontrados:"
    [ ${#OFFICIAL_DEPS[@]} -gt 0 ] && printf "    oficiais: %s\n" "${OFFICIAL_DEPS[*]}"
    [ ${#AUR_DEPS[@]} -gt 0 ] && printf "    AUR:      %s\n" "${AUR_DEPS[*]}"

    DO_INSTALL=0
    if [ "${AUTO_INSTALL:-0}" = "1" ]; then
        DO_INSTALL=1
    elif [ -t 0 ]; then
        printf "  Deseja instalar os pacotes faltantes agora? [S/n] "
        read -r answer || answer="s"
        [[ "$answer" =~ ^[sSyY]?$ ]] && DO_INSTALL=1
    fi

    if [ "$DO_INSTALL" = "1" ]; then
        case "$PM" in
            apt)
                sudo apt-get update -qq
                sudo apt-get install -y "${OFFICIAL_DEPS[@]}"
                ;;
            pacman)
                sudo pacman -S --needed "${OFFICIAL_DEPS[@]}"
                if [ ${#AUR_DEPS[@]} -gt 0 ]; then
                    AUR_HELPER=""
                    command -v yay >/dev/null && AUR_HELPER=yay
                    command -v paru >/dev/null && AUR_HELPER=paru
                    if [ -n "$AUR_HELPER" ]; then
                        "$AUR_HELPER" -S --needed "${AUR_DEPS[@]}"
                    else
                        warn "Helper do AUR (yay/paru) não encontrado. Instale manualmente: ${AUR_DEPS[*]}"
                    fi
                fi
                ;;
            dnf)
                sudo dnf install -y "${OFFICIAL_DEPS[@]}"
                ;;
        esac
        info "Pacotes do sistema instalados."
    else
        warn "Pulou a instalação automática. Instale manualmente os pacotes acima para suporte completo."
    fi
else
    info "Todas as ferramentas de sistema recomendadas estão instaladas!"
fi

# -----------------------------------------------------------------------------
# 2. Configuração do Ambiente Virtual Python
# -----------------------------------------------------------------------------
step "2/6 Configurando ambiente virtual em $VENV"
mkdir -p "$PREFIX"
if [ ! -d "$VENV" ]; then
    python3 -m venv --system-site-packages "$VENV"
    info "Ambiente virtual criado."
else
    info "Ambiente virtual existente reaproveitado."
fi

"$VENV/bin/pip" install --quiet --upgrade pip setuptools wheel
"$VENV/bin/pip" install --quiet -e "$ROOT"
info "Pacote zorin-copilot instalado em modo desenvolvimento."

# -----------------------------------------------------------------------------
# 3. Binários de Comando em ~/.local/bin
# -----------------------------------------------------------------------------
step "3/6 Criando links de comando em $BIN"
mkdir -p "$BIN"
ln -sf "$VENV/bin/zorin-copilot" "$BIN/zorin-copilot"
ln -sf "$VENV/bin/zorin-copilot-cli" "$BIN/zorin-copilot-cli"
info "Comandos 'zorin-copilot' e 'zorin-copilot-cli' disponíveis em $BIN"

# -----------------------------------------------------------------------------
# 4. Registro no Menu de Aplicativos do Zorin OS
# -----------------------------------------------------------------------------
step "4/6 Registrando aplicativo no menu do Zorin OS"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP" <<EOF_DESK
[Desktop Entry]
Type=Application
Version=1.0
Name=Zorin Copilot
GenericName=Assistente de IA
Comment=Assistente de IA integrado ao desktop Zorin OS
Exec=$BIN/zorin-copilot
Icon=system-help-symbolic
Terminal=false
Categories=Utility;GTK;GNOME;
Keywords=ia;ai;assistente;copilot;zorin;
StartupNotify=true
StartupWMClass=ZorinCopilot
EOF_DESK

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" || true
info "Lançador registrado: $DESKTOP"

# -----------------------------------------------------------------------------
# 5. Configuração dos Atalhos Globais de Sistema (GNOME/Zorin)
# -----------------------------------------------------------------------------
step "5/7 Registrando atalhos globais de sistema no GNOME/Zorin OS"

"$VENV/bin/python3" -c "
from zorin_copilot.core.shortcuts import ShortcutManager
# Atalho HUD: Super + C
if ShortcutManager.register('<Super>c'):
    print('  [Atalho HUD] Super + C configurado com sucesso.')
else:
    print('  [Atalho HUD] Aviso ao registrar Super + C.')

# Atalho Recorte Inteligente: Super + Shift + S
if ShortcutManager.register_crop('<Super><Shift>s'):
    print('  [Atalho Recorte] Super + Shift + S configurado com sucesso.')
else:
    print('  [Atalho Recorte] Aviso ao registrar Super + Shift + S.')

# Atalho Conversa por Voz: Super + Shift + V
if ShortcutManager.register_voice('<Super><Shift>v'):
    print('  [Atalho Voz] Super + Shift + V configurado com sucesso.')
else:
    print('  [Atalho Voz] Aviso ao registrar Super + Shift + V.')
"

# -----------------------------------------------------------------------------
# 6. Modelo Neural de Voz Offline (Piper pt_BR)
# -----------------------------------------------------------------------------
step "6/7 Verificando modelo de voz offline (Piper pt_BR)"
PIPER_DIR="$DATA_HOME/zorin-copilot/models/piper"
mkdir -p "$PIPER_DIR"
if [ ! -f "$PIPER_DIR/pt_BR-faber-medium.onnx" ]; then
    info "Baixando modelo neural de voz pt_BR-faber-medium..."
    curl -sSL -o "$PIPER_DIR/pt_BR-faber-medium.onnx" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx" || true
    curl -sSL -o "$PIPER_DIR/pt_BR-faber-medium.onnx.json" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json" || true
fi
info "Modelo Piper configurado em $PIPER_DIR"

# -----------------------------------------------------------------------------
# 7. Inicialização com a Máquina (Autostart)
# -----------------------------------------------------------------------------
step "7/7 Configurando inicialização automática com o sistema (Autostart)"
mkdir -p "$AUTOSTART_DIR"

"$VENV/bin/python3" -c "
from zorin_copilot.core.shortcuts import AutostartManager
if AutostartManager.enable('$BIN/zorin-copilot'):
    print('  [Autostart] Inicialização no boot habilitada.')
else:
    print('  [Autostart] Falha ao configurar arquivo de autostart.')
"

# -----------------------------------------------------------------------------
# Diagnóstico Final
# -----------------------------------------------------------------------------
step "Validação e Diagnóstico do Sistema"
"$BIN/zorin-copilot-cli" doctor || true

printf "\n${BOLD}${GREEN}======================================================${NC}\n"
printf "${BOLD}${GREEN}   Instalação e Setup Concluídos com Sucesso!        ${NC}\n"
printf "${BOLD}${GREEN}======================================================${NC}\n"
printf "• Pressione ${BOLD}Super + C${NC} a qualquer momento para abrir o Copilot.\n"
printf "• Pressione ${BOLD}Super + Shift + S${NC} para recorte e análise visual instantânea.\n"
printf "• Pressione ${BOLD}Super + Shift + V${NC} para conversar por voz contínua 100%% offline.\n"
printf "• O assistente iniciará automaticamente com o computador em segundo plano.\n"
printf "• Para ajustar preferências, clique no ícone ⚙️ no topo da interface.\n\n"
