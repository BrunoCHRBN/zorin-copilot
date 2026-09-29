"""Testes do fio entre o app e a consolidação da memória — sem display.

O que se testa aqui é o **efeito**: quem arma o temporizador, quem decide que é hora, quem grava
o estado e quem desarma no fim. A regra de "já venceu?" tem suíte própria
(`tests/test_consolidator.py`).

Técnica: `types.SimpleNamespace` com os métodos da janela **não vinculados**, religados
por `tests/_window_stub.py` — o mesmo padrão de `test_ha_announce_wiring.py`, e pelo mesmo
motivo: subir uma `CopilotWindow` de verdade custa +109 MB e subprocessos de MCP/RAG.

**O motor roda de verdade, contra um banco temporário.** Os caminhos entram por atributo
(`_consolidation_db_path` / `_consolidation_state_path`), então não há dublê do
`MemoryConsolidator` — e também não há como um teste apontar para o banco real do usuário, que
`run_consolidation` **apaga** linhas.
"""

from __future__ import annotations

import inspect
import re
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from zorin_copilot.core.consolidator import (
    ConsolidationState,
    load_consolidation_state,
    save_consolidation_state,
)
from zorin_copilot.core.memory import MemoryManager
from zorin_copilot.ui import app as app_module
from zorin_copilot.ui.app import CopilotWindow

try:  # importado como pacote (`tests._window_stub`) quando há `tests/__init__.py`
    from tests._window_stub import bind_window_methods, missing_collaborators
except ImportError:  # importado solto, conforme o rootdir do pytest
    from _window_stub import bind_window_methods, missing_collaborators  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent
PREFERENCES_PATH = REPO_ROOT / "src" / "zorin_copilot" / "ui" / "preferences.py"


class _WindowStub:
    """Janela de mentira com os métodos reais religados."""

    METHODS = (
        "_init_consolidation",
        "_check_consolidation",
        "_run_consolidation_async",
        "_stop_consolidation",
        "_reconfigure_consolidation",
        "teardown_background_services",
    )

    #: `teardown_background_services` é emprestado só para observar **um** passo do
    #: desligamento; ele toca meia dúzia de subsistemas alheios, então não entra na
    #: guarda de completude abaixo.
    GUARDED_METHODS = tuple(m for m in METHODS if m != "teardown_background_services")

    def __init__(self, **overrides):
        self.config = types.SimpleNamespace(
            memory_consolidation_enabled=True,
            memory_consolidation_days_to_keep=14,
            memory_consolidation_interval_hours=24.0,
        )
        self._consolidation_source_id = 0
        self._consolidation_running = False
        self._consolidation_db_path = None
        self._consolidation_state_path = None
        self._services_torn_down = False
        self.logger = mock.MagicMock()
        self.show_toast = mock.MagicMock()
        bind_window_methods(self, self.METHODS)
        for key, value in overrides.items():
            setattr(self, key, value)


class StubCompletenessTest(unittest.TestCase):
    """A lista `METHODS` do stub envelhece em silêncio — esta guarda impede.

    Mesma guarda de `test_ha_announce_wiring.py`: lê o **código-fonte** dos métodos
    religados e cobra cada `self.<nome>` que eles tocam. Um colaborador novo esquecido
    na lista produz `AttributeError` apontando para o lugar errado.
    """

    def test_stub_tem_tudo_o_que_os_metodos_tocam(self):
        win = _WindowStub()
        faltando = missing_collaborators(win, _WindowStub.GUARDED_METHODS)
        self.assertEqual(faltando, set(), "o stub não tem: " + ", ".join(sorted(faltando)))

    def test_a_guarda_esta_olhando_para_metodos_de_verdade(self):
        self.assertGreaterEqual(len(_WindowStub.GUARDED_METHODS), 5)
        source = inspect.getsource(getattr(CopilotWindow, "_check_consolidation"))
        self.assertIn("_run_consolidation_async", source)

    def test_constantes_de_classe_chegam_ao_stub(self):
        """Um `self.CONSTANTE` legítimo não pode virar "colaborador faltando"."""
        win = _WindowStub()
        self.assertEqual(
            win.CONSOLIDATION_TICK_SECONDS, CopilotWindow.CONSOLIDATION_TICK_SECONDS
        )

    def test_a_copia_de_constantes_e_derivada_e_nao_escrita_a_mao(self):
        """Se a lista fosse escrita à mão, uma constante nova voltaria a ser
        reportada como colaborador faltando — a falha que esta guarda existe
        para evitar."""
        CopilotWindow.CONSTANTE_DE_SONDAGEM = 7
        try:
            self.assertEqual(_WindowStub().CONSTANTE_DE_SONDAGEM, 7)
        finally:
            del CopilotWindow.CONSTANTE_DE_SONDAGEM


