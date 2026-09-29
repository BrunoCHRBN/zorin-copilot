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
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

Adw.init()


def _count_children(box) -> int:
    total = 0
    child = box.get_first_child()
    while child is not None:
        total += 1
        child = child.get_next_sibling()
    return total


def _last_child(box):
    child = box.get_first_child()
    last = None
    while child is not None:
        last = child
        child = child.get_next_sibling()
    return last


def _collect_label_text(widget) -> str:
    """Todo o texto de `Gtk.Label` na subárvore, separado por espaço.

    Afirmar pelo rótulo renderizado, e não pelo getter do modelo, é o que pega a
    falha silenciosa de markup: o GTK deixa o rótulo vazio e o getter continua
    devolvendo o texto original.
    """
    parts: list[str] = []
    if isinstance(widget, Gtk.Label):
        parts.append(widget.get_text())
    child = widget.get_first_child()
    while child is not None:
        parts.append(_collect_label_text(child))
        child = child.get_next_sibling()
    return " ".join(p for p in parts if p)

from zorin_copilot.ai.agent import AgentResult, Step, STOP_ABORTED, STOP_DONE, STOP_MAX_STEPS, STOP_REJECTED  # noqa: E402
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

    # ------------------------------------------------- subagentes

    def _subagent_state(self, phase, index=0, total=1, title="Pesquisador", run=None):
        import dataclasses

        from zorin_copilot.ai.subagents import CATALOG, PipelineState

        spec = dataclasses.replace(CATALOG["researcher"], title=title)
        return PipelineState(
            phase=phase,
            index=index,
            total=total,
            spec=spec,
            elapsed=1.0,
            run=run,
        )

    def test_subagent_stage_adds_one_row_per_transition(self):
        from zorin_copilot.ai.subagents import PHASE_END, PHASE_START

        widget = AgentExecutionWidget(objective="pesquisar", ctx=self.mock_ctx)
        antes = _count_children(widget.steps_box)

        widget.add_subagent_stage(self._subagent_state(PHASE_START))
        widget.add_subagent_stage(self._subagent_state(PHASE_END))

        self.assertEqual(_count_children(widget.steps_box), antes + 2)

    def test_subagent_row_is_marked_as_nested_and_not_numbered(self):
        """A linha é trabalho DENTRO de um passo: não leva número de etapa.

        Numerá-la faria a contagem de etapas do agente principal mentir.
        """
        from zorin_copilot.ai.subagents import PHASE_START

        widget = AgentExecutionWidget(objective="pesquisar", ctx=self.mock_ctx)
        widget.add_subagent_stage(self._subagent_state(PHASE_START))

        row = _last_child(widget.steps_box)
        self.assertTrue(row.has_css_class("agent-subagent-row"))
        self.assertEqual(_collect_label_text(row), "1/1 Pesquisador — trabalhando…")

    def test_subagent_end_row_reports_steps_and_time(self):
        from zorin_copilot.ai.subagents import PHASE_END, SubagentRun

        widget = AgentExecutionWidget(objective="pesquisar", ctx=self.mock_ctx)
        run = SubagentRun(
            name="researcher",
            title="Pesquisador",
            objective="o",
            input="i",
            output="relatório",
            ok=True,
            stop_reason="done",
            elapsed=12.4,
            steps=[Step(index=0, tool="web_search", ok=True)],
        )
        widget.add_subagent_stage(self._subagent_state(PHASE_END, run=run))

        texto = _collect_label_text(_last_child(widget.steps_box))
        self.assertIn("concluído", texto)
        self.assertIn("1 passo", texto)
        self.assertIn("12.4s", texto)

    def test_subagent_icon_reflects_the_stop_reason_not_just_ok(self):
        """O ícone não pode dizer "deu certo" num estágio que não terminou.

        `SubagentRun.ok` é `stop_reason == "done"` — um booleano que joga fora o
        motivo. A primeira versão pintava de verde TODO estágio concluído, então
        um estágio parado por orçamento saía com ícone de sucesso ao lado do
        texto "interrompido". Só o render do card revelou isso; nenhuma asserção
        de texto pegaria, porque o texto sempre esteve certo.
        """
        from zorin_copilot.ai.subagents import PHASE_END, SubagentRun

        casos = [
            ("done", True, "success", "concluído"),
            ("max_steps", False, "warning", "interrompido"),
            ("timeout", False, "warning", "interrompido"),
            ("no_provider", False, "error", "falhou"),
        ]
        for motivo, ok, classe, rotulo in casos:
            widget = AgentExecutionWidget(objective="pesquisar", ctx=self.mock_ctx)
            run = SubagentRun(
                name="researcher",
                title="Pesquisador",
                objective="o",
                input="i",
                output="r",
                ok=ok,
                stop_reason=motivo,
                elapsed=1.0,
                steps=[Step(index=0, tool="web_search", ok=True)],
            )
            widget.add_subagent_stage(self._subagent_state(PHASE_END, run=run))

            row = _last_child(widget.steps_box)
            icone = row.get_first_child()
            self.assertIsInstance(icone, Gtk.Image, motivo)
            self.assertTrue(
                icone.has_css_class(classe), f"{motivo}: o ícone esperava '{classe}'"
            )
            self.assertIn(rotulo, _collect_label_text(row), motivo)

    def test_subagent_row_in_progress_is_not_painted_as_finished(self):
        """Enquanto roda, o ícone não pode usar nenhuma cor de desfecho."""
        from zorin_copilot.ai.subagents import PHASE_START

        widget = AgentExecutionWidget(objective="pesquisar", ctx=self.mock_ctx)
        widget.add_subagent_stage(self._subagent_state(PHASE_START))

        icone = _last_child(widget.steps_box).get_first_child()
        self.assertTrue(icone.has_css_class("working"))
        for classe in ("success", "warning", "error"):
            self.assertFalse(icone.has_css_class(classe), classe)

    def test_subagent_start_updates_the_status_line(self):
        from zorin_copilot.ai.subagents import PHASE_START

        widget = AgentExecutionWidget(objective="pesquisar", ctx=self.mock_ctx)
        widget.add_subagent_stage(self._subagent_state(PHASE_START))
        self.assertIn("Pesquisador", widget.status_lbl.get_text())

    def test_subagent_title_is_escaped_so_the_label_never_blanks(self):
        """O mesmo defeito de markup que já apareceu em `Adw.ActionRow`.

        Um `&` cru não dá exceção: o GTK não consegue interpretar o markup, o
        rótulo sai **em branco** e nenhum getter reclama. Medido: com o markup
        cru, `Gtk.Label.get_text()` devolve a própria string de markup e o
        `Pango.Layout` sai **0 px de largura**; escapado, devolve o texto simples
        e o layout mede 170 px.

        Por isso a asserção é sobre o markup **não ter vazado** para o texto: é
        o sinal que distingue os dois casos. Um `assertIn("A & B <x>", texto)`
        sozinho passa nos dois — o teste ficaria verde com o defeito presente.
        """
        from zorin_copilot.ai.subagents import PHASE_START

        widget = AgentExecutionWidget(objective="pesquisar", ctx=self.mock_ctx)
        widget.add_subagent_stage(self._subagent_state(PHASE_START, title="A & B <x>"))

        texto = _collect_label_text(_last_child(widget.steps_box))
        self.assertIn("A & B <x>", texto)
        self.assertNotIn(
            "<b>", texto, "o markup vazou para o texto: o rótulo saiu em branco"
        )

    def test_subagent_icons_exist_in_the_icon_theme(self):
        """Nome de ícone inventado não dá erro — só some. Por isso o teste."""
        from zorin_copilot.ai.subagents import CATALOG
        from zorin_copilot.ui.widgets.agent_card import (
            DEFAULT_SUBAGENT_ICON,
            SUBAGENT_ICONS,
        )

        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        nomes = [DEFAULT_SUBAGENT_ICON, *SUBAGENT_ICONS.values()]
        for nome in nomes:
            self.assertTrue(theme.has_icon(nome), f"ícone inexistente: {nome}")

        # Todo especialista do catálogo precisa de ícone próprio, senão o usuário
        # vê o genérico e perde a pista de qual especialista está trabalhando.
        for especialista in CATALOG:
            self.assertIn(especialista, SUBAGENT_ICONS)

    def test_subagent_icons_are_all_symbolic(self):
        """Regra do sistema visual: só ícones simbólicos do Adwaita."""
        from zorin_copilot.ui.widgets.agent_card import (
            DEFAULT_SUBAGENT_ICON,
            SUBAGENT_ICONS,
        )

        for nome in [DEFAULT_SUBAGENT_ICON, *SUBAGENT_ICONS.values()]:
            self.assertTrue(nome.endswith("-symbolic"), nome)

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

    def test_agent_execution_widget_finish_error(self):
        widget = AgentExecutionWidget(
            objective="tarefa com falha",
            ctx=MagicMock(),
        )
        self.assertTrue(widget.spinner.get_visible())
        self.assertTrue(widget.abort_btn.get_visible())
        self.assertFalse(widget.conclusion_box.get_visible())

        widget.finish_error("Falha de conexão com o modelo")
        self.assertFalse(widget.spinner.get_visible())
        self.assertFalse(widget.abort_btn.get_visible())
        self.assertTrue(widget.conclusion_box.get_visible())
        self.assertIn("Falha de conexão", widget.status_lbl.get_text())
        self.assertIn("Falha de conexão", widget.answer_lbl.get_text())

    def test_start_agent_execution_invokes_turn_creation_without_name_error(self):
        from zorin_copilot.ui.app import CopilotWindow

        mock_win = MagicMock()
        mock_win.config = MagicMock()
        mock_win.config.mcp_enabled = False
        mock_win.config.agent_max_steps = 12
        mock_win.inspector = MagicMock()
        mock_win.executor = MagicMock()
        mock_win.fence = MagicMock()
        mock_win.engine = MagicMock()
        mock_win.prompt_bar = MagicMock()
        mock_win.chat_stream = MagicMock()
        user_bubble = Gtk.Box()
        mock_win.chat_stream._build_user_bubble.return_value = user_bubble
        stream_box = Gtk.Box()
        mock_win.chat_stream.stream_box = stream_box

        with patch("threading.Thread") as mock_thread:
            CopilotWindow.start_agent_execution(mock_win, "listar diretório atual")
            mock_win.prompt_bar.set_busy.assert_called_with(True)
            mock_win.chat_stream.welcome_box.set_visible.assert_called_with(False)
            mock_win.chat_stream._build_user_bubble.assert_called_once()
            # Verifica que o turn_box foi criado e adicionado ao stream_box
            self.assertIsNotNone(stream_box.get_first_child())
            mock_thread.assert_called_once()

    def test_agent_execution_widget_continue_btn_on_max_steps(self):
        mock_ctx = MagicMock()
        mock_ctx.continue_agent_execution = MagicMock()

        widget = AgentExecutionWidget(
            objective="tarefa longa",
            ctx=mock_ctx,
        )
        self.assertFalse(widget.continue_btn.get_visible())

        result = AgentResult(
            objective="tarefa longa",
            success=False,
            stop_reason=STOP_MAX_STEPS,
            steps=[Step(index=0, tool="list_directory", ok=True)],
        )
        widget.finish(result)

        self.assertTrue(widget.conclusion_box.get_visible())
        self.assertTrue(widget.continue_btn.get_visible())
        self.assertTrue(widget.continue_btn.get_sensitive())

        # Ao clicar no botão Continuar, invoca continue_agent_execution
        widget._on_continue_clicked(widget.continue_btn)
        mock_ctx.continue_agent_execution.assert_called_once_with(result, widget, additional_steps=10)

    def test_continue_agent_execution_invokes_thread(self):
        from zorin_copilot.ui.app import CopilotWindow

        mock_win = MagicMock()
        mock_win._is_busy = False
        mock_win.config = MagicMock()
        mock_win.config.mcp_enabled = False
        mock_win.config.agent_max_steps = 15
        mock_win.config.agent_max_seconds = 300.0
        mock_win.inspector = MagicMock()
        mock_win.executor = MagicMock()
        mock_win.fence = MagicMock()
        mock_win.engine = MagicMock()
        mock_win.prompt_bar = MagicMock()
        mock_win.session = MagicMock()
        mock_win.session.get_history_for_llm.return_value = []

        prev_result = AgentResult(
            objective="tarefa analítica",
            success=False,
            stop_reason=STOP_MAX_STEPS,
            steps=[Step(index=0, tool="list_directory", ok=True)],
        )
        mock_widget = MagicMock()

        with patch("threading.Thread") as mock_thread:
            CopilotWindow.continue_agent_execution(mock_win, prev_result, mock_widget, additional_steps=10)
            mock_win.prompt_bar.set_busy.assert_called_with(True)
            mock_thread.assert_called_once()

    def test_git_tools_labels_and_icons(self):
        from zorin_copilot.ui.widgets.agent_card import TOOL_ICONS, TOOL_LABELS, _format_args_summary

        self.assertIn("git_log", TOOL_LABELS)
        self.assertIn("git_status", TOOL_LABELS)
        self.assertEqual(TOOL_LABELS["git_log"], "Histórico Git (Commits)")
        self.assertEqual(TOOL_LABELS["git_status"], "Status do Git")

        self.assertEqual(TOOL_ICONS["git_log"], "vcs-branch-symbolic")
        self.assertEqual(TOOL_ICONS["git_status"], "dialog-information-symbolic")

        summary_log = _format_args_summary("git_log", {"max_count": 5, "path": "src"})
        self.assertIn("5 commits", summary_log)
        self.assertIn("src", summary_log)

        summary_status = _format_args_summary("git_status", {"path": "/home/user/project"})
        self.assertEqual(summary_status, "/home/user/project")

        # search_code & system_processes
        self.assertIn("search_code", TOOL_LABELS)
        self.assertIn("system_processes", TOOL_LABELS)
        self.assertEqual(TOOL_LABELS["search_code"], "Buscar no Código")
        self.assertEqual(TOOL_LABELS["system_processes"], "Processos do Sistema")
        self.assertEqual(TOOL_ICONS["search_code"], "system-search-symbolic")
        self.assertEqual(TOOL_ICONS["system_processes"], "utilities-system-monitor-symbolic")

        summary_search = _format_args_summary("search_code", {"pattern": "calculate", "path": "src", "file_glob": "*.py"})
        self.assertIn('"calculate"', summary_search)
        self.assertIn("em src", summary_search)
        self.assertIn("(*.py)", summary_search)

        summary_procs = _format_args_summary("system_processes", {"filter": "python", "limit": 10})
        self.assertIn('filtro "python"', summary_procs)
        self.assertIn("top 10", summary_procs)


