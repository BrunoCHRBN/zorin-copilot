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
  'piper-tts: síntese de voz offline'
  'python-faster-whisper: reconhecimento de fala offline'
  'evince: abertura de PDFs na página exata'
  'tesseract: OCR de telas sem árvore de acessibilidade'
)
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
source=("$pkgname-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz")
# Checksum real do tarball da tag — 'SKIP' é aceito pelo makepkg mas a AUR
# rejeita checksum vazio em fonte que não é VCS. Ao marcar nova release:
#   git tag v0.2.0 && git push --tags && updpkgsums   # e suba o pkgver
sha256sums=('b7185b3477c070950a6591d98584f9496c4b527c52bbaf0d1d83361f15ca73a7')

prepare() {
  cd "$pkgname-$pkgver"
  # Rede de segurança: o fonte precisa trazer a camada de portabilidade. Sem
  # `core/desktop/` o pacote instala um Copilot que só funciona no GNOME — ou
  # seja, exatamente o problema que este pacote existe para resolver, entregue
  # silenciosamente. Se a tag estiver atrasada, é melhor falhar aqui do que
  # publicar binário quebrado. (A tag v0.1.0 é anterior ao porte: 22 arquivos.)
  if [[ ! -d src/zorin_copilot/core/desktop ]]; then
    error "v$pkgver não contém a camada de portabilidade (src/zorin_copilot/core/desktop)."
    error "Marque uma release que inclua o porte, suba o pkgver e rode 'updpkgsums'."
    return 1
  fi
}

build() {
  cd "$pkgname-$pkgver"
  python -m build --wheel --no-isolation
}

package() {
  cd "$pkgname-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl

  # O wheel sai do build com __pycache__ dentro (140 arquivos num pacote de
  # 244). Bytecode não é conteúdo de pacote: o Python recompila no primeiro uso,
  # e assim o pacote não carrega lixo de build nem deixa órfão na remoção.
  find "$pkgdir" -type d -name '__pycache__' -prune -exec rm -rf {} +

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