def _banco_temporario(tmp: Path) -> Path:
    """Banco de memória vazio, com o schema criado."""
    db = tmp / "memory.db"
    MemoryManager(db_path=db)
    return db


def _memory_manager_apontando_para(db: Path):
    """Faz `MemoryManager()` reportar `db`, para quem o chamar sem argumento.

    `db_path` é atributo de **instância** (atribuído em `__init__`), não
    propriedade de classe — então não existe `MemoryManager.db_path` para
    remendar, e é isso que `mock.patch(..., "MemoryManager.db_path")` reclama.
    Remenda-se a **classe**, que é exatamente o que a produção chama
    (`MemoryManager().db_path` em `_init_consolidation`).
    """
    patcher = mock.patch("zorin_copilot.core.memory.MemoryManager")
    classe = patcher.start()
    classe.return_value.db_path = db
    return patcher


def _log_acao(db: Path, *, quando: datetime, tipo: str = "media_control", alvo: str = "Queen") -> None:
    import sqlite3

    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "INSERT INTO action_logs (timestamp, prompt, action_type, target, params_json, success, message)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (quando.isoformat(), "teste", tipo, alvo, "{}", 1, "ok"),
        )
        con.commit()
    finally:
        con.close()


class OptInTest(unittest.TestCase):
    """Recurso que apaga histórico precisa ser pedido, nunca presumido."""

    def setUp(self):
        gate = mock.patch.object(app_module, "_is_test_environment", return_value=False)
        gate.start()
        self.addCleanup(gate.stop)

    def test_disabled_does_not_arm_the_timer(self):
        win = _WindowStub()
        win.config.memory_consolidation_enabled = False
        with mock.patch.object(app_module.GLib, "timeout_add_seconds") as timer:
            win._init_consolidation()
        timer.assert_not_called()
        self.assertEqual(win._consolidation_source_id, 0)
        self.assertIsNone(win._consolidation_state_path)

    def test_enabled_arms_the_timer_and_checks_at_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _banco_temporario(Path(tmp))
            win = _WindowStub()
            patcher = _memory_manager_apontando_para(db)
            self.addCleanup(patcher.stop)
            with mock.patch.object(app_module.GLib, "idle_add") as idle, mock.patch.object(
                app_module.GLib, "timeout_add_seconds", return_value=4242
            ) as timer:
                win._init_consolidation()
            idle.assert_called_once()  # a verificação do arranque é o que torna durável
            timer.assert_called_once()
            self.assertEqual(timer.call_args.args[0], CopilotWindow.CONSOLIDATION_TICK_SECONDS)
            self.assertEqual(win._consolidation_source_id, 4242)
            self.assertEqual(win._consolidation_db_path, db)
            self.assertEqual(win._consolidation_state_path.parent, db.parent)

    def test_state_path_sits_beside_the_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _banco_temporario(Path(tmp))
            win = _WindowStub()
            patcher = _memory_manager_apontando_para(db)
            self.addCleanup(patcher.stop)
            with mock.patch.object(app_module.GLib, "idle_add"), mock.patch.object(
                app_module.GLib, "timeout_add_seconds", return_value=1
            ):
                win._init_consolidation()
            self.assertEqual(win._consolidation_state_path, db.parent / "consolidation_state.json")


class TestEnvironmentGateTest(unittest.TestCase):
    """Sob o runner, o recurso não pode se armar sozinho.

    O gate existe porque `run_consolidation` **apaga linhas**, e um teste que suba a
    janela apontaria para o banco de quem está testando. É a única barreira entre a
    suíte e o histórico real do usuário — por isso tem teste próprio.
    """

    def test_under_the_runner_it_does_not_arm(self):
        win = _WindowStub()  # config com o recurso ligado
        with mock.patch.object(
            app_module, "_is_test_environment", return_value=True
        ), mock.patch.object(app_module.GLib, "timeout_add_seconds") as timer, mock.patch.object(
            app_module.GLib, "idle_add"
        ) as idle:
            win._init_consolidation()
        timer.assert_not_called()
        idle.assert_not_called()
        self.assertIsNone(win._consolidation_db_path, "resolveu caminho sob o runner")