# --------------------------------------------------------------------------- #
# Cancelamento do Modo Agente (PROPOSTA_INTERRUPCAO_AGENTE.md)
# --------------------------------------------------------------------------- #

from test_ui_refactor import run_loop_until  # noqa: E402


def _mock_copilot_window():
    """`CopilotWindow` dublê: todos os atributos usados pelas rotas de agente
    são MagicMock, e o que precisa de valor concreto é definido."""
    win = MagicMock()
    win.config = MagicMock()
    win.config.mcp_enabled = False
    win.config.agent_max_steps = 15
    win.config.agent_max_seconds = 300.0
    win.config.agent_adaptive_budget = True
    win.inspector = MagicMock()
    win.executor = MagicMock()
    win.fence = MagicMock()
    win.engine = MagicMock()
    win.prompt_bar = MagicMock()
    win.chat_stream = MagicMock()
    # `start_agent_execution` faz `turn_box.append(_build_user_bubble(...))` num
    # `Gtk.Box` REAL. Se `_build_user_bubble` devolver MagicMock, o append levanta
    # TypeError no caminho SÍNCRONO, o `except` da função põe `_is_busy=False` e o
    # teste passa SEM nunca iniciar o thread — a guarda do cleanup fica vazia.
    # Medido: com o MagicMock, a mutação "start: cleanup removido" sobrevivia.
    win.chat_stream._build_user_bubble.return_value = Gtk.Box()
    win.sidebar = MagicMock()
    win.vision = MagicMock()
    win.entry = MagicMock()
    win.header = MagicMock()
    win.status_bar = MagicMock()
    win.window_title = MagicMock()
    win.session = MagicMock()
    win.session.get_history_for_llm.return_value = []
    win._is_busy = False
    win._active_agent_loop = None
    win._active_agent_widget = None
    win._agent_watchdog_source = None
    win.show_toast = MagicMock()
    return win


