#!/usr/bin/env bash
# =============================================================================
# Zorin Copilot — Script de Setup do Sistema
#
# Configura dependências do sistema, ambiente virtual, atalhos globais e
# inicialização automática. Funciona em Debian/Ubuntu/Zorin (apt), Arch e
# derivados como EndeavourOS/Manjaro (pacman + AUR) e Fedora (dnf).
#
# Principais diferenças por ambiente:
#   GNOME     -> atalhos via media-keys (gsettings), captura via portal XDG
#   KDE       -> atalhos via kglobalshortcutsrc, captura via spectacle
#   Hyprland  -> atalhos e autostart via snippet em ~/.config/hypr/
#   Sway      -> atalhos e autostart via snippet em ~/.config/sway/
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
DESKTOP="$DESKTOP_DIR/$APP_ID.desktop"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

info() { printf "  ${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "  ${YELLOW}⚠${NC} %s\n" "$*"; }
step() { printf "\n${BOLD}${BLUE}==>${NC} ${BOLD}%s${NC}\n" "$*"; }

printf "${BOLD}======================================================${NC}\n"
printf "${BOLD}   Zorin Copilot — Setup (multi-distribuição)${NC}\n"
printf "${BOLD}======================================================${NC}\n"

# -----------------------------------------------------------------------------
# 0. Detecção de ambiente
# -----------------------------------------------------------------------------
step "0/7 Detectando ambiente"

PM="unknown"
if command -v apt-get >/dev/null 2>&1; then PM="apt"
elif command -v pacman >/dev/null 2>&1; then PM="pacman"
elif command -v dnf >/dev/null 2>&1; then PM="dnf"
fi

DESKTOP_ENV="$(printf '%s' "${XDG_CURRENT_DESKTOP:-}" | tr '[:upper:]' '[:lower:]')"
if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then
    DESKTOP_ENV="hyprland"
elif [ -n "${SWAYSOCK:-}" ]; then
    DESKTOP_ENV="sway"
fi
[ -z "$DESKTOP_ENV" ] && DESKTOP_ENV="${XDG_SESSION_TYPE:-desconhecido}"

info "Gerenciador de pacotes: $PM"
info "Ambiente gráfico:       $DESKTOP_ENV (${XDG_SESSION_TYPE:-?})"

# -----------------------------------------------------------------------------
# 1. Dependências do sistema
# -----------------------------------------------------------------------------
step "1/7 Verificando dependências do sistema"

# Mapeia capacidade -> pacotes. Prefixo "AUR:" indica pacote do AUR (vai por yay/paru).
resolve_pkg() {
    local cap="$1"
    case "$PM" in
        apt) case "$cap" in
                python3) echo python3 ;;
                pip3) echo "python3-pip python3-venv" ;;
                gi) echo "python3-gi python3-gi-cairo" ;;
                gtk4) echo "gir1.2-gtk-4.0" ;;
                libadwaita) echo "gir1.2-adw-1 libadwaita-1-0" ;;
                pdftotext) echo poppler-utils ;;
                evince) echo evince ;;
                ydotool) echo ydotool ;;
                notify) echo libnotify-bin ;;
                clipboard) echo "wl-clipboard xclip" ;;
                grim) echo "" ;;   # não existe no Debian; cai no portal/grim compilado
                slurp) echo "" ;;
            esac ;;
        pacman) case "$cap" in
                python3) echo python ;;
                pip3) echo python-pip ;;
                gi) echo python-gobject ;;
                gtk4) echo gtk4 ;;
                libadwaita) echo libadwaita ;;
                pdftotext) echo poppler ;;
                evince) echo evince ;;
                ydotool) echo "AUR:ydotool" ;;
                notify) echo libnotify ;;
                clipboard) echo "wl-clipboard xclip" ;;
                grim) echo grim ;;
                slurp) echo slurp ;;
            esac ;;
        dnf) case "$cap" in
                python3) echo python3 ;;
                pip3) echo python3-pip ;;
                gi) echo python3-gobject ;;
                gtk4) echo gtk4 ;;
                libadwaita) echo libadwaita ;;
                pdftotext) echo poppler-utils ;;
                evince) echo evince ;;
                ydotool) echo ydotool ;;
                notify) echo libnotify ;;
                clipboard) echo "wl-clipboard xclip" ;;
                grim) echo grim ;;
                slurp) echo slurp ;;
            esac ;;
    esac
}

cap_present() {
    case "$1" in
        python3) command -v python3 >/dev/null ;;
        pip3) command -v pip3 >/dev/null || command -v pip >/dev/null ;;
        pdftotext) command -v pdftotext >/dev/null ;;
        evince) command -v evince >/dev/null ;;
        ydotool) command -v ydotool >/dev/null || command -v dotool >/dev/null ;;
        notify) command -v notify-send >/dev/null ;;
        clipboard) command -v wl-copy >/dev/null || command -v xclip >/dev/null ;;
        grim) command -v grim >/dev/null ;;
        slurp) command -v slurp >/dev/null ;;
        *) return 1 ;;
    esac
}