class DecideAndRunTest(unittest.TestCase):
    """O fio completo: decisão pura + motor de verdade, contra banco temporário."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raiz = Path(self.tmp.name)
        self.db = _banco_temporario(raiz)
        self.estado = raiz / "consolidation_state.json"

    def _win(self, **overrides):
        return _WindowStub(
            _consolidation_db_path=self.db,
            _consolidation_state_path=self.estado,
            **overrides,
        )

    def _espera(self, win, timeout: float = 5.0) -> None:
        """Espera a thread de consolidação terminar.

        A espera é pelo flag, e isso é deliberado: `_run_consolidation_async` só o
        baixa no `finally`, **depois** de gravar o estado. Flag em `False` significa
        "o arquivo já está no disco", não apenas "a thread morreu".
        """
        prazo = time.time() + timeout
        while time.time() < prazo and win._consolidation_running:
            time.sleep(0.01)
        self.assertFalse(win._consolidation_running, "a consolidação não terminou a tempo")

    def test_never_ran_prunes_and_writes_state(self):
        antigo = datetime.now() - timedelta(days=30)
        recente = datetime.now() - timedelta(hours=1)
        _log_acao(self.db, quando=antigo)
        _log_acao(self.db, quando=recente)

        win = self._win()
        self.assertTrue(win._check_consolidation())
        self._espera(win)

        estado = load_consolidation_state(self.estado)
        self.assertIsNotNone(estado.last_run_at, "o estado não foi gravado")
        self.assertEqual(estado.last_logs_pruned, 1, "a linha de 30 dias devia ter sido podada")

    def test_running_twice_in_a_row_does_not_prune_twice(self):
        """O que o estado registra é "eu rodei", não "eu apaguei"."""
        _log_acao(self.db, quando=datetime.now() - timedelta(days=30))
        win = self._win()
        win._check_consolidation()
        self._espera(win)
        primeiro = load_consolidation_state(self.estado)

        # Segunda verificação: já rodou, então não é hora.
        win._check_consolidation()
        self._espera(win)
        self.assertEqual(load_consolidation_state(self.estado), primeiro)

    def test_not_yet_does_not_touch_the_database(self):
        _log_acao(self.db, quando=datetime.now() - timedelta(days=30))
        save_consolidation_state(
            self.estado, ConsolidationState(last_run_at=datetime.now() - timedelta(hours=1))
        )
        win = self._win()
        with mock.patch.object(win, "_run_consolidation_async") as disparo:
            win._check_consolidation()
        disparo.assert_not_called()

    def test_due_after_the_interval_fires(self):
        save_consolidation_state(
            self.estado, ConsolidationState(last_run_at=datetime.now() - timedelta(hours=25))
        )
        win = self._win()
        with mock.patch.object(win, "_run_consolidation_async") as disparo:
            win._check_consolidation()
        disparo.assert_called_once()

    def test_disabled_at_check_time_does_not_fire(self):
        """O interruptor pode ser desligado depois de o timer estar armado."""
        save_consolidation_state(
            self.estado, ConsolidationState(last_run_at=datetime.now() - timedelta(days=9))
        )
        win = self._win()
        win.config.memory_consolidation_enabled = False
        with mock.patch.object(win, "_run_consolidation_async") as disparo:
            win._check_consolidation()
        disparo.assert_not_called()

    def test_clock_skewed_state_fires_instead_of_stalling_forever(self):
        """Estado no futuro: sem a condição, a consolidação nunca mais rodaria."""
        save_consolidation_state(
            self.estado, ConsolidationState(last_run_at=datetime.now() + timedelta(days=30))
        )
        win = self._win()
        with mock.patch.object(win, "_run_consolidation_async") as disparo:
            with self.assertLogs("zorin_copilot.core.consolidator", level="WARNING"):
                win._check_consolidation()
        disparo.assert_called_once()

    def test_missing_paths_do_not_explode(self):
        win = _WindowStub()  # sem caminhos
        self.assertFalse(win._check_consolidation())

    def test_a_running_job_blocks_a_second_one(self):
        """Duas consolidações ao mesmo tempo brigariam pelo mesmo SQLite."""
        win = self._win()
        win._consolidation_running = True
        with mock.patch.object(win, "_run_consolidation_async") as disparo:
            self.assertTrue(win._check_consolidation())
        disparo.assert_not_called()

    def test_corrupt_state_file_falls_back_to_running(self):
        self.estado.write_text('{"last_run_at": "2026-09-2', encoding="utf-8")
        win = self._win()
        with self.assertLogs("zorin_copilot.core.consolidator", level="WARNING"):
            win._check_consolidation()
        self._espera(win)
        self.assertIsNotNone(load_consolidation_state(self.estado).last_run_at)

    def test_days_to_keep_comes_from_the_config(self):
        """O corte é configurável: um dia em vez de catorze."""
        _log_acao(self.db, quando=datetime.now() - timedelta(days=3))
        win = self._win()
        win.config.memory_consolidation_days_to_keep = 1
        win._check_consolidation()
        self._espera(win)
        self.assertEqual(load_consolidation_state(self.estado).last_logs_pruned, 1)

    def test_failure_in_the_engine_is_contained(self):
        """Falha na consolidação não pode derrubar o app nem deixar a trava presa."""
        win = self._win()
        with mock.patch(
            "zorin_copilot.core.consolidator.MemoryConsolidator.run_consolidation",
            side_effect=RuntimeError("sqlite explodiu"),
        ):
            with self.assertLogs("zorin_copilot.ui.app", level="WARNING"):
                win._check_consolidation()
                self._espera(win)
        self.assertFalse(win._consolidation_running, "a trava ficou presa")


class TeardownTest(unittest.TestCase):
    def test_stop_removes_the_glib_source(self):
        win = _WindowStub(_consolidation_source_id=777)
        with mock.patch.object(app_module.GLib, "source_remove") as remover:
            win._stop_consolidation()
        remover.assert_called_once_with(777)
        self.assertEqual(win._consolidation_source_id, 0)

    def test_stop_is_idempotent(self):
        win = _WindowStub()
        with mock.patch.object(app_module.GLib, "source_remove") as remover:
            win._stop_consolidation()
            win._stop_consolidation()
        remover.assert_not_called()

    def test_teardown_step_calls_stop(self):
        """O `timeout_add_seconds` não pode ficar pendurado em janela destruída."""
        source = inspect.getsource(CopilotWindow.teardown_background_services)
        self.assertIn("_stop_consolidation", source)

    def test_reconfigure_stops_before_rearming(self):
        win = _WindowStub()
        ordem: list[str] = []
        with mock.patch.object(win, "_stop_consolidation", lambda: ordem.append("stop")), (
            mock.patch.object(win, "_init_consolidation", lambda: ordem.append("init"))
        ):
            win._reconfigure_consolidation()
        self.assertEqual(ordem, ["stop", "init"], "rearmar sem desarmar deixaria dois timers")


class ConfigWiringTest(unittest.TestCase):
    """O app precisa ler os três campos — e o coletor precisa reescrevê-los."""

    CAMPOS = (
        "memory_consolidation_enabled",
        "memory_consolidation_days_to_keep",
        "memory_consolidation_interval_hours",
    )

    def test_config_has_the_fields_with_safe_defaults(self):
        from zorin_copilot.core.config import CopilotConfig

        cfg = CopilotConfig()
        self.assertIs(cfg.memory_consolidation_enabled, False, "apagar histórico não é default")
        self.assertEqual(cfg.memory_consolidation_days_to_keep, 14)
        self.assertEqual(cfg.memory_consolidation_interval_hours, 24.0)

    def test_app_reads_the_fields(self):
        fonte = (REPO_ROOT / "src" / "zorin_copilot" / "ui" / "app.py").read_text(encoding="utf-8")
        for campo in self.CAMPOS:
            with self.subTest(campo=campo):
                self.assertIn(campo, fonte)

    def test_collector_reattributes_the_new_fields(self):
        """`_collect_current_config` parte de um `CopilotConfig()` novo: campo esquecido
        volta ao default a cada salvamento, e o sintoma é a preferência "não pegar"."""
        source = PREFERENCES_PATH.read_text(encoding="utf-8")
        start = source.index("def _collect_current_config")
        rest = source[start + 1 :]
        match = re.search(r"\n    def ", rest)
        body = rest[: match.start()] if match else rest
        for campo in self.CAMPOS:
            with self.subTest(campo=campo):
                self.assertIn(f"cfg.{campo}", body, f"{campo} não é reatribuído no coletor")

    def test_preferences_rows_exist_and_are_added_to_a_group(self):
        source = PREFERENCES_PATH.read_text(encoding="utf-8")
        for attr in ("self.consolidation_row", "self.consolidation_days_row"):
            with self.subTest(attr=attr):
                self.assertIn(attr, source)
        self.assertIn("maintenance_group.add(self.consolidation_row)", source)
        self.assertIn("maintenance_group.add(self.consolidation_days_row)", source)


if __name__ == "__main__":
    unittest.main()