def _agent_window():
    """`CopilotWindow` dublê que EXECUTA os métodos reais de cancelamento.

    `_mock_copilot_window()` deixa `win.metodo` como MagicMock, então chamar
    `self.metodo()` dentro de `_on_new_topic` não produz efeito colateral — as
    asserções de `_is_busy`/`_active_agent_loop` ficariam verdes com o defeito
    presente. Aqui ligamos os métodos reais (só eles; o resto continua MagicMock)
    para que o ciclo de fim rode de verdade sobre o dublê.
    """
    from zorin_copilot.ui.app import CopilotWindow

    base = _mock_copilot_window()
    # `base` não é instância de CopilotWindow: atribuir a função desvincula o
    # `self`. Cada closure reinjeta `base` como primeiro argumento, então chamar
    # `self.metodo()` dentro de `start_agent_execution` executa o método real.
    base.abort_active_agent = lambda: CopilotWindow.abort_active_agent(base)
    base._arm_agent_watchdog = lambda: CopilotWindow._arm_agent_watchdog(base)
    base._disarm_agent_watchdog = lambda: CopilotWindow._disarm_agent_watchdog(base)
    base._fire_agent_watchdog = lambda: CopilotWindow._fire_agent_watchdog(base)
    base._teardown_active_agent_ui = lambda: CopilotWindow._teardown_active_agent_ui(base)
    base._on_agent_finished = (lambda *a, **k: CopilotWindow._on_agent_finished(base, *a, **k))
    base._on_new_topic = lambda: CopilotWindow._on_new_topic(base)
    return base