# Capacidades que realmente importam para o Copilot rodar.
CAPS="python3 pip3 gi gtk4 libadwaita pdftotext notify clipboard"
# wlroots precisa de grim/slurp; o GNOME usa o portal XDG.
case "$DESKTOP_ENV" in
    hyprland|sway|wlroots) CAPS="$CAPS grim slurp" ;;
esac
# Bandeja é StatusNotifierItem puro em D-Bus: não depende de AppIndicator/GTK3.
CAPS="$CAPS ydotool"

OFFICIAL_DEPS=()
AUR_DEPS=()
for cap in $CAPS; do
    if ! cap_present "$cap"; then
        for p in $(resolve_pkg "$cap"); do
            case "$p" in
                AUR:*) AUR_DEPS+=("${p#AUR:}") ;;
                "") ;;
                *) OFFICIAL_DEPS+=("$p") ;;
            esac
        done
    fi
done

if [ "$PM" = "unknown" ]; then
    warn "Gerenciador de pacotes não reconhecido. Instale manualmente: python3, pip, python-gobject, gtk4, libadwaita, poppler, libnotify, wl-clipboard."
elif [ ${#OFFICIAL_DEPS[@]} -eq 0 ] && [ ${#AUR_DEPS[@]} -eq 0 ]; then
    info "Todas as dependências do sistema já estão presentes."
else
    warn "Pacotes recomendados não encontrados:"
    [ ${#OFFICIAL_DEPS[@]} -gt 0 ] && printf "    oficiais: %s\n" "${OFFICIAL_DEPS[*]}"
    [ ${#AUR_DEPS[@]} -gt 0 ] && printf "    AUR:      %s\n" "${AUR_DEPS[*]}"

    DO_INSTALL=0
    if [ "${AUTO_INSTALL:-0}" = "1" ]; then
        DO_INSTALL=1
    elif [ -t 0 ]; then
        printf "  Deseja instalar agora? [S/n] "
        read -r answer || answer="s"
        [[ "$answer" =~ ^[sSyY]?$ ]] && DO_INSTALL=1
    fi

    if [ "$DO_INSTALL" = "1" ]; then
        case "$PM" in
            apt)
                sudo apt-get update -qq
                # shellcheck disable=SC2068
                sudo apt-get install -y ${OFFICIAL_DEPS[@]}
                ;;
            pacman)
                # shellcheck disable=SC2068
                sudo pacman -S --needed --noconfirm ${OFFICIAL_DEPS[@]}
                if [ ${#AUR_DEPS[@]} -gt 0 ]; then
                    AUR_HELPER=""
                    command -v yay  >/dev/null && AUR_HELPER=yay
                    command -v paru >/dev/null && AUR_HELPER=paru
                    if [ -n "$AUR_HELPER" ]; then
                        # shellcheck disable=SC2068
                        "$AUR_HELPER" -S --needed --noconfirm ${AUR_DEPS[@]}
                    else
                        warn "Sem helper do AUR (yay/paru). Instale manualmente: ${AUR_DEPS[*]}"
                    fi
                fi
                ;;
            dnf)
                # shellcheck disable=SC2068
                sudo dnf install -y ${OFFICIAL_DEPS[@]}
                ;;
        esac
        info "Dependências do sistema instaladas."
    else
        warn "Pulando instalação. O Copilot pode funcionar com recursos reduzidos."
    fi
fi

# -----------------------------------------------------------------------------
# 2. Ambiente virtual
# -----------------------------------------------------------------------------
step "2/7 Configurando ambiente virtual em $VENV"
mkdir -p "$PREFIX"
if [ ! -d "$VENV" ]; then
    # --system-site-packages é obrigatório: PyGObject vem do gerenciador da
    # distro (precisa das typelibs do sistema). No Arch, pip não pode instalar
    # PyGObject por causa do PEP 668.
    python3 -m venv --system-site-packages "$VENV"
    info "Ambiente virtual criado."
else
    info "Ambiente virtual existente reaproveitado."
fi

"$VENV/bin/pip" install --quiet --upgrade pip setuptools wheel
# As dependências pesadas e opcionais (piper-tts, faster-whisper) ficam no extra
# [voice] — instalá-las aqui quebraria a instalação em Arch/Python 3.13.
"$VENV/bin/pip" install --quiet -e "$ROOT"
info "Pacote zorin-copilot instalado em modo desenvolvimento."

# -----------------------------------------------------------------------------
# 3. Binários em ~/.local/bin
# -----------------------------------------------------------------------------
step "3/7 Criando links de comando em $BIN"
mkdir -p "$BIN"
ln -sf "$VENV/bin/zorin-copilot" "$BIN/zorin-copilot"
ln -sf "$VENV/bin/zorin-copilot-cli" "$BIN/zorin-copilot-cli"
info "Comandos 'zorin-copilot' e 'zorin-copilot-cli' disponíveis em $BIN"

# -----------------------------------------------------------------------------
# 4. Lançador no menu de aplicativos
# -----------------------------------------------------------------------------
step "4/7 Registrando aplicativo no menu do sistema"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP" <<EOF_DESK
[Desktop Entry]
Type=Application
Version=1.0
Name=Zorin Copilot
GenericName=Assistente de IA
Comment=Assistente de IA integrado ao desktop
Exec=$BIN/zorin-copilot
Icon=system-help-symbolic
Terminal=false
Categories=Utility;GTK;
Keywords=ia;ai;assistente;copilot;
StartupNotify=true
StartupWMClass=ZorinCopilot
EOF_DESK

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" || true
info "Lançador registrado: $DESKTOP"

# -----------------------------------------------------------------------------
# 5. Atalhos globais (backend escolhido pelo ambiente)
# -----------------------------------------------------------------------------
step "5/7 Registrando atalhos globais"
"$VENV/bin/python3" - <<'PY'
from zorin_copilot.core.shortcuts import ShortcutManager

print(f"  Backend de atalhos detectado: {ShortcutManager.backend_name()}")
for label, fn in (
    ("HUD (Super+C)", ShortcutManager.register),
    ("Recorte (Super+Shift+S)", ShortcutManager.register_crop),
    ("Voz (Super+Shift+V)", ShortcutManager.register_voice),
):
    ok = fn()
    print(f"  {'✓' if ok else '⚠'} {label}: {ShortcutManager.last_message}")
PY

# -----------------------------------------------------------------------------
# 6. Modelo de voz offline (opcional)
# -----------------------------------------------------------------------------
step "6/7 Modelo de voz offline (Piper pt_BR)"
PIPER_DIR="$DATA_HOME/zorin-copilot/models/piper"
if "$VENV/bin/python3" -c "import piper" 2>/dev/null; then
    mkdir -p "$PIPER_DIR"
    if [ ! -f "$PIPER_DIR/pt_BR-faber-medium.onnx" ]; then
        info "Baixando modelo neural de voz pt_BR-faber-medium..."
        curl -sSL -o "$PIPER_DIR/pt_BR-faber-medium.onnx" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx" || true
        curl -sSL -o "$PIPER_DIR/pt_BR-faber-medium.onnx.json" "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json" || true
    fi
    info "Modelo Piper configurado em $PIPER_DIR"
else
    warn "piper-tts não instalado — voz offline desativada."
    warn "Para habilitar: '$VENV/bin/pip install -e $ROOT[voice]'"
fi

# -----------------------------------------------------------------------------
# 7. Autostart
# -----------------------------------------------------------------------------
step "7/7 Inicialização automática com o sistema"
"$VENV/bin/python3" - <<PY
from zorin_copilot.core.shortcuts import AutostartManager
ok = AutostartManager.enable("$BIN/zorin-copilot")
print("  Autostart habilitado." if ok else "  Falha ao configurar autostart.")
PY

# -----------------------------------------------------------------------------
# 7b. Regras de decoração no Hyprland (blur/rounding por app_id e namespace da pílula)
# -----------------------------------------------------------------------------
if [ "$DESKTOP_ENV" = "hyprland" ]; then
    step "7b/7 Vidro real no Hyprland (blur/rounding)"
    "$VENV/bin/python3" - <<PY
from zorin_copilot.core.desktop.env import current_environment
from zorin_copilot.core.shortcuts import ensure_decor_rules
ok, msg = ensure_decor_rules(current_environment())
print("  " + msg)
PY
fi

# -----------------------------------------------------------------------------
# Diagnóstico final
# -----------------------------------------------------------------------------
step "Validação e Diagnóstico do Sistema"
"$BIN/zorin-copilot-cli" doctor || true

printf "\n${BOLD}${GREEN}======================================================${NC}\n"
printf "${BOLD}${GREEN}   Setup Concluído${NC}\n"
printf "${BOLD}${GREEN}======================================================${NC}\n"

case "$DESKTOP_ENV" in
    hyprland)
        printf "• Hyprland: os atalhos foram gravados em ${BOLD}~/.config/hypr/zorin-copilot.conf${NC}.\n"
        printf "  Recarregue com ${BOLD}hyprctl reload${NC} se o snippet ainda não estiver incluído.\n"
        printf "• Para ícone na bandeja, use um módulo SNI na waybar (tray).\n"
        ;;
    sway)
        printf "• Sway: os atalhos foram gravados em ${BOLD}~/.config/sway/zorin-copilot.conf${NC}.\n"
        printf "  Recarregue com ${BOLD}swaymsg reload${NC} se o snippet ainda não estiver incluído.\n"
        ;;
    *)
        printf "• Pressione ${BOLD}Super + C${NC} para abrir o Copilot.\n"
        ;;
esac
printf "• Para ajustar preferências, use o ícone ⚙️ no topo da interface.\n\n"
