# Decisão de design: testes do widget e integração do Modo Agente na interface gráfica.
# Testa o ciclo de vida do stepper, diálogos de aprovação, parada de emergência e
# serialização de histórico de sessão sem depender de modelo externo.

"""Testes da interface gráfica do Modo Agente no Zorin Copilot."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import gi

from zorin_copilot.ui.gi_versions import require_gtk4  # noqa: E402
require_gtk4()
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from zorin_copilot.ai.agent import AgentResult, Step, STOP_ABORTED, STOP_DONE, STOP_REJECTED  # noqa: E402
from zorin_copilot.core.export import render_conversation_markdown  # noqa: E402
from zorin_copilot.core.session import ChatTurn, TopicSession  # noqa: E402
from zorin_copilot.ui.widgets.agent_card import (  # noqa: E402
    AgentExecutionWidget,
    TOOL_LABELS,
    _format_args_summary,
)


class TestAgentTurnSession(unittest.TestCase):
    """Testa a persistência e restauração de dados de execução de agente no ChatTurn."""

    def test_turn_with_agent_result_to_and_from_dict(self):
        agent_data = {
            "objective": "abrir o terminal e listar arquivos",
            "success": True,
            "stop_reason": STOP_DONE,
            "stop_message": "Objetivo concluído.",
            "final_answer": "Terminal aberto com sucesso.",
            "elapsed": 1.5,
            "provider": "ollama",
            "steps": [
                {
                    "index": 0,
                    "tool": "launch_app",
                    "args": {"query": "terminal"},
                    "risk": "safe",
                    "ok": True,
                    "elapsed": 0.4,
                }
            ],
        }

        turn = ChatTurn(
            prompt="abrir terminal",
            answer="Terminal aberto com sucesso.",
            agent_result=agent_data,
        )

        serialized = turn.to_dict()
        self.assertIn("agent_result", serialized)
        self.assertEqual(serialized["agent_result"]["objective"], "abrir o terminal e listar arquivos")

        restored = ChatTurn.from_dict(serialized)
        self.assertIsNotNone(restored.agent_result)
        self.assertEqual(restored.agent_result["stop_reason"], STOP_DONE)
        self.assertEqual(len(restored.agent_result["steps"]), 1)

    def test_topic_session_records_agent_turn(self):
        session = TopicSession(title="Teste Agente")
        agent_data = {
            "objective": "organizar downloads",
            "success": True,
            "stop_reason": STOP_DONE,
            "steps": [],
        }
        turn = session.record_turn(
            prompt="organizar downloads",
            answer="Downloads organizados.",
            agent_result=agent_data,
        )

        self.assertEqual(turn.prompt, "organizar downloads")
        self.assertEqual(turn.agent_result, agent_data)


class TestAgentExecutionWidget(unittest.TestCase):
    """Testa o comportamento do widget visual do Modo Agente."""

    def setUp(self):
        self.mock_ctx = MagicMock()
        self.mock_ctx.chat_stream = MagicMock()

    def test_format_args_summary(self):
        self.assertEqual(_format_args_summary("launch_app", {"query": "firefox"}), "firefox")
        self.assertEqual(_format_args_summary("type_text", {"text": "ola mundo"}), '"ola mundo"')
        self.assertEqual(_format_args_summary("click_element", {"query": "Salvar"}), "Salvar")
        self.assertEqual(_format_args_summary("write_document", {"filename": "relatorio.md"}), "relatorio.md")
        self.assertEqual(_format_args_summary("unknown_tool", {"param1": "val1"}), "param1=val1")

    def test_widget_initialization(self):
        widget = AgentExecutionWidget(
            objective="abrir Firefox e pesquisar linux",
            ctx=self.mock_ctx,
            on_abort=lambda: None,
        )
        self.assertEqual(widget.objective, "abrir Firefox e pesquisar linux")
        self.assertTrue(widget.spinner.get_visible())
        self.assertTrue(widget.abort_btn.get_visible())
        self.assertFalse(widget.approval_box.get_visible())
        self.assertFalse(widget.conclusion_box.get_visible())

    def test_add_step_success(self):
        widget = AgentExecutionWidget(
            objective="abrir calculadora",
            ctx=self.mock_ctx,
        )
        step = Step(
            index=0,
            tool="launch_app",
            args={"query": "gnome-calculator"},
            risk="safe",
            ok=True,
            elapsed=0.5,
        )
        widget.add_step(step)

        self.assertEqual(len(widget.steps), 1)
        self.assertIn("Passo 1", widget.status_lbl.get_text())
        self.mock_ctx.chat_stream.scroll_to_bottom.assert_called()

    def test_prompt_approval_and_approve(self):
        widget = AgentExecutionWidget(
            objective="apagar arquivo teste",
            ctx=self.mock_ctx,
        )
        step = Step(
            index=1,
            tool="write_document",
            args={"filename": "teste.txt"},
            risk="confirm",
            requires_approval=True,
        )

        event = threading.Event()
        result_dict = {"approved": False}

        widget.prompt_approval(step, event, result_dict)
        self.assertTrue(widget.approval_box.get_visible())
        self.assertIn("Confirmação", widget.approval_box.get_first_child().get_last_child().get_text())

        # Simula clique em aprovar
        widget.approve_btn.emit("clicked")
        self.assertTrue(event.is_set())
        self.assertTrue(result_dict["approved"])
        self.assertFalse(widget.approval_box.get_visible())

    def test_prompt_approval_and_reject(self):
        widget = AgentExecutionWidget(
            objective="apagar arquivo teste",
            ctx=self.mock_ctx,
        )
        step = Step(
            index=1,
            tool="write_document",
            args={"filename": "teste.txt"},
            risk="confirm",
            requires_approval=True,
        )

        event = threading.Event()
        result_dict = {"approved": False}

        widget.prompt_approval(step, event, result_dict)
        self.assertTrue(widget.approval_box.get_visible())

        # Simula clique em recusar
        widget.reject_btn.emit("clicked")
        self.assertTrue(event.is_set())
        self.assertFalse(result_dict["approved"])
        self.assertFalse(widget.approval_box.get_visible())

    def test_finish_success(self):
        widget = AgentExecutionWidget(
            objective="abrir terminal",
            ctx=self.mock_ctx,
        )
        result = AgentResult(
            objective="abrir terminal",
            success=True,
            stop_reason=STOP_DONE,
            final_answer="O terminal foi aberto com sucesso.",
            elapsed=2.1,
            provider="local",
            steps=[
                Step(index=0, tool="launch_app", args={"query": "terminal"}, ok=True, elapsed=0.4)
            ],
        )

        widget.finish(result)
        self.assertFalse(widget.spinner.get_visible())
        self.assertFalse(widget.abort_btn.get_visible())
        self.assertTrue(widget.conclusion_box.get_visible())
        self.assertIn("concluído", widget.status_lbl.get_text().lower())
        self.assertIn("terminal foi aberto", widget.answer_lbl.get_text())

    def test_from_result_dict_reconstruction(self):
        turn = ChatTurn(prompt="tarefa histórica", answer="Concluído com sucesso.")
        data = {
            "objective": "tarefa histórica",
            "success": True,
            "stop_reason": STOP_DONE,
            "stop_message": "Objetivo concluído.",
            "final_answer": "Concluído com sucesso.",
            "elapsed": 3.4,
            "provider": "gemini",
            "steps": [
                {
                    "index": 0,
                    "tool": "launch_app",
                    "args": {"query": "gedit"},
                    "ok": True,
                    "elapsed": 0.8,
                },
                {
                    "index": 1,
                    "tool": "type_text",
                    "args": {"text": "Notas de reunião"},
                    "ok": True,
                    "elapsed": 0.2,
                },
            ],
        }

        reconstructed = AgentExecutionWidget.from_result_dict(data, self.mock_ctx, turn)
        self.assertEqual(len(reconstructed.steps), 2)
        self.assertEqual(reconstructed.steps[0].tool, "launch_app")
        self.assertEqual(reconstructed.steps[1].tool, "type_text")
        self.assertTrue(reconstructed.conclusion_box.get_visible())
        self.assertFalse(reconstructed.abort_btn.get_visible())
        self.assertFalse(reconstructed.spinner.get_visible())

    def test_auto_approve_rest_preconfigured(self):
        """Verifica que quando auto_approve_rest está ativo, prompt_approval aprova imediatamente."""
        widget = AgentExecutionWidget(
            objective="escrever vários arquivos",
            ctx=self.mock_ctx,
        )
        widget.auto_approve_rest = True

        step = Step(
            index=0,
            tool="write_document",
            args={"filename": "doc1.txt"},
            risk="confirm",
            requires_approval=True,
        )
        event = threading.Event()
        result_dict = {"approved": False}

        widget.prompt_approval(step, event, result_dict)
        self.assertTrue(event.is_set())
        self.assertTrue(result_dict["approved"])
        self.assertFalse(widget.approval_box.get_visible())
        self.assertIn("auto-aprovada", widget.status_lbl.get_text())

    def test_approve_all_button_workflow(self):
        """Verifica que clicar em 'Aprovar Todas' aprova a ação atual e todas as subsequentes."""
        widget = AgentExecutionWidget(
            objective="organizar arquivos",
            ctx=self.mock_ctx,
        )
        step1 = Step(
            index=0,
            tool="organize_directory",
            args={"directory": "~/Downloads"},
            risk="confirm",
            requires_approval=True,
        )
        event1 = threading.Event()
        res1 = {"approved": False}

        widget.prompt_approval(step1, event1, res1)
        self.assertTrue(widget.approval_box.get_visible())

        # Clica em Aprovar Todas
        widget.approve_all_btn.emit("clicked")
        self.assertTrue(event1.is_set())
        self.assertTrue(res1["approved"])
        self.assertTrue(widget.auto_approve_rest)
        self.assertFalse(widget.approval_box.get_visible())

        # Próxima ação sensível deve auto-aprovar
        step2 = Step(
            index=1,
            tool="write_document",
            args={"filename": "relatorio.txt"},
            risk="confirm",
            requires_approval=True,
        )
        event2 = threading.Event()
        res2 = {"approved": False}
        widget.prompt_approval(step2, event2, res2)
        self.assertTrue(event2.is_set())
        self.assertTrue(res2["approved"])
        self.assertFalse(widget.approval_box.get_visible())

    def test_step_observation_revealer(self):
        """Verifica que observações ricas geram revealer com texto formatado e toggle funcional."""
        widget = AgentExecutionWidget(
            objective="ler arquivo e inspecionar",
            ctx=self.mock_ctx,
        )
        step_ok = Step(
            index=0,
            tool="read_file",
            args={"path": "/etc/os-release"},
            rationale="Verificar distribuição do sistema",
            ok=True,
            observation={"NAME": "Zorin OS", "VERSION": "17"},
            elapsed=0.2,
        )
        widget.add_step(step_ok)

        # O steps_box deve conter o container com a linha e o revealer
        step_container = widget.steps_box.get_first_child()
        self.assertIsNotNone(step_container)

        # Obtém os filhos do container: [row, revealer]
        row = step_container.get_first_child()
        revealer = row.get_next_sibling()
        self.assertIsInstance(revealer, Gtk.Revealer)
        self.assertFalse(revealer.get_reveal_child())  # Passo OK começa fechado

        # O botão de expandir deve estar no row
        expand_btn = row.get_last_child()
        self.assertIsInstance(expand_btn, Gtk.Button)
        expand_btn.emit("clicked")
        self.assertTrue(revealer.get_reveal_child())

        # Testa passo com erro: deve começar revelado automaticamente
        step_err = Step(
            index=1,
            tool="read_file",
            args={"path": "/arquivo/inexistente"},
            ok=False,
            error="Arquivo não encontrado",
            elapsed=0.1,
        )
        widget.add_step(step_err)
        err_container = step_container.get_next_sibling()
        err_revealer = err_container.get_first_child().get_next_sibling()
        self.assertTrue(err_revealer.get_reveal_child())

    def test_undo_button_lifecycle(self):
        """Verifica que o botão de Desfazer só aparece em ações de arquivo e chama undo_last_action."""
        widget = AgentExecutionWidget(
            objective="criar arquivo importante",
            ctx=self.mock_ctx,
        )

        # Sem ferramentas que alteram arquivos -> sem botão de desfazer
        res_no_undo = AgentResult(
            objective="pesquisar",
            success=True,
            stop_reason=STOP_DONE,
            steps=[Step(index=0, tool="launch_app", args={"query": "gedit"}, ok=True)],
        )
        widget.finish(res_no_undo)
        self.assertFalse(widget.undo_btn.get_visible())

        # Com write_document com sucesso -> botão de desfazer visível
        res_undo = AgentResult(
            objective="criar arquivo",
            success=True,
            stop_reason=STOP_DONE,
            steps=[
                Step(index=0, tool="write_document", args={"filename": "test.txt"}, ok=True)
            ],
        )
        widget.finish(res_undo)
        self.assertTrue(widget.undo_btn.get_visible())

        # Clica no botão de desfazer
        widget.undo_btn.emit("clicked")
        self.mock_ctx.undo_last_action.assert_called_once()
        self.assertFalse(widget.undo_btn.get_sensitive())
        self.assertIn("desfeita", widget.undo_btn_lbl.get_text().lower())

    def test_agent_markdown_export_audit_table(self):
        """Verifica que a exportação para Markdown inclui a tabela de auditoria do Modo Agente."""
        session = TopicSession(title="Sessão com Agente")
        agent_data = {
            "objective": "gravar notas e abrir gedit",
            "success": True,
            "stop_reason": STOP_DONE,
            "stop_message": "Objetivo concluído com sucesso.",
            "final_answer": "Arquivo criado e gedit aberto.",
            "elapsed": 2.4,
            "provider": "gemini-2.0-flash",
            "steps": [
                {
                    "index": 0,
                    "tool": "write_document",
                    "args": {"filename": "notas.md"},
                    "ok": True,
                    "elapsed": 0.5,
                    "rationale": "Salvar texto do usuário",
                    "observation": {"path": "/home/user/notas.md", "bytes": 128},
                },
                {
                    "index": 1,
                    "tool": "launch_app",
                    "args": {"query": "gedit"},
                    "ok": True,
                    "elapsed": 0.8,
                },
            ],
        }
        session.record_turn(
            prompt="criar notas.md e abrir gedit",
            answer="Arquivo criado e gedit aberto.",
            agent_result=agent_data,
        )

        md = render_conversation_markdown(session, provider="Google", model="gemini-2.0-flash")
        self.assertIn("### Auditoria de Execução (Modo Agente)", md)
        self.assertIn("| # | Ferramenta | Argumentos | Status | Tempo |", md)
        self.assertIn("| 1 | `write_document` | `notas.md` | OK | 0.5s |", md)
        self.assertIn("| 2 | `launch_app` | `gedit` | OK | 0.8s |", md)
        self.assertIn("<details>", md)
        self.assertIn("Detalhes e Observações das Etapas", md)
        self.assertIn("Salvar texto do usuário", md)


class TestAgentIntegration(unittest.TestCase):
    """Testa a integração entre PromptBar, ChatStream e CopilotWindow para o Modo Agente."""

    def test_chat_stream_renders_agent_widget_for_agent_turn(self):
        from zorin_copilot.ui.widgets.chat_stream import ChatStreamView

        mock_win = MagicMock()
        mock_win.config = MagicMock()
        mock_win.session = MagicMock()
        mock_win.session.turns = []

        chat_stream = ChatStreamView(mock_win)
        turn = ChatTurn(
            prompt="fazer café",
            answer="Feito.",
            agent_result={
                "objective": "fazer café",
                "success": True,
                "stop_reason": STOP_DONE,
                "steps": [],
            },
        )

        widget = chat_stream.create_turn_widget(turn)
        # O segundo filho do turn_box deve ser o AgentExecutionWidget
        children = []
        child = widget.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()

        self.assertEqual(len(children), 2)
        self.assertIsInstance(children[1], AgentExecutionWidget)

    def test_prompt_bar_agent_button_toggle(self):
        from zorin_copilot.ui.widgets.prompt_bar import PromptBar

        mock_win = MagicMock()
        mock_win.agent_mode_active = False
        mock_win.attachment_bar = MagicMock()
        mock_win.attachment_bar.box = Gtk.Box()
        mock_win.vision = MagicMock()
        mock_win.vision.preview_box = Gtk.Box()
        prompt_bar = PromptBar(mock_win)

        # Inicialmente inativo
        self.assertFalse(prompt_bar.agent_btn.get_active())

        # Ativa o botão
        prompt_bar.agent_btn.set_active(True)
        self.assertTrue(mock_win.agent_mode_active)
        self.assertIn("Descreva o objetivo para o Agente", prompt_bar.entry.get_placeholder_text())

        # Desativa o botão
        prompt_bar.agent_btn.set_active(False)
        self.assertFalse(mock_win.agent_mode_active)
        self.assertIn("Peça ao Zorin Copilot", prompt_bar.entry.get_placeholder_text())

    def test_prompt_bar_submit_with_agent_prefix(self):
        from zorin_copilot.ui.widgets.prompt_bar import PromptBar

        mock_win = MagicMock()
        mock_win.agent_mode_active = False
        mock_win._is_busy = False
        mock_win.entry = Gtk.Entry()
        mock_win.attachment_bar = MagicMock()
        mock_win.attachment_bar.box = Gtk.Box()
        mock_win.vision = MagicMock()
        mock_win.vision.preview_box = Gtk.Box()
        mock_win.chat_stream = MagicMock()
        mock_win.session = MagicMock()
        mock_win.session.get_history_for_llm.return_value = []

        prompt_bar = PromptBar(mock_win)
        mock_win.entry.set_text("/agente abrir o navegador e pesquisar linux")

        prompt_bar.submit(prompt_bar.submit_btn)

        # start_agent_execution deve ter sido chamado com o texto sem o prefixo
        mock_win.start_agent_execution.assert_called_once()
        called_args = mock_win.start_agent_execution.call_args[0]
        self.assertEqual(called_args[0], "abrir o navegador e pesquisar linux")


if __name__ == "__main__":
    unittest.main()