def test_agent_thread_finally_cleans_up_on_setup_exception():
    """Defeito 3: falha no setup do agent_thread não pode deixar a UI presa."""
    from zorin_copilot.ui.app import CopilotWindow

    win = _agent_window()
    with patch("zorin_copilot.ai.agent_router.AgentRouter", side_effect=RuntimeError("sem modelo")):
        CopilotWindow.start_agent_execution(win, "listar diretório atual")
        ok = run_loop_until(lambda: not win._is_busy, timeout_ms=2000)
    assert ok, "a UI não foi destravada após falha no setup do agente (Defeito 3)"
    assert win._active_agent_loop is None


def test_continue_thread_finally_cleans_up_on_setup_exception():
    """Defeito 3 (caminho de continuação): idem, com AgentResult prévio."""
    from zorin_copilot.ai.agent import AgentResult, Step, STOP_MAX_STEPS

    from zorin_copilot.ui.app import CopilotWindow

    win = _agent_window()
    prev = AgentResult(
        objective="tarefa", success=False, stop_reason=STOP_MAX_STEPS,
        steps=[Step(index=0, tool="noop", ok=True)],
    )
    widget = MagicMock()
    widget.get_visible.return_value = False
    with patch("zorin_copilot.ai.agent_router.AgentRouter", side_effect=RuntimeError("sem modelo")):
        CopilotWindow.continue_agent_execution(win, prev, widget, additional_steps=10)
        ok = run_loop_until(lambda: not win._is_busy, timeout_ms=2000)
    assert ok, "a UI não foi destravada após falha no setup da continuação (Defeito 3)"


