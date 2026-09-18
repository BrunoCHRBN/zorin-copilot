"""Testes de regressão para correção de respostas e UX de agentes (Ollama/Dolphin).

Garante:
1. JSONs com apenas "code" ou "command" não vazam sintaxe crua e geram FIX_COMMAND.
2. Chaves alternativas de modelos locais (response, message, answer) são respeitadas.
3. Tags HTML/Pango (<b>, </b>, <code>, <span>) são limpas ou convertidas para Markdown (**).
4. Abertura de terminal vazio (launch_app: kitty) é convertida em fix_command quando há comando.
5. Caminhos fictícios como /home/usuario são normalizados para a home real do usuário.
6. Consultas técnicas/scripts não sofrem injeção indevida de RAG nem abertura falsa de documentos.
"""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from zorin_copilot.ai.actions import ActionType, DesktopAction
from zorin_copilot.ai.engine import IntentEngine
from zorin_copilot.ai.providers import BaseLLMProvider
from zorin_copilot.core.files import FileManager
from zorin_copilot.core.rag import DocumentSearchResult, LocalDocumentRAG


def test_parse_json_with_code_only():
    raw = '{"code": "nmap -sP 192.168.1.0/24"}'
    explanation, actions = BaseLLMProvider.parse_response_payload(raw)

    assert not explanation.strip().startswith("{")
    assert "nmap -sP 192.168.1.0/24" in explanation
    assert len(actions) >= 1
    assert actions[0].action_type == ActionType.FIX_COMMAND
    assert actions[0].params["command"] == "nmap -sP 192.168.1.0/24"


def test_parse_json_with_command_only():
    raw = '{"command": "python3 scripts/scan.py"}'
    explanation, actions = BaseLLMProvider.parse_response_payload(raw)

    assert not explanation.strip().startswith("{")
    assert "python3 scripts/scan.py" in explanation
    assert len(actions) >= 1
    assert actions[0].action_type == ActionType.FIX_COMMAND
    assert actions[0].params["command"] == "python3 scripts/scan.py"


def test_parse_json_with_alternative_keys():
    raw = '{"response": "Aqui está a análise completa do tráfego.", "actions": []}'
    explanation, actions = BaseLLMProvider.parse_response_payload(raw)

    assert explanation == "Aqui está a análise completa do tráfego."


def test_strip_and_convert_html_tags():
    raw = (
        '{"explanation": "Execute o script em <b>scripts</b>/<b>scan_slop.py</b> '
        'com <code>sudo</code>.", "actions": []}'
    )
    explanation, _ = BaseLLMProvider.parse_response_payload(raw)

    assert "<b>" not in explanation
    assert "</b>" not in explanation
    assert "<code>" not in explanation
    assert "</code>" not in explanation
    assert "**scripts**" in explanation
    assert "**scan_slop.py**" in explanation
    assert "`sudo`" in explanation


def test_launch_app_terminal_converted_to_fix_command():
    raw = """
    {
        "explanation": "Para escanear a rede wifi use o comando:\\n```bash\\nnmcli dev wifi\\n```",
        "actions": [
            {
                "type": "launch_app",
                "target": "kitty"
            }
        ]
    }
    """
    explanation, actions = BaseLLMProvider.parse_response_payload(raw)

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.FIX_COMMAND
    assert actions[0].params["command"] == "nmcli dev wifi"


def test_write_file_directory_sanitizes_dummy_user():
    raw = """
    {
        "explanation": "Script gerado com sucesso.",
        "actions": [
            {
                "type": "write_file",
                "target": "scan_wifi.py",
                "params": {
                    "filename": "scan_wifi.py",
                    "directory": "/home/usuario/scripts"
                }
            }
        ]
    }
    """
    _, actions = BaseLLMProvider.parse_response_payload(raw)

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.WRITE_FILE
    expected_dir = str(Path.home() / "scripts")
    assert actions[0].params["directory"] == expected_dir


def test_file_manager_normalizes_dummy_user_path():
    target = FileManager.resolve_target_path("scan.py", directory="/home/usuario/scripts")
    expected = str(Path.home() / "scripts" / "scan.py")
    assert target == expected


def test_engine_does_not_inject_rag_or_force_open_doc_on_technical_query():
    # Cria RAG mockado que retornaria um blueprint caso fosse consultado
    mock_rag = MagicMock(spec=LocalDocumentRAG)
    mock_rag.search.return_value = [
        DocumentSearchResult(
            file_path="/home/bruno-vsantos/Downloads/academicxore-blueprint-v4.md",
            file_name="academicxore-blueprint-v4.md",
            title="Blueprint",
            page_number=10,
            snippet="arquivo com permissao de escrita",
            rank_score=-5.0,
        )
    ]

    engine = IntentEngine(rag=mock_rag)
    engine.llm_provider.is_configured = MagicMock(return_value=True)
    engine.llm_provider.chat = MagicMock(
        return_value=(
            "Para corrigir a permissão, execute chmod +x no arquivo.",
            [
                DesktopAction(
                    ActionType.FIX_COMMAND,
                    "chmod +x script.sh",
                    {"command": "chmod +x script.sh", "terminal": True},
                    description="Dar permissão de execução",
                )
            ],
        )
    )

    plan = engine.parse("não consigo salvar o arquivo com permissão de root")

    # RAG não deve ter sido consultado para essa dúvida técnica
    mock_rag.search.assert_not_called()

    # Nenhuma ação de OPEN_DOCUMENT deve ser anexada
    open_acts = [a for a in plan.actions if a.action_type == ActionType.OPEN_DOCUMENT]
    assert len(open_acts) == 0
    assert any(a.action_type == ActionType.FIX_COMMAND for a in plan.actions)
