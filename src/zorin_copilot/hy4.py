"""
hy4 - Interface de Linha de Comando dedicada ao Tencent HY4 (WorkBuddy AI).
Suporta consultas diretas, pipelines Unix (stdin), e modo interativo (REPL)
com streaming em tempo real e visualização de raciocínio.
"""

from __future__ import annotations

import argparse
import json
import os
import readline
import signal
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Generator

VERSION = "1.0.0"
DEFAULT_URL = "https://www.workbuddy.ai/v2/chat/completions"
DEFAULT_MODEL = "hy4-preview"
DEFAULT_SYSTEM_PROMPT = (
    "Você é o Tencent HY4, um modelo de IA de alta capacidade (770B MoE) rodando diretamente "
    "no terminal do usuário. Responda com clareza, precisão técnica e formatação limpa em Markdown."
)

# Cores ANSI para o terminal
class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[90m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"


def load_config() -> dict[str, str]:
    """Descobre a chave de API e URL do WorkBuddy em diferentes locais."""
    api_key = os.getenv("HY4_API_KEY") or os.getenv("WORKBUDDY_API_KEY") or ""
    api_url = os.getenv("WORKBUDDY_URL") or DEFAULT_URL
    model = os.getenv("HY4_MODEL") or DEFAULT_MODEL

    # 1. Tentar ler do ~/.config/zorin-copilot/config.json
    copilot_cfg_path = Path.home() / ".config" / "zorin-copilot" / "config.json"
    if copilot_cfg_path.exists():
        try:
            with open(copilot_cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not api_key:
                    api_key = data.get("workbuddy_api_key", "")
                if not os.getenv("WORKBUDDY_URL"):
                    api_url = data.get("workbuddy_url", DEFAULT_URL)
                if not os.getenv("HY4_MODEL"):
                    model = data.get("workbuddy_model", DEFAULT_MODEL)
        except Exception:
            pass

    # 2. Tentar ler do ~/.config/hy4/config.json
    hy4_cfg_path = Path.home() / ".config" / "hy4" / "config.json"
    if hy4_cfg_path.exists():
        try:
            with open(hy4_cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not api_key:
                    api_key = data.get("api_key", "")
                if not os.getenv("WORKBUDDY_URL") and "api_url" in data:
                    api_url = data["api_url"]
                if not os.getenv("HY4_MODEL") and "model" in data:
                    model = data["model"]
        except Exception:
            pass

    return {
        "api_key": api_key,
        "api_url": api_url,
        "model": model,
    }


def save_hy4_key(api_key: str):
    """Salva a chave no arquivo de configuração do hy4."""
    hy4_dir = Path.home() / ".config" / "hy4"
    hy4_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = hy4_dir / "config.json"
    data = {}
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data["api_key"] = api_key
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def stream_chat(
    api_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    timeout: int = 120,
) -> Generator[tuple[str, str], None, None]:
    """
    Envia a requisição para a API WorkBuddy / Tencent HY4 usando SSE streaming.
    Retorna tuplas (tipo, texto), onde tipo é 'reasoning' ou 'content'.
    """
    # Requisito WorkBuddy: primeira mensagem precisa ter role: system
    valid_messages = []
    if not messages or messages[0].get("role") != "system":
        valid_messages.append({"role": "system", "content": DEFAULT_SYSTEM_PROMPT})
    valid_messages.extend(messages)

    payload = {
        "model": model,
        "messages": valid_messages,
        "temperature": temperature,
        "stream": True,
    }

    endpoint = api_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"hy4-cli/{VERSION}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})

                    # Tencent HY4 envia 'reasoning_content' para o processo de pensamento
                    if "reasoning_content" in delta and delta["reasoning_content"]:
                        yield ("reasoning", delta["reasoning_content"])

                    if "content" in delta and delta["content"]:
                        yield ("content", delta["content"])
                except json.JSONDecodeError:
                    continue

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(body)
            msg = err_json.get("message") or err_json.get("error", {}).get("message") or body
        except Exception:
            msg = body
        raise RuntimeError(f"Erro na API WorkBuddy (HTTP {e.code}): {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Erro de conexão com WorkBuddy AI: {e.reason}")
    except TimeoutError:
        raise RuntimeError(f"Tempo limite de resposta esgotado ({timeout}s).")


def run_interactive(cfg: dict[str, str], show_thinking: bool, raw_mode: bool):
    """Loop interativo estilo REPL no terminal."""
    c_bold = "" if raw_mode else Color.BOLD
    c_cyan = "" if raw_mode else Color.CYAN
    c_dim = "" if raw_mode else Color.DIM
    c_green = "" if raw_mode else Color.GREEN
    c_yellow = "" if raw_mode else Color.YELLOW
    c_red = "" if raw_mode else Color.RED
    c_reset = "" if raw_mode else Color.RESET

    print(f"{c_bold}{c_cyan}╭─────────────────────────────────────────────────────────────╮{c_reset}")
    print(f"{c_bold}{c_cyan}│  Tencent HY4 Terminal (WorkBuddy AI) - 770B MoE             │{c_reset}")
    print(f"{c_bold}{c_cyan}│  Modelo: {cfg['model']:<15} Digite /help para ver comandos    │{c_reset}")
    print(f"{c_bold}{c_cyan}╰─────────────────────────────────────────────────────────────╯{c_reset}\n")

    history: list[dict[str, str]] = []

    # Configurar histórico do readline
    hist_file = Path.home() / ".hy4_history"
    try:
        if hist_file.exists():
            readline.read_history_file(str(hist_file))
    except Exception:
        pass

    interrupted = False

    def sigint_handler(sig, frame):
        nonlocal interrupted
        interrupted = True
        print(f"\n{c_yellow}[Interrompido]{c_reset}")

    prev_handler = signal.signal(signal.SIGINT, sigint_handler)

    try:
        while True:
            try:
                interrupted = False
                user_input = input(f"{c_bold}{c_green}hy4>{c_reset} ").strip()
                if not user_input:
                    continue

                # Comandos especiais
                if user_input in ("/exit", "/quit", ":q"):
                    print(f"{c_dim}Até logo!{c_reset}")
                    break
                if user_input == "/clear":
                    history.clear()
                    print(f"{c_yellow}Histórico de conversa limpo.{c_reset}\n")
                    continue
                if user_input == "/help":
                    print(f"\n{c_bold}Comandos disponíveis:{c_reset}")
                    print(f"  {c_cyan}/clear{c_reset}  - Limpa a memória da conversa atual")
                    print(f"  {c_cyan}/model{c_reset}  - Mostra o modelo ativo")
                    print(f"  {c_cyan}/exit{c_reset}   - Sai do terminal hy4 (ou use Ctrl+D)\n")
                    continue
                if user_input == "/model":
                    print(f"{c_cyan}Modelo ativo: {cfg['model']} | Endpoint: {cfg['api_url']}{c_reset}\n")
                    continue

                history.append({"role": "user", "content": user_input})

                print()
                thinking_started = False
                content_started = False
                full_response = []

                for kind, chunk in stream_chat(
                    api_url=cfg["api_url"],
                    api_key=cfg["api_key"],
                    model=cfg["model"],
                    messages=history,
                ):
                    if interrupted:
                        break

                    if kind == "reasoning":
                        if show_thinking:
                            if not thinking_started:
                                sys.stdout.write(f"{c_dim}💭 Pensando: ")
                                thinking_started = True
                            sys.stdout.write(chunk)
                            sys.stdout.flush()
                    elif kind == "content":
                        if thinking_started and not content_started:
                            sys.stdout.write(f"{c_reset}\n\n")
                        content_started = True
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
                        full_response.append(chunk)

                if thinking_started and not content_started:
                    sys.stdout.write(f"{c_reset}\n")

                sys.stdout.write("\n\n")
                sys.stdout.flush()

                if full_response:
                    history.append({"role": "assistant", "content": "".join(full_response)})

            except EOFError:
                print(f"\n{c_dim}Até logo!{c_reset}")
                break
            except Exception as e:
                print(f"\n{c_red}Erro: {e}{c_reset}\n")
    finally:
        signal.signal(signal.SIGINT, prev_handler)
        try:
            readline.write_history_file(str(hist_file))
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="hy4",
        description="Cliente de terminal oficial para o Tencent HY4 via WorkBuddy AI.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Pergunta ou comando a enviar ao HY4 (se vazio, abre modo interativo).",
    )
    parser.add_argument(
        "-m", "--model",
        help="Especifica o modelo (padrão: hy4-preview).",
    )
    parser.add_argument(
        "-k", "--key",
        help="Especifica ou atualiza a chave de API da WorkBuddy AI.",
    )
    parser.add_argument(
        "-s", "--system",
        default=DEFAULT_SYSTEM_PROMPT,
        help="Define o prompt de sistema personalizado.",
    )
    parser.add_argument(
        "-t", "--temperature",
        type=float,
        default=0.7,
        help="Temperatura de amostragem (padrão: 0.7).",
    )
    parser.add_argument(
        "--no-think",
        action="store_true",
        help="Oculta o processo de raciocínio (thinking) do modelo.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Desativa formatação com cores ANSI (ideal para scripts e pipes).",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"hy4 CLI v{VERSION}",
    )

    args = parser.parse_args()

    cfg = load_config()

    if args.key:
        cfg["api_key"] = args.key
        save_hy4_key(args.key)
        print(f"{Color.GREEN}Chave salva com sucesso em ~/.config/hy4/config.json{Color.RESET}")

    if args.model:
        cfg["model"] = args.model

    if not cfg["api_key"]:
        sys.stderr.write(
            f"{Color.RED}Erro: Nenhuma chave de API encontrada para o Tencent HY4.{Color.RESET}\n"
            "Configure usando uma das opções:\n"
            "  1. hy4 --key 'ck_sua_chave_aqui'\n"
            "  2. export HY4_API_KEY='ck_sua_chave_aqui'\n"
            "  3. Configure no Zorin Copilot (zorin-copilot-cli config --set-workbuddy-key ...)\n"
        )
        return 1

    # Verificar se há entrada via stdin (pipeline Unix)
    stdin_content = ""
    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read().strip()

    prompt_args = " ".join(args.prompt).strip()

    # Se recebeu pergunta via argumentos ou pipeline -> modo direto
    if prompt_args or stdin_content:
        full_user_content = prompt_args
        if stdin_content:
            if full_user_content:
                full_user_content = f"{full_user_content}\n\n---\nEntrada (stdin):\n{stdin_content}"
            else:
                full_user_content = stdin_content

        messages = [
            {"role": "system", "content": args.system},
            {"role": "user", "content": full_user_content},
        ]

        c_dim = "" if args.raw else Color.DIM
        c_reset = "" if args.raw else Color.RESET
        c_red = "" if args.raw else Color.RED

        thinking_started = False
        content_started = False

        try:
            for kind, chunk in stream_chat(
                api_url=cfg["api_url"],
                api_key=cfg["api_key"],
                model=cfg["model"],
                messages=messages,
                temperature=args.temperature,
            ):
                if kind == "reasoning":
                    if not args.no_think and not args.raw:
                        if not thinking_started:
                            sys.stderr.write(f"{c_dim}💭 Pensando: ")
                            thinking_started = True
                        sys.stderr.write(chunk)
                        sys.stderr.flush()
                elif kind == "content":
                    if thinking_started and not content_started:
                        sys.stderr.write(f"{c_reset}\n\n")
                    content_started = True
                    sys.stdout.write(chunk)
                    sys.stdout.flush()

            if thinking_started and not content_started:
                sys.stderr.write(f"{c_reset}\n")

            sys.stdout.write("\n")
            sys.stdout.flush()
            return 0

        except Exception as e:
            sys.stderr.write(f"\n{c_red}Erro: {e}{c_reset}\n")
            return 1

    # Caso contrário, se o terminal for interativo, inicia o REPL
    if sys.stdin.isatty():
        run_interactive(
            cfg=cfg,
            show_thinking=not args.no_think,
            raw_mode=args.raw,
        )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