def test_new_topic_aborts_running_agent():
    """Defeito 2 / Frente B: nova conversa aborta o agente e libera a UI."""
    from zorin_copilot.ui.app import CopilotWindow

    win = _agent_window()
    win._is_busy = True
    loop_mock = MagicMock()
    win._active_agent_loop = loop_mock
    widget = MagicMock()
    widget.get_visible.return_value = True
    win._active_agent_widget = widget

    CopilotWindow._on_new_topic(win)

    assert not win._is_busy
    assert win._active_agent_loop is None
    assert win._active_agent_widget is None
    widget.set_visible.assert_called_with(False)
    loop_mock.abort.assert_called_once()


def test_resume_topic_aborts_running_agent():
    """Defeito 2 / Frente B: retomar conversa aborta o agente e libera a UI."""
    from zorin_copilot.ui.app import CopilotWindow

    win = _agent_window()
    win._is_busy = True
    loop_mock = MagicMock()
    win._active_agent_loop = loop_mock
    widget = MagicMock()
    widget.get_visible.return_value = True
    win._active_agent_widget = widget
    win.engine.memory.get_chat_topic.return_value = {"id": "t1", "title": "T"}

    CopilotWindow._resume_topic(win, "t1")

    assert not win._is_busy
    assert win._active_agent_loop is None
    assert win._active_agent_widget is None
    widget.set_visible.assert_called_with(False)
    loop_mock.abort.assert_called_once()


