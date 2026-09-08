# Maintainer: Bruno <bruno@example.com>
# Pacote para Arch Linux / EndeavourOS.
#
# Notas de empacotamento:
#  * PyGObject vem do repositório (python-gobject), nunca do pip — o pacote do pip
#    não traz as typelibs e conflita com o python marcado como EXTERNALLY-MANAGED.
#  * piper-tts e faster-whisper ficam como dependências opcionais: puxam
#    onnxruntime/ctranslate2 e quebram a instalação limpa em Python 3.13.
#  * gtk4-layer-shell é opcional e melhora o posicionamento da pílula de voz
#    em compositores Wayland (sem ela, o compositor escolhe a posição).

pkgname=zorin-copilot
pkgver=0.1.0
pkgrel=1
pkgdesc="Assistente de IA integrado ao desktop Linux (GNOME, KDE Plasma, Hyprland, Sway)"
arch=('any')
url="https://github.com/BrunoCHRBN/zorin-copilot"
license=('MIT')
depends=(
  'python'
  'python-gobject'
  'python-requests'
  'python-beautifulsoup4'
  'python-websockets'
  'gtk4'
  'libadwaita'
  'poppler'
)
optdepends=(
  'python-pillow: otimização de capturas de tela e área de transferência'
  'grim: captura de tela em compositores wlroots (Hyprland, Sway)'
  'slurp: seleção de área para recorte em wlroots'
  'grimblast: captura integrada ao Hyprland'
  'wl-clipboard: área de transferência em Wayland'
  'xclip: área de transferência em X11'
  'ydotool: injeção de clique e digitação via uinput'
  'dotool: alternativa leve a ydotool'
  'libnotify: notificações de desktop (notify-send)'
  'wireplumber: controle de volume via wpctl'
  'pipewire-pulse: compatibilidade PulseAudio para wpctl/pactl'
  'xdg-desktop-portal: captura de tela via portal em GNOME'
  'xdg-desktop-portal-hyprland: portal de captura no Hyprland'
  'xdg-desktop-portal-wlr: portal de captura em wlroots'
  'gtk4-layer-shell: posicionamento da pílula de voz em Wayland'
  'libayatana-appindicator: bandeja em sessões X11'
  'piper-tts: síntese de voz offline'
  'python-faster-whisper: reconhecimento de fala offline'
  'evince: abertura de PDFs na página exata'
  'tesseract: OCR de telas sem árvore de acessibilidade'
)
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
source=("$pkgname-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz")
sha256sums=('SKIP')

build() {
  cd "$pkgname-$pkgver"
  python -m build --wheel --no-isolation
}

package() {
  cd "$pkgname-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl

  install -Dm644 /dev/stdin "$pkgdir/usr/share/applications/io.github.bruno.ZorinCopilot.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Version=1.0
Name=Zorin Copilot
GenericName=Assistente de IA
Comment=Assistente de IA integrado ao desktop
Exec=zorin-copilot
Icon=system-help-symbolic
Terminal=false
Categories=Utility;GTK;
Keywords=ia;ai;assistente;copilot;
StartupNotify=true
StartupWMClass=ZorinCopilot
DESKTOP
}