def test_delete_active_topic_aborts_running_agent():
    """Defeito 2 / Frente B: apagar a conversa ativa aborta o agente."""
    from zorin_copilot.ui.app import CopilotWindow
    from zorin_copilot.ui.widgets.sidebar import SidebarPanel

    win = _agent_window()
    win._is_busy = True
    loop_mock = MagicMock()
    win._active_agent_loop = loop_mock
    widget = MagicMock()
    widget.get_visible.return_value = True
    win._active_agent_widget = widget
    win.session.id = "t1"

    sidebar_mock = MagicMock()
    sidebar_mock.ctx = win
    SidebarPanel.on_delete_topic(sidebar_mock, "t1")

    assert not win._is_busy
    assert win._active_agent_loop is None
    assert win._active_agent_widget is None
    widget.set_visible.assert_called_with(False)
    loop_mock.abort.assert_called_once()


def test_clear_history_aborts_running_agent():
    """Defeito 2 / Frente B: limpar o histórico aborta o agente."""
    from zorin_copilot.ui.widgets.sidebar import SidebarPanel

    win = _agent_window()
    win._is_busy = True
    loop_mock = MagicMock()
    win._active_agent_loop = loop_mock
    widget = MagicMock()
    widget.get_visible.return_value = True
    win._active_agent_widget = widget
    win.engine.memory.list_chat_topics.return_value = []

    sidebar_mock = MagicMock()
    sidebar_mock.ctx = win
    SidebarPanel.clear_history(sidebar_mock)

    assert not win._is_busy
    assert win._active_agent_loop is None
    assert win._active_agent_widget is None
    widget.set_visible.assert_called_with(False)
    loop_mock.abort.assert_called_once()


def test_submit_busy_shows_toast():
    """Defeito 4 / Frente C: enviar ocupado dá feedback em vez de engolir."""
    from zorin_copilot.ui.widgets.prompt_bar import PromptBar

    win = _agent_window()
    win._is_busy = True
    win.entry = Gtk.Entry()
    win.attachment_bar.box = Gtk.Box()
    win.vision.preview_box = Gtk.Box()
    win.attachments = []
    win._active_image_bytes = None

    prompt_bar = PromptBar(win)
    # `PromptBar.__init__` faz `ctx.entry = self.entry`, então o texto precisa ser
    # definido DEPOIS da construção (igual a test_prompt_bar_submit_with_agent_prefix).
    win.entry.set_text("oi")
    prompt_bar.submit(prompt_bar.submit_btn)

    win.show_toast.assert_called_once()
    assert "Esc" in win.show_toast.call_args[0][0]


def test_stale_agent_result_not_recorded_on_session_switch():
    """Defeito 2 / Frente B.2 (opção a): resultado obsoleto não entra na sessão nova."""
    from zorin_copilot.ai.agent import AgentResult, Step, STOP_DONE

    from zorin_copilot.ui.app import CopilotWindow

    win = _agent_window()
    win._is_busy = True
    win._active_agent_loop = MagicMock()
    old_widget = MagicMock()
    win._active_agent_widget = old_widget
    # Simula a troca de conversa: o widget ativo foi anulado pelo teardown.
    win._active_agent_widget = None

    result = AgentResult(
        objective="x", success=True, stop_reason=STOP_DONE,
        steps=[Step(index=0, tool="noop", ok=True)], final_answer="ok",
    )
    CopilotWindow._on_agent_finished(win, result, "x", old_widget, "")

    win.session.record_turn.assert_not_called()
    assert win._is_busy is False


def test_agent_watchdog_libera_ui_quando_excede_limite():
    """Defeito 1 / Frente A.3: watchdog libera a UI se o agente passa do limite."""
    from zorin_copilot.ui.app import CopilotWindow

    win = _agent_window()
    win._is_busy = True
    win._active_agent_loop = MagicMock()

    # Mantém a UI ocupada além do limite (simula planejador travado em rede ruim).
    def bloqueado():
        time.sleep(5.0)

    threading.Thread(target=bloqueado, daemon=True).start()
    # Agenda o disparo do watchdog com prazo curto (o de produção é max_seconds+30).
    win._agent_watchdog_source = GLib.timeout_add(120, win._fire_agent_watchdog)

    ok = run_loop_until(lambda: not win._is_busy, timeout_ms=1500)
    win.prompt_bar.set_busy.assert_called_with(False)
    assert ok, "watchdog não liberou a UI no limite interno (Defeito 1)"


if __name__ == "__main__":
    unittest.main()



