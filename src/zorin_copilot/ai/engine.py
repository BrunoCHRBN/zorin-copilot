# Decisão de design: motor híbrido em camadas — comandos de sistema e lançamento de apps resolvem instantaneamente (0ms); perguntas e comandos livres utilizam provedores de LLM configurados (Gemini, Ollama, OpenAI) com fallback guiado para serviços comuns da web.

"""Motor de interpretação semântica de intenções para o Zorin Copilot."""

from __future__ import annotations

import ast
import logging
import math
import operator
import re
import time
from datetime import date, datetime, timedelta
from typing import Any

from .actions import ActionPlan, ActionType, DesktopAction
from .providers import BaseLLMProvider, get_llm_provider
from ..core.a11y import DesktopInspector
from ..core.apps import AppManager
from ..core.clipboard import ClipboardService
from ..core.config import CopilotConfig
from ..core.files import FileManager
from ..core.media import MediaPlayerManager
from ..core.memory import MemoryManager
from ..core.rag import LocalDocumentRAG
from ..core.usage import TokenUsageTracker
from ..core.web_search import WebSearchClient

logger = logging.getLogger(__name__)


def get_easter_date(year: int) -> date:
    """Calcula a data do Domingo de Páscoa usando o algoritmo de Meeus/Jones/Butcher."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def get_holiday_calendar(year: int) -> list[tuple[str, list[str], date, str]]:
    """Gera o calendário completo de feriados nacionais e datas comemorativas brasileiras para um ano."""
    easter = get_easter_date(year)
    carnaval = easter - timedelta(days=47)
    sexta_santa = easter - timedelta(days=2)
    corpus_christi = easter + timedelta(days=60)

    # Segundo domingo de maio (Dia das Mães)
    may1 = date(year, 5, 1)
    mothers_day = date(year, 5, 1 + (6 - may1.weekday()) % 7 + 7)

    # Segundo domingo de agosto (Dia dos Pais)
    aug1 = date(year, 8, 1)
    fathers_day = date(year, 8, 1 + (6 - aug1.weekday()) % 7 + 7)

    return [
        ("Ano Novo", ["ano novo", "reveillon", "réveillon", "confraternizacao universal", "confraternização universal", "1 de janeiro", "primeiro de janeiro"], date(year, 1, 1), "Feriado Nacional"),
        ("Carnaval", ["carnaval"], carnaval, "Ponto Facultativo"),
        ("Sexta-feira Santa", ["sexta feira santa", "sexta-feira santa", "paixao de cristo", "paixão de cristo"], sexta_santa, "Feriado Nacional"),
        ("Páscoa", ["pascoa", "páscoa"], easter, "Data Comemorativa"),
        ("Tiradentes", ["tiradentes"], date(year, 4, 21), "Feriado Nacional"),
        ("Dia do Trabalhador", ["dia do trabalhador", "dia do trabalho", "1 de maio", "primeiro de maio"], date(year, 5, 1), "Feriado Nacional"),
        ("Dia das Mães", ["dia das maes", "dia das mães"], mothers_day, "Data Comemorativa"),
        ("Dia dos Namorados", ["dia dos namorados"], date(year, 6, 12), "Data Comemorativa"),
        ("Corpus Christi", ["corpus christi"], corpus_christi, "Ponto Facultativo"),
        ("Dia dos Pais", ["dia dos pais"], fathers_day, "Data Comemorativa"),
        ("Independência do Brasil", ["independencia", "independência", "7 de setembro", "sete de setembro"], date(year, 9, 7), "Feriado Nacional"),
        ("Nossa Senhora Aparecida / Dia das Crianças", ["nossa senhora aparecida", "aparecida", "dia das criancas", "dia das crianças", "12 de outubro"], date(year, 10, 12), "Feriado Nacional"),
        ("Dia de Finados", ["finados", "dia de finados", "2 de novembro"], date(year, 11, 2), "Feriado Nacional"),
        ("Proclamação da República", ["proclamacao da republica", "proclamação da república", "15 de novembro"], date(year, 11, 15), "Feriado Nacional"),
        ("Dia da Consciência Negra", ["consciencia negra", "consciência negra", "20 de novembro"], date(year, 11, 20), "Feriado Nacional"),
        ("Véspera de Natal", ["vespera de natal", "véspera de natal", "24 de dezembro"], date(year, 12, 24), "Data Comemorativa"),
        ("Natal", ["natal", "25 de dezembro"], date(year, 12, 25), "Feriado Nacional"),
        ("Véspera de Ano Novo / Fim de Ano", ["vespera de ano novo", "véspera de ano novo", "fim de ano", "fim do ano", "31 de dezembro"], date(year, 12, 31), "Data Comemorativa"),
    ]


class IntentEngine:
    """Interpreta solicitações do usuário e gera planos de ação para o desktop."""

    def __init__(
        self,
        inspector: DesktopInspector | None = None,
        config: CopilotConfig | None = None,
        memory: MemoryManager | None = None,
        search_client: WebSearchClient | None = None,
        rag: LocalDocumentRAG | None = None,
    ):
        self.inspector = inspector or DesktopInspector()
        self.config = config or CopilotConfig.load()
        self.memory = memory or MemoryManager()
        self.search_client = search_client or WebSearchClient()
        self.rag = rag or LocalDocumentRAG(memory=self.memory)
        self.llm_provider: BaseLLMProvider = get_llm_provider(self.config)
        # Tracker de tokens por sessão (uma janela). Ligado ao provedor para que
        # cada resposta de modelo acumule seu consumo automaticamente.
        self.usage_tracker = TokenUsageTracker()
        self.llm_provider.usage_tracker = self.usage_tracker

    def reload_config(self, config: CopilotConfig | None = None) -> None:
        """Recarrega a configuração e reinicializa o provedor de LLM."""
        self.config = config or CopilotConfig.load()
        self.llm_provider = get_llm_provider(self.config)
        self.llm_provider.usage_tracker = self.usage_tracker

    def _get_situational_context(self) -> str:
        """Coleta contexto situacional silencioso do desktop (janela ativa, horário, mídia tocando)."""
        details = []
        try:
            active_app, active_win, _ = self.inspector.get_active_window_info()
            if active_app:
                title_info = f" ('{active_win[:60]}')" if active_win else ""
                details.append(f"Janela em foco: {active_app}{title_info}")
        except Exception as exc:
            logger.debug(f"Erro ao obter janela em foco para contexto: {exc}")

        try:
            now = datetime.now()
            today = now.date()
            dias_semana_full = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
            meses_full = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
            dia_extenso = f"{dias_semana_full[today.weekday()]}, {today.day:02d} de {meses_full[today.month - 1]} de {today.year}"

            hour = now.hour
            if 5 <= hour < 12:
                periodo = "Manhã"
            elif 12 <= hour < 18:
                periodo = "Tarde"
            elif 18 <= hour < 24:
                periodo = "Noite"
            else:
                periodo = "Madrugada"
            details.append(f"Data e Horário: {dia_extenso} às {now.strftime('%H:%M')} ({periodo})")

            upcoming = [h for h in get_holiday_calendar(today.year) if h[2] >= today]
            if not upcoming:
                upcoming = [h for h in get_holiday_calendar(today.year + 1) if h[2] >= today]
            if upcoming:
                h_name, _, h_date, _ = upcoming[0]
                diff = (h_date - today).days
                when = "hoje" if diff == 0 else ("amanhã" if diff == 1 else f"em {diff} dias")
                details.append(f"Próximo Feriado de Referência: {h_name} em {h_date.day:02d}/{h_date.month:02d}/{h_date.year} ({when})")
        except Exception:
            pass

        try:
            track = MediaPlayerManager.get_track_info()
            if track.playback_status == "Playing" and track.title:
                player = track.player_name.replace("org.mpris.MediaPlayer2.", "").capitalize() if track.player_name else "Player"
                artist_info = f" por '{track.artist}'" if track.artist else ""
                details.append(f"Mídia tocando: '{track.title}'{artist_info} ({player})")
        except Exception:
            pass

        if not details:
            return ""
        return "[Contexto Situacional do Desktop]:\n" + "\n".join(f"- {d}" for d in details)

    def _resolve_calendar_or_math(self, prompt: str) -> ActionPlan | None:
        """Resolve perguntas sobre calendário, feriados, contagem de datas e expressões matemáticas com precisão determinística (0ms)."""
        low = prompt.strip().lower()

        # -------------------------------------------------------------
        # 1. Matemática determinística segura (AST)
        # -------------------------------------------------------------
        # Percentual: ex: "quanto é 15% de 800", "calcule 20% de 250"
        m_pct = re.search(r'(\d+(?:[.,]\d+)?)\s*%\s*(?:de|das|dos)?\s*(\d+(?:[.,]\d+)?)', low)
        if m_pct and any(w in low for w in ["quanto", "calcule", "calcular", "%", "por cento", "porcento"]):
            try:
                pct = float(m_pct.group(1).replace(",", "."))
                base = float(m_pct.group(2).replace(",", "."))
                res = (pct / 100.0) * base
                res_str = f"{res:g}"
                resp = f"**{m_pct.group(1)}% de {m_pct.group(2)}** é igual a **{res_str}**."
                return ActionPlan(
                    thought=resp,
                    actions=[DesktopAction(ActionType.ANSWER, resp, description="Cálculo de porcentagem")]
                )
            except Exception:
                pass

        # Raiz quadrada: ex: "raiz quadrada de 144", "calcule a raiz de 81"
        m_sqrt = re.search(r'raiz\s*(?:quadrada)?\s*(?:de)?\s*(\d+(?:[.,]\d+)?)', low)
        if m_sqrt:
            try:
                val = float(m_sqrt.group(1).replace(",", "."))
                if val >= 0:
                    res = math.isqrt(int(val)) if val.is_integer() and math.isqrt(int(val))**2 == int(val) else math.sqrt(val)
                    resp = f"A raiz quadrada de **{m_sqrt.group(1)}** é **{res:g}**."
                    return ActionPlan(
                        thought=resp,
                        actions=[DesktopAction(ActionType.ANSWER, resp, description="Cálculo de raiz quadrada")]
                    )
            except Exception:
                pass

        # Expressões aritméticas simples: ex: "quanto é 25 * 4", "calcule 1500 / 12", "quanto dá (10 + 5) * 3"
        m_calc = re.search(r'(?:quanto (?:é|e|dá|da)|calcule|calcular|resultado de)\s*(.+)', low)
        if m_calc:
            raw_expr = m_calc.group(1).strip().rstrip("?!. ")
            expr = (
                raw_expr.replace(" vezes ", " * ")
                .replace(" x ", " * ")
                .replace(" dividido por ", " / ")
                .replace(" divido por ", " / ")
                .replace(" mais ", " + ")
                .replace(" menos ", " - ")
                .replace("^", "**")
            )
            if re.fullmatch(r'[\d\s\+\-\*\/\(\)\.\,]+', expr) and any(op in expr for op in "+-*/"):
                clean_expr = expr.replace(",", ".")
                safe_ops = {
                    ast.Add: operator.add,
                    ast.Sub: operator.sub,
                    ast.Mult: operator.mul,
                    ast.Div: operator.truediv,
                    ast.FloorDiv: operator.floordiv,
                    ast.Mod: operator.mod,
                    ast.Pow: operator.pow,
                    ast.USub: operator.neg,
                    ast.UAdd: operator.pos,
                }
                def eval_ast(node: Any) -> float | int:
                    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                        return node.value
                    if isinstance(node, ast.BinOp) and type(node.op) in safe_ops:
                        left = eval_ast(node.left)
                        right = eval_ast(node.right)
                        return safe_ops[type(node.op)](left, right)
                    if isinstance(node, ast.UnaryOp) and type(node.op) in safe_ops:
                        return safe_ops[type(node.op)](eval_ast(node.operand))
                    raise ValueError("Operação não permitida")

                try:
                    tree = ast.parse(clean_expr, mode="eval")
                    res = eval_ast(tree.body)
                    res_str = f"{res:g}"
                    resp = f"O resultado de **{raw_expr}** é **{res_str}**."
                    return ActionPlan(
                        thought=resp,
                        actions=[DesktopAction(ActionType.ANSWER, resp, description="Cálculo matemático determinístico")]
                    )
                except Exception:
                    pass

        # -------------------------------------------------------------
        # 2. Calendário, Feriados e Contagem de Dias/Semanas/Dias da Semana
        # -------------------------------------------------------------
        today = date.today()
        dias_semana_pt = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
        meses_pt = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
        weekday_map = {
            "domingo": 6, "domingos": 6,
            "sabado": 5, "sabados": 5, "sábado": 5, "sábados": 5,
            "sexta": 4, "sextas": 4, "sexta-feira": 4, "sextas-feiras": 4,
            "quinta": 3, "quintas": 3, "quinta-feira": 3, "quintas-feiras": 3,
            "quarta": 2, "quartas": 2, "quarta-feira": 2, "quartas-feiras": 2,
            "terca": 1, "tercas": 1, "terça": 1, "terças": 1, "terça-feira": 1, "terças-feiras": 1,
            "segunda": 0, "segundas": 0, "segunda-feira": 0, "segundas-feiras": 0,
        }

        # A) Próximo(s) feriado(s)
        if any(q in low for q in ["proximo feriado", "próximo feriado", "proximos feriados", "próximos feriados", "qual o feriado", "qual é o feriado"]):
            upcoming = [h for h in get_holiday_calendar(today.year) if h[2] >= today]
            if not upcoming:
                upcoming = [h for h in get_holiday_calendar(today.year + 1) if h[2] >= today]
            if upcoming:
                name, _, h_date, cat = upcoming[0]
                diff = (h_date - today).days
                dia_sem = dias_semana_pt[h_date.weekday()]
                mes_str = meses_pt[h_date.month - 1]
                when_str = "hoje" if diff == 0 else ("amanhã" if diff == 1 else f"daqui a **{diff} dias**")
                resp = f"O próximo feriado é **{name}**, no dia **{h_date.day:02d} de {mes_str} de {h_date.year}** ({dia_sem}), {when_str}."
                if len(upcoming) > 1:
                    resp += "\n\n**Próximos feriados seguintes:**\n"
                    for h_name, _, d, _ in upcoming[1:5]:
                        resp += f"• **{h_name}**: {d.day:02d} de {meses_pt[d.month - 1]} ({dias_semana_pt[d.weekday()]})\n"
                return ActionPlan(
                    thought=resp,
                    actions=[DesktopAction(ActionType.ANSWER, resp, description="Próximo feriado nacional")]
                )

        # B) Busca de evento ou feriado específico (Natal, Páscoa, Ano Novo, etc.)
        year_match = re.search(r'\b(20\d\d)\b', low)
        req_year = int(year_match.group(1)) if year_match else None
        search_years = [req_year] if req_year else [today.year, today.year + 1]

        matched_event = None
        for y in search_years:
            for h_name, aliases, h_date, cat in get_holiday_calendar(y):
                if any(alias in low for alias in aliases):
                    if req_year or h_date >= today:
                        matched_event = (h_name, h_date, cat)
                        break
            if matched_event:
                break

        if not matched_event:
            return None

        event_name, event_date, cat = matched_event
        diff_days = (event_date - today).days
        event_weekday = dias_semana_pt[event_date.weekday()]
        today_weekday = dias_semana_pt[today.weekday()]
        mes_str = meses_pt[event_date.month - 1]

        # 1. Contagem de um dia da semana específico (ex: "quantos domingos faltam para o natal")
        found_weekday = None
        for w_name, w_idx in weekday_map.items():
            if re.search(rf'\bquant[oa]s?\s+{w_name}\b', low) or re.search(rf'\b{w_name}\s+(?:faltam|restam|ate|até)\b', low):
                found_weekday = (w_name, w_idx)
                break

        if found_weekday:
            w_name, w_idx = found_weekday
            future_count = sum(1 for d in range(1, diff_days + 1) if (today + timedelta(days=d)).weekday() == w_idx)
            today_is_target = (today.weekday() == w_idx)
            count_detail = f"**{future_count} {w_name}** restantes até lá"
            if today_is_target:
                count_detail += f" (ou **{future_count + 1}** considerando o dia de hoje)"

            weeks = diff_days // 7
            week_text = f" (cerca de {weeks} semanas)" if weeks > 0 else ""
            resp = (
                f"Hoje é {today_weekday}, {today.day:02d} de {meses_pt[today.month - 1]} de {today.year}.\n\n"
                f"O **{event_name}** ({event_date.day:02d} de {mes_str} de {event_date.year}) cairá em uma **{event_weekday}**.\n"
                f"Faltam **{diff_days} dias**{week_text}, com {count_detail}."
            )
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description=f"Contagem de {w_name} até {event_name}")]
            )

        # 2. Contagem geral de dias ou semanas ("quantos dias faltam para o natal", "quanto tempo falta para...")
        if any(q in low for q in ["quantos dias faltam", "quantos dias até", "quantos dias ate", "quanto tempo falta", "quantas semanas faltam", "dias faltam"]):
            weeks = diff_days // 7
            week_str = f" (cerca de **{weeks} semanas**)" if weeks > 0 else ""
            resp = (
                f"Hoje é {today_weekday}, {today.day:02d} de {meses_pt[today.month - 1]} de {today.year}.\n\n"
                f"Faltam exatamente **{diff_days} dias**{week_str} para o **{event_name}** "
                f"({event_date.day:02d} de {mes_str} de {event_date.year}, {event_weekday})."
            )
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description=f"Dias restantes até {event_name}")]
            )

        # 3. Dia da semana ou data ("que dia cai o natal", "quando é o natal", "que dia da semana é o natal")
        if any(q in low for q in ["que dia cai", "que dia sera", "que dia será", "quando é", "quando e", "qual o dia do", "qual é o dia do", "que dia da semana"]):
            when_str = "hoje" if diff_days == 0 else ("amanhã" if diff_days == 1 else f"faltando **{diff_days} dias**")
            resp = (
                f"O **{event_name}** em {event_date.year} será no dia **{event_date.day:02d} de {mes_str}** "
                f"(uma **{event_weekday}**), {when_str}."
            )
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description=f"Data de {event_name}")]
            )

        return None

    def parse(
        self,
        prompt: str,
        history: list[dict[str, str]] | None = None,
        image_bytes: bytes | None = None,
        is_area_capture: bool = False,
    ) -> ActionPlan:
        prompt_clean = prompt.strip()
        low = prompt_clean.lower()

        # =========================================================================
        # -1. MODO VISUAL MULTIMODAL (Análise de tela ou recorte de área)
        # =========================================================================
        if image_bytes:
            if not prompt_clean or any(
                low == w
                for w in [
                    "analise", "leia", "veja", "o que tem na tela", "analisar tela",
                    "analise minha tela", "analise a tela", "print", "screenshot",
                    "analise este recorte", "analise o recorte", "recorte",
                ]
            ):
                if is_area_capture:
                    prompt_clean = (
                        "Analise detalhadamente o conteúdo deste recorte de tela selecionado no Zorin OS. "
                        "Identifique e leia qualquer texto, código, diálogo, mensagem de erro ou dados visíveis. "
                        "Explique claramente o que significa e indique a solução ou próximos passos recomendados."
                    )
                else:
                    prompt_clean = (
                        "Analise esta captura de tela completa do desktop no Zorin OS. "
                        "Identifique as janelas ativas, mensagens de erro ou informações visíveis e explique o que está acontecendo e como proceder."
                    )

            if self.llm_provider.is_configured():
                try:
                    app_names = [a.get_name() for a in AppManager.get_all_apps() if a.get_name()]
                    context_parts = [self.memory.get_context_summary()]
                    situational = self._get_situational_context()
                    if situational:
                        context_parts.append(situational)
                    context_summary = "\n\n".join(p for p in context_parts if p)
                    explanation, actions = self.llm_provider.chat(
                        prompt_clean,
                        app_list=app_names,
                        context_summary=context_summary,
                        history=history,
                        image_bytes=image_bytes,
                    )
                    if not actions:
                        actions = [
                            DesktopAction(
                                ActionType.ANSWER,
                                explanation,
                                description="Análise visual do Zorin Copilot",
                            )
                        ]
                    ocr_act = next((a for a in actions if a.action_type == ActionType.SMART_OCR), None)
                    ext_text = ocr_act.target if ocr_act else None
                    ext_kind = ocr_act.params.get("kind", "texto") if ocr_act else "text"
                    return ActionPlan(
                        thought=explanation,
                        actions=actions,
                        raw_response=explanation,
                        extracted_text=ext_text,
                        extracted_kind=ext_kind,
                    )
                except Exception as exc:
                    logger.error(f"Erro na análise visual da imagem: {exc}")
                    return ActionPlan(
                        thought=f"Ocorreu um erro ao processar a imagem com a IA ({self.config.provider}):\n\n{exc}",
                        actions=[
                            DesktopAction(
                                ActionType.ANSWER,
                                f"Falha na visão: {exc}",
                                description="Erro de comunicação com o modelo",
                            )
                        ],
                    )
            else:
                return ActionPlan(
                    thought=(
                        "A captura da tela foi realizada com sucesso! Porém, para fazer a leitura inteligente "
                        "do conteúdo da imagem, é necessário conectar sua chave do Google Gemini nas Preferências (⚙️)."
                    ),
                    actions=[
                        DesktopAction(
                            ActionType.ANSWER,
                            "IA não configurada para visão computacional.",
                            description="Configurar chave de API para análise visual",
                        )
                    ],
                )

        # =========================================================================
        # 0. BASE DE CONHECIMENTO: Memorização e Aprendizado Explícito
        # =========================================================================
        learn_match = re.match(
            r"^(?:lembre-se que|lembre que|guarde que|salve que|anote que|grave que)\s+(.+)$",
            prompt_clean,
            flags=re.I,
        )
        if learn_match:
            fact_content = learn_match.group(1).strip()
            fact_key = f"fato_{int(time.time())}"
            self.memory.save_fact(fact_key, fact_content, category="usuario", source="usuario")
            return ActionPlan(
                thought=(
                    f"Anotado! Lembro disso nas próximas vezes:\n\n"
                    f"• \"{fact_content}\""
                ),
                actions=[
                    DesktopAction(
                        ActionType.ANSWER,
                        f"Conhecimento memorizado: '{fact_content}'",
                        description="Salvo na base de conhecimento",
                    )
                ],
            )

        # =========================================================================
        # 0.5 ÁREA DE TRANSFERÊNCIA: Clipboard Inteligente (Texto, Código e Imagem)
        # =========================================================================
        is_clipboard_intent = (
            any(w in low for w in ["copiad", "copiei", "acabei de copiar", "clipboard", "área de transferência", "area de transferencia"])
            or low in [
                "traduza o texto selecionado para o inglês",
                "traduza o texto selecionado para o ingles",
                "traduza o texto selecionado",
                "traduzir o texto selecionado",
                "traduza a seleção",
                "traduza a selecao",
                "corrija a gramática e formalize este e-mail",
                "corrija a gramatica e formalize este e-mail",
                "formalize este e-mail",
                "formalize este email",
                "formalize este texto",
                "corrija este e-mail",
                "corrija este email",
                "analisar_copiado",
                "analisar copiado",
            ]
            or (
                any(q in low for q in ["traduza", "traduzir", "corrija", "corrigir", "formalize", "resuma", "resumir", "explique"])
                and any(t in low for t in ["selecionado", "seleção", "selecao", "este texto", "este e-mail", "este email", "este código", "este codigo"])
                and len(prompt_clean.split()) <= 12
            )
        )

        if is_clipboard_intent and not image_bytes:
            content_type, clip_content = ClipboardService.get_content()

            if content_type == "empty" or not clip_content:
                thought = (
                    "A área de transferência está vazia no momento.\n\n"
                    "**Como usar o Clipboard Inteligente:**\n"
                    "1. Selecione o código, texto, e-mail ou imagem em qualquer aplicativo;\n"
                    "2. Pressione **Ctrl+C** para copiar;\n"
                    "3. Volte aqui e clique em **📋 Analisar Copiado** ou peça para explicar, traduzir ou formalizar!"
                )
                return ActionPlan(
                    thought=thought,
                    actions=[
                        DesktopAction(
                            ActionType.ANSWER,
                            "Área de transferência vazia. Copie algum conteúdo (Ctrl+C) e tente novamente.",
                            description="Aguardando conteúdo copiado na área de transferência",
                        )
                    ],
                    raw_response=thought,
                )

            if content_type == "image" and isinstance(clip_content, bytes):
                # Processa imagem da área de transferência via visão computacional multimodal
                if self.llm_provider.is_configured():
                    try:
                        app_names = [a.get_name() for a in AppManager.get_all_apps() if a.get_name()]
                        context_summary = self.memory.get_context_summary()
                        vision_prompt = (
                            "Analise detalhadamente esta imagem copiada da área de transferência no Zorin OS. "
                            "Identifique qualquer texto, código, diálogo, diagrama ou mensagem visível e explique seu conteúdo com precisão."
                        )
                        explanation, actions = self.llm_provider.chat(
                            vision_prompt,
                            app_list=app_names,
                            context_summary=context_summary,
                            history=history,
                            image_bytes=clip_content,
                        )
                        if not actions:
                            actions = [
                                DesktopAction(
                                    ActionType.ANSWER,
                                    explanation,
                                    description="Análise da imagem copiada na área de transferência",
                                )
                            ]
                        ocr_act = next((a for a in actions if a.action_type == ActionType.SMART_OCR), None)
                        ext_text = ocr_act.target if ocr_act else None
                        ext_kind = ocr_act.params.get("kind", "texto") if ocr_act else "text"
                        return ActionPlan(
                            thought=explanation,
                            actions=actions,
                            raw_response=explanation,
                            extracted_text=ext_text,
                            extracted_kind=ext_kind,
                        )
                    except Exception as exc:
                        logger.error(f"Erro na análise visual do clipboard: {exc}")
                        return ActionPlan(
                            thought=f"Ocorreu um erro ao processar a imagem do clipboard com a IA ({self.config.provider}):\n\n{exc}",
                            actions=[DesktopAction(ActionType.ANSWER, str(exc), description="Erro de comunicação com a IA")],
                        )
                else:
                    thought = (
                        "Imagem detectada na área de transferência!\n\n"
                        "Para que a IA possa ler e analisar o conteúdo da imagem, "
                        "configure sua chave do Google Gemini nas Preferências (⚙️)."
                    )
                    return ActionPlan(
                        thought=thought,
                        actions=[
                            DesktopAction(
                                ActionType.ANSWER,
                                "Configure o Gemini para leitura visual do clipboard.",
                                description="IA não configurada para visão computacional",
                            )
                        ],
                        raw_response=thought,
                    )

            if content_type == "text" and isinstance(clip_content, str):
                # Formulação do prompt contextual enriquecido
                if any(w in low for w in ["expliq", "o que faz", "codigo", "código", "program", "funcao", "função", "script", "bug"]):
                    enriched_prompt = (
                        "O usuário copiou o seguinte trecho de código para a área de transferência:\n\n"
                        f"```\n{clip_content}\n```\n\n"
                        "Por favor, analise e explique detalhadamente:\n"
                        "1. **Finalidade:** O que este código faz;\n"
                        "2. **Lógica & Componentes:** Como funciona e que módulos ou sintaxes utiliza;\n"
                        "3. **Melhorias & Boas Práticas:** Sugestões de otimização, legibilidade ou potenciais bugs."
                    )
                elif any(w in low for w in ["traduz", "ingles", "inglês", "portugues", "português", "espanhol", "idioma"]):
                    enriched_prompt = (
                        "O usuário copiou o seguinte texto para a área de transferência:\n\n"
                        f"\"{clip_content}\"\n\n"
                        "Por favor, traduza este conteúdo para o inglês (ou para o idioma indicado), priorizando uma linguagem fluente, natural e precisa. Se houver expressões idiomáticas ou termos específicos, aponte breves notas de tradução."
                    )
                elif any(w in low for w in ["corrija", "corrigir", "formaliz", "gramatica", "gramática", "e-mail", "email", "melhor"]):
                    enriched_prompt = (
                        "O usuário copiou o seguinte texto/e-mail para a área de transferência:\n\n"
                        f"\"{clip_content}\"\n\n"
                        "Por favor, faça a revisão deste texto:\n"
                        "1. **Correção Gramatical:** Corrija concordância, pontuação, acentuação e clareza;\n"
                        "2. **Versão Formal:** Reescreva em formato profissional, cortês e polido;\n"
                        "3. **Texto Pronto:** Apresente o texto final pronto para cópia/envio."
                    )
                elif any(w in low for w in ["resum", "sintetiz", "topicos", "tópicos", "principais pontos"]):
                    enriched_prompt = (
                        "O usuário copiou o seguinte texto para a área de transferência:\n\n"
                        f"\"{clip_content}\"\n\n"
                        "Por favor, elabore um resumo conciso e estruturado em tópicos (bullet points), destacando as ideias centrais, conclusões e decisões mais importantes."
                    )
                else:
                    lines = clip_content.strip().splitlines()
                    looks_like_code = (
                        any(k in clip_content for k in ["def ", "import ", "class ", "function", "const ", "var ", "let ", "return ", "SELECT ", "FROM ", "<div>", "public static"])
                        or (len(lines) > 2 and any(l.startswith("    ") or l.startswith("\t") for l in lines))
                    )
                    if looks_like_code:
                        enriched_prompt = (
                            "O usuário copiou o seguinte trecho de código para a área de transferência:\n\n"
                            f"```\n{clip_content}\n```\n\n"
                            "Por favor, analise este código: explique o que ele faz, sua estrutura, lógica e aponte sugestões de otimização ou correções se aplicável."
                        )
                    else:
                        instruction = prompt_clean if low not in ("analisar copiado", "analisar_copiado", "analise o copiado", "analise o que copiei", "clipboard") else "Analise detalhadamente o conteúdo acima, explique seu propósito e forneça pontos de atenção ou ações recomendadas."
                        enriched_prompt = (
                            "O usuário copiou o seguinte conteúdo para a área de transferência:\n\n"
                            f"\"{clip_content}\"\n\n"
                            f"Instrução do usuário: {instruction}"
                        )

                if self.llm_provider.is_configured():
                    try:
                        app_names = [a.get_name() for a in AppManager.get_all_apps() if a.get_name()]
                        context_summary = self.memory.get_context_summary()
                        explanation, actions = self.llm_provider.chat(
                            enriched_prompt,
                            app_list=app_names,
                            context_summary=context_summary,
                            history=history,
                        )
                        if not actions:
                            actions = [
                                DesktopAction(
                                    ActionType.ANSWER,
                                    explanation,
                                    description="Análise do conteúdo da área de transferência",
                                )
                            ]
                        return ActionPlan(thought=explanation, actions=actions, raw_response=explanation)
                    except Exception as exc:
                        logger.error(f"Erro na análise de texto do clipboard: {exc}")
                        return ActionPlan(
                            thought=f"Ocorreu um erro ao comunicar com a IA ({self.config.provider}):\n\n{exc}",
                            actions=[DesktopAction(ActionType.ANSWER, str(exc), description="Erro de comunicação com a IA")],
                        )
                else:
                    preview = ClipboardService.get_preview(60)
                    thought = (
                        f"Conteúdo detectado na área de transferência ({len(clip_content)} caracteres):\n\n"
                        f"> {preview}\n\n"
                        "Para que a IA explique este código, traduza o texto, corrija o e-mail ou gere resumos, "
                        "configure sua chave do Google Gemini ou Ollama nas Preferências (⚙️)."
                    )
                    return ActionPlan(
                        thought=thought,
                        actions=[
                            DesktopAction(
                                ActionType.ANSWER,
                                "Configure um provedor de IA nas Preferências (⚙️) para habilitar a análise do clipboard.",
                                description="IA não configurada para análise de clipboard",
                            )
                        ],
                        raw_response=thought,
                    )

        # =========================================================================
        # 1. CAMADA RÁPIDA LOCAL: Relógio do Sistema e Configurações (0ms)
        # =========================================================================

        # Resolução Instantânea e Determinística: Calendário, Feriados e Cálculos Matemáticos (0ms)
        calc_or_calendar_plan = self._resolve_calendar_or_math(prompt_clean)
        if calc_or_calendar_plan:
            return calc_or_calendar_plan

        # Relógio, Data e Calendário Local Instantâneo (0ms)
        dias_sem = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
        meses_pt = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
        now = datetime.now()

        # Amanhã
        if any(q in low for q in ["que dia sera amanha", "que dia será amanhã", "data de amanha", "data de amanhã", "dia de amanha", "dia de amanhã"]):
            tom = now + timedelta(days=1)
            dia_str = dias_sem[tom.weekday()]
            mes_str = meses_pt[tom.month - 1]
            resp = f"Amanhã será **{dia_str}**, {tom.day:02d} de {mes_str} de {tom.year}."
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description="Data e dia da semana de amanhã")],
            )

        # Hoje
        if any(q in low for q in ["que dia é hoje", "que dia e hoje", "data de hoje", "qual a data de hoje", "em que dia estamos", "que dia da semana é hoje", "que dia da semana e hoje"]):
            dia_str = dias_sem[now.weekday()]
            mes_str = meses_pt[now.month - 1]
            resp = f"Hoje é **{dia_str}**, {now.day:02d} de {mes_str} de {now.year}."
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description="Data e dia da semana atual")],
            )

        # Horário
        if any(q in low for q in [
            "que horas são", "que horas sao", "qual a hora atual", "horario atual", "horário atual",
            "que horas", "diga as horas", "fale as horas", "quantas horas", "que hora é", "que hora e",
        ]) or low.rstrip("?!. ") in ["horas", "hora"]:
            resp = f"Agora são exatamente **{now.strftime('%H:%M')}**."
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description="Hora atual do sistema")],
            )

        # Cortesia e Agradecimento Rápido (0ms)
        clean_courtesy = low.rstrip("!., ")
        if clean_courtesy in [
            "muito obrigado", "muito obrigada", "obrigado", "obrigada",
            "valeu", "valeu mesmo", "agradeço", "obrigado copilot", "muito obrigado copilot", "obrigadão",
        ]:
            resp = "Por nada! Estou sempre aqui no seu desktop para o que precisar."
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description="Resposta de cortesia")],
            )

        # Saudações Rápidas (0ms)
        if clean_courtesy in [
            "olá", "ola", "oi", "opa", "e aí", "e ai", "bom dia", "boa tarde", "boa noite",
            "olá copilot", "ola copilot", "oi copilot"
        ]:
            hour = now.hour
            periodo = "Bom dia" if 5 <= hour < 12 else ("Boa tarde" if 12 <= hour < 18 else "Boa noite")
            user_name = self.memory.get_user_name() if hasattr(self.memory, "get_user_name") else ""
            saudacao = f"{periodo}, {user_name}!" if user_name else f"{periodo}!"
            resp = f"{saudacao} Como posso ajudar você agora no Zorin OS?"
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description="Saudação rápida")],
            )

        # Apresentação e Capacidades do Copilot ("o que você pode fazer", "quem é você")
        if any(w in low for w in [
            "o que você pode fazer", "o que voce pode fazer",
            "o que você faz", "o que voce faz",
            "o que vc pode fazer", "o que vc faz",
            "quais são suas funções", "quais sao suas funcoes", "quais suas funções", "quais suas funcoes",
            "quais suas habilidades", "quais são suas habilidades",
            "quem é você", "quem e você", "quem e voce", "quem e vc", "quem é vc",
            "como você pode me ajudar", "como voce pode me ajudar",
        ]):
            resp = (
                "Eu sou o Zorin Copilot, seu assistente inteligente integrado ao Zorin OS!\n\n"
                "Aqui estão as minhas principais capacidades:\n"
                "• **Abrir e controlar aplicativos:** Iniciar navegadores, terminais, reprodutores e outros programas.\n"
                "• **Ajustes de sistema:** Controlar volume, tema visual e janelas.\n"
                "• **Visão computacional local:** Analisar sua tela inteira ou recortes de erros com IA offline.\n"
                "• **Conversa por voz contínua:** Bater papo, tirar dúvidas e executar tarefas diretamente por áudio.\n"
                "• **Inteligência e produtividade:** Fazer pesquisas na web, ler páginas e gerenciar arquivos."
            )
            return ActionPlan(
                thought=resp,
                actions=[DesktopAction(ActionType.ANSWER, resp, description="Apresentação das capacidades do Zorin Copilot")],
                raw_response=resp,
            )

        # Esquema de Cores (Modo Escuro / Claro)
        if any(w in low for w in ["modo escuro", "tema escuro", "dark mode", "tema dark"]):
            return ActionPlan(
                thought="Ativação do modo escuro do sistema",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "modo_escuro",
                        {"setting": "dark_mode", "value": True},
                        description="Ativar Modo Escuro no Zorin OS",
                    )
                ],
            )
        if any(w in low for w in ["modo claro", "tema claro", "light mode", "tema light"]):
            return ActionPlan(
                thought="Ativação do modo claro do sistema",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "modo_claro",
                        {"setting": "dark_mode", "value": False},
                        description="Ativar Modo Claro no Zorin OS",
                    )
                ],
            )

        # Luz Noturna
        if any(w in low for w in ["luz noturna", "filtro azul", "night light"]):
            return ActionPlan(
                thought="Alternar estado da luz noturna",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "luz_noturna",
                        {"setting": "night_light"},
                        description="Alternar Luz Noturna",
                    )
                ],
            )

        # Volume e Áudio
        if any(w in low for w in ["aumentar volume", "aumenta o volume", "mais volume", "subir o som"]):
            return ActionPlan(
                thought="Aumentar o volume do áudio",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "volume_up",
                        {"setting": "volume", "change": "up"},
                        description="Aumentar volume em 5%",
                    )
                ],
            )
        if any(w in low for w in ["diminuir volume", "diminui o volume", "menos volume", "abaixar o som"]):
            return ActionPlan(
                thought="Reduzir o volume do áudio",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "volume_down",
                        {"setting": "volume", "change": "down"},
                        description="Diminuir volume em 5%",
                    )
                ],
            )
        if any(w in low for w in ["mutar", "mudo", "silenciar", "desmutar"]):
            return ActionPlan(
                thought="Mutar ou desmutar o áudio",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "volume_mute",
                        {"setting": "volume", "change": "mute"},
                        description="Mutar/Desmutar áudio do sistema",
                    )
                ],
            )

        # Bloqueio e Captura de Tela
        if any(w in low for w in ["bloquear tela", "bloquear computador", "bloqueie"]):
            return ActionPlan(
                thought="Bloquear a sessão do usuário",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "lock",
                        {"setting": "lock"},
                        description="Bloquear tela do computador",
                    )
                ],
            )
        # Análise Visual da Tela
        if any(w in low for w in ["analise minha tela", "analise a tela", "o que tem na minha tela", "o que está na minha tela", "leia minha tela", "ler minha tela", "analise esse erro", "analise o erro", "analisar tela", "ler tela"]):
            return ActionPlan(
                thought=(
                    "Para analisar a tela com precisão, você pode usar os botões de visão computacional:\n\n"
                    "• **✂️ Recortar Área da Tela:** Selecione com o mouse exatamente a área ou erro que deseja ler.\n"
                    "• **🖥️ Capturar Tela Inteira:** Lê toda a área de trabalho de uma vez só.\n\n"
                    "Ao selecionar, o Copilot faz a leitura e o diagnóstico automaticamente!"
                ),
                actions=[
                    DesktopAction(
                        ActionType.CAPTURE_SCREEN,
                        "area",
                        description="Recortar área da tela para leitura imediata",
                    ),
                    DesktopAction(
                        ActionType.CAPTURE_SCREEN,
                        "fullscreen",
                        description="Capturar tela inteira para análise",
                    ),
                ],
            )

        if any(w in low for w in ["tirar print", "print da tela", "screenshot", "capturar tela"]):
            return ActionPlan(
                thought="Abrir ferramenta de captura de tela",
                actions=[
                    DesktopAction(
                        ActionType.SYSTEM_CONTROL,
                        "screenshot",
                        {"setting": "screenshot"},
                        description="Capturar tela do desktop",
                    )
                ],
            )

        # Notificações explícitas
        if low.startswith(("notificar ", "lembrete ", "aviso ")):
            msg = re.sub(r"^(notificar|lembrete|aviso)\s+", "", prompt_clean, flags=re.I)
            return ActionPlan(
                thought="Criar notificação no desktop",
                actions=[
                    DesktopAction(
                        ActionType.NOTIFY,
                        msg,
                        {"message": msg},
                        description=f"Exibir notificação: '{msg}'",
                    )
                ],
            )

        # =========================================================================
        # 1.5 CONTROLE DE MÍDIA E ORGANIZAÇÃO RÁPIDA (0ms)
        # =========================================================================

        # Integrações Profundas de Aplicações e Serviços (Spotify, Gmail, Google Drive/Docs, YouTube, Maps)
        try:
            from ..core.app_integrations import AppIntentRouter
            app_plan = AppIntentRouter.resolve_intent(prompt_clean)
            if app_plan:
                return app_plan
        except Exception as exc:
            logger.debug(f"Erro no AppIntentRouter: {exc}")

        # Controle de Mídia / Spotify (Pausar)
        if any(w in low for w in [
            "pausar musica", "pausar música", "pausa a musica", "pausa a música",
            "pausa musica", "pausa música", "pausa o spotify", "pausar spotify",
            "pausar som", "pausa som", "parar a musica", "para a musica"
        ]):
            return ActionPlan(
                thought="Pausar reprodução de mídia",
                actions=[
                    DesktopAction(
                        ActionType.MEDIA_CONTROL,
                        "pause",
                        {"action": "pause"},
                        description="Pausar mídia (Spotify/reprodutor ativo)",
                    )
                ],
            )

        # Controle de Mídia / Spotify (Tocar / Continuar)
        if any(w in low for w in [
            "tocar musica", "tocar música", "play na musica", "play na música",
            "play musica", "continuar musica", "continuar música", "retomar musica",
            "iniciar musica", "tocar spotify"
        ]):
            return ActionPlan(
                thought="Iniciar/retomar reprodução de mídia",
                actions=[
                    DesktopAction(
                        ActionType.MEDIA_CONTROL,
                        "play",
                        {"action": "play"},
                        description="Tocar mídia (Spotify/reprodutor ativo)",
                    )
                ],
            )

        # Controle de Mídia / Spotify (Avançar)
        if any(w in low for w in [
            "proxima musica", "próxima música", "proxima faixa", "próxima faixa",
            "pular musica", "pula a musica", "avancar musica", "avançar música",
            "pular faixa", "proximo som"
        ]):
            return ActionPlan(
                thought="Avançar para a próxima faixa",
                actions=[
                    DesktopAction(
                        ActionType.MEDIA_CONTROL,
                        "next",
                        {"action": "next"},
                        description="Avançar faixa (Spotify/reprodutor ativo)",
                    )
                ],
            )

        # Controle de Mídia / Spotify (Voltar)
        if any(w in low for w in [
            "musica anterior", "música anterior", "faixa anterior", "voltar musica",
            "volta a musica", "retroceder musica", "retroceder faixa"
        ]):
            return ActionPlan(
                thought="Retroceder para a faixa anterior",
                actions=[
                    DesktopAction(
                        ActionType.MEDIA_CONTROL,
                        "previous",
                        {"action": "previous"},
                        description="Faixa anterior (Spotify/reprodutor ativo)",
                    )
                ],
            )

        # Controle de Mídia / Spotify (Status / Faixa atual)
        if any(w in low for w in [
            "qual musica esta tocando", "qual música está tocando", "que musica e essa",
            "que música é essa", "qual e a musica", "qual é a música", "musica atual",
            "música atual", "status da musica", "status da música", "o que esta tocando",
            "o que está tocando"
        ]):
            info = MediaPlayerManager.get_track_info()
            summary = info.summary()
            return ActionPlan(
                thought=summary,
                actions=[
                    DesktopAction(
                        ActionType.MEDIA_CONTROL,
                        "get_status",
                        {"action": "get_status"},
                        description="Consultar faixa e status de mídia atual",
                    )
                ],
            )

        # Organização de Pastas (Downloads / Documentos)
        if any(w in low for w in [
            "organizar downloads", "organizar meus downloads", "organizar a pasta de downloads",
            "organizar pasta de downloads", "organize downloads", "organize meus downloads",
            "organize a pasta de downloads", "organize minha pasta de downloads",
            "arrumar downloads", "limpar downloads"
        ]):
            return ActionPlan(
                thought="Organização inteligente da pasta Downloads por categorias seguras",
                actions=[
                    DesktopAction(
                        ActionType.ORGANIZE_FILES,
                        "~/Downloads",
                        {"directory": "~/Downloads", "dry_run": False},
                        description="Organizar pasta ~/Downloads em subpastas categorizadas",
                    )
                ],
            )

        # =========================================================================
        # 1.2 NAVEGAÇÃO WEB APROFUNDADA (Pesquisas Detalhadas e Leitura de Páginas)
        # =========================================================================

        # Pesquisa Aprofundada / Deep Research
        if any(t in low for t in [
            "pesquisa aprofundada", "pesquisa detalhada", "pesquise aprofundado",
            "pesquise em detalhes", "deep search", "investigue sobre",
            "relatório detalhado sobre", "relatorio detalhado sobre"
        ]):
            from ..core.web_search import DeepWebResearcher
            deep_q = re.sub(
                r"^(?:faça uma pesquisa aprofundada sobre|faça uma pesquisa detalhada sobre|pesquisa aprofundada sobre|pesquisa detalhada sobre|pesquise em detalhes sobre|pesquise aprofundadamente sobre|deep search|investigue sobre)\s+",
                "",
                prompt_clean,
                flags=re.I,
            ).strip() or prompt_clean

            researcher = DeepWebResearcher(search_client=self.search_client)
            res = researcher.deep_search(
                deep_q,
                llm_provider=self.llm_provider if self.llm_provider.is_configured() else None,
            )
            actions = [
                DesktopAction(
                    ActionType.DEEP_RESEARCH,
                    deep_q,
                    {"sources": res.get("sources", [])},
                    description=f"Pesquisa aprofundada sobre '{deep_q}'",
                )
            ]
            if res.get("sources"):
                first_src = res["sources"][0]
                actions.append(
                    DesktopAction(
                        ActionType.OPEN_URL,
                        first_src["url"],
                        description=f"Abrir fonte: {first_src['title'][:40]}...",
                    )
                )
            return ActionPlan(thought=res.get("report") or res.get("summary", ""), actions=actions)

        # Leitura de Páginas Abertas ou URLs
        url_match = re.search(r"https?://[^\s]+", prompt_clean)
        is_read_page = (
            any(t in low for t in [
                "leia a página aberta", "leia a pagina aberta", "leia o site aberto",
                "resuma a página aberta", "resuma a pagina aberta", "o que diz a página aberta",
                "o que tem na página do navegador", "o que tem na pagina do navegador",
                "leia a url", "leia o link", "resuma a url", "resuma o link",
                "leia a página", "leia a pagina"
            ])
            or (bool(url_match) and any(w in low for w in ["leia", "resuma", "analise", "o que diz", "conteúdo", "conteudo"]))
        )
        if is_read_page:
            from ..core.browser import BrowserManager
            target_url = url_match.group(0) if url_match else None
            page_data = BrowserManager.read_page(url=target_url)

            if page_data.get("success"):
                title = page_data.get("title") or target_url or "Página aberta"
                content_text = page_data.get("text", "")
                url_found = page_data.get("url") or target_url or ""

                actions = [
                    DesktopAction(
                        ActionType.READ_PAGE,
                        url_found or title,
                        {"url": url_found, "title": title},
                        description=f"Leitura de página web: '{title[:45]}'",
                    )
                ]
                if url_found:
                    actions.append(DesktopAction(ActionType.OPEN_URL, url_found, description=f"Abrir no navegador: {title[:40]}..."))

                # Se houver LLM configurado, sintetiza análise sob medida
                if self.llm_provider.is_configured() and content_text:
                    try:
                        read_prompt = (
                            f"Você é o Zorin Copilot. Abaixo está o conteúdo extraído da página web "
                            f"'{title}' ({url_found}). Analise e responda à dúvida do usuário com clareza e precisão em português:\n\n"
                            f"[CONTEÚDO DA PÁGINA]:\n{content_text[:7000]}\n\n"
                            f"[SOLICITAÇÃO DO USUÁRIO]:\n{prompt_clean}"
                        )
                        explanation, _ = self.llm_provider.chat(read_prompt)
                        return ActionPlan(thought=explanation, actions=actions)
                    except Exception as exc:
                        logger.warning(f"Erro ao processar conteúdo da página com LLM: {exc}")

                thought = (
                    f"### 🌐 Leitura da Página: **{title}**\n\n"
                    f"{page_data.get('description', '')}\n\n"
                    f"{content_text[:1200]}...\n\n"
                    f"*(Conteúdo completo extraído com {page_data.get('length', 0)} caracteres)*"
                )
                return ActionPlan(thought=thought, actions=actions)
            else:
                return ActionPlan(
                    thought=page_data.get("text", "Não foi possível ler o conteúdo da página."),
                    actions=[],
                )

        # Busca semântica e localização em documentos pessoais (RAG Local: PDFs, contratos, planilhas)
        is_rag_query = any(w in low for w in [
            "buscar documento", "buscar documentos", "busque nos meus documentos", "busque no meu documento",
            "buscar nos meus documentos", "buscar em documentos", "pesquisar documento", "pesquisar documentos",
            "pesquise nos meus documentos", "procurar documento", "procure nos meus documentos",
            "procurar nos meus documentos", "onde está o documento", "onde esta o documento",
            "encontre o documento", "encontre nos documentos", "procurar arquivo", "procure o arquivo",
            "busque o arquivo", "buscar arquivo", "pesquise o arquivo", "pesquisar arquivo",
            "no meu contrato", "no contrato", "na minha planilha", "na planilha",
            "o que diz o contrato", "o que diz a planilha", "qual o valor no contrato",
            "qual o valor na planilha", "dúvida sobre o pdf", "duvida sobre o pdf",
            "leia o contrato", "leia a planilha", "resumo do contrato", "resuma o contrato",
        ])
        if is_rag_query:
            # Extrai termo de busca removendo o prefixo se for comando de busca
            search_term = re.sub(
                r"^(buscar|busque|pesquisar|pesquise|procurar|procure|encontrar|encontre|onde está|onde esta)\s+(nos\s+meus\s+documentos|no\s+meu\s+documento|em\s+documentos|documentos|o\s+documento|documento|o\s+arquivo|arquivo)?\s*(sobre|de|com)?\s*",
                "",
                prompt_clean,
                flags=re.I,
            ).strip()
            if not search_term:
                search_term = prompt_clean

            if getattr(self, "rag", None):
                # Se for pergunta elaborada, utiliza ask() com síntese
                is_question = any(q in low for q in ["qual", "o que", "quanto", "como", "resumo", "resuma", "dúvida", "duvida"])
                if is_question:
                    rag_ans = self.rag.ask(prompt_clean, llm_provider=self.llm_provider)
                    if rag_ans["found"]:
                        actions = []
                        for cit in rag_ans["results"]:
                            actions.append(
                                DesktopAction(
                                    ActionType.OPEN_DOCUMENT,
                                    cit.file_path,
                                    {"page_number": cit.page_number},
                                    description=f"Abrir {cit.file_name} (Pág. {cit.page_number})",
                                )
                            )
                        return ActionPlan(
                            thought=rag_ans["answer"],
                            actions=actions,
                        )

                results = self.rag.search(search_term, limit=4)
                if results:
                    thought_lines = [f"Encontrei {len(results)} trecho(s) relevante(s) nos seus documentos para '{search_term}':\n"]
                    actions = []
                    for r in results:
                        thought_lines.append(r.format_citation())
                        actions.append(
                            DesktopAction(
                                ActionType.OPEN_DOCUMENT,
                                r.file_path,
                                {"page_number": r.page_number},
                                description=f"Abrir {r.file_name} (Pág. {r.page_number})",
                            )
                        )
                    return ActionPlan(
                        thought="\n\n".join(thought_lines),
                        actions=actions,
                    )
                else:
                    return ActionPlan(
                        thought=f"Nenhum documento contendo '{search_term}' foi encontrado em ~/Documentos ou ~/Downloads.",
                        actions=[],
                    )

        # Interação com Elementos da Janela Ativa (AT-SPI2)
        if low.startswith(("clicar em ", "clique em ", "aperte ", "pressione ")):
            target = re.sub(r"^(clicar em|clique em|aperte|pressione)\s+", "", prompt_clean, flags=re.I).strip("'\"")
            return ActionPlan(
                thought=f"Localizar e clicar no elemento '{target}' na tela",
                actions=[
                    DesktopAction(
                        ActionType.CLICK,
                        target,
                        description=f"Clicar no botão ou elemento '{target}'",
                    )
                ],
            )

        # Comandos diretos de abertura de apps (ex: "abrir steam", "abrir calculadora", etc.)
        # Somente se não for uma pergunta explicativa ("como acessar", "me explique", "o que é", etc.)
        is_question = any(q in low for q in ["me explique", "como", "o que", "onde", "por que", "qual", "quem"])
        if not is_question:
            app, app_name = AppManager.find_app(prompt_clean)
            if app:
                return ActionPlan(
                    thought=f"Localizado aplicativo correspondente: {app_name}",
                    actions=[
                        DesktopAction(
                            ActionType.LAUNCH_APP,
                            app_name,
                            {"app_id": app.get_id(), "executable": app.get_executable()},
                            description=f"Abrir o aplicativo '{app_name}'",
                        )
                    ],
                )

        # =========================================================================
        # 2. CAMADA DE INTELIGÊNCIA ARTIFICIAL (LLM: Gemini / Ollama / OpenAI)
        # =========================================================================
        if self.llm_provider.is_configured():
            try:
                # Obtém nomes de alguns apps instalados para dar contexto ao LLM
                app_names = [a.get_name() for a in AppManager.get_all_apps() if a.get_name()]
                context_parts = [self.memory.get_context_summary()]
                situational = self._get_situational_context()
                if situational:
                    context_parts.append(situational)

                doc_matches = []
                greeting_words = {
                    "oi", "ola", "olá", "alo", "alô", "hey", "hello", "hi", "opa", "eae",
                    "bom", "boa", "dia", "tarde", "noite", "obrigado", "obrigada", "valeu",
                    "tchau", "adeus"
                }
                doc_keywords = {
                    "documento", "documentos", "arquivo", "arquivos", "pasta", "pastas",
                    "pdf", "docx", "planilha", "relatorio", "relatório", "contrato",
                    "extrato", "tabela", "artigo", "anexo", "nota", "comprovante",
                    "leia", "ler", "leitura", "procure", "encontre", "buscar", "busque",
                    "ache", "onde"
                }
                words_list = [w.lower() for w in re.findall(r"\w+", prompt_clean)]
                is_pure_greeting = bool(words_list) and all(w in greeting_words for w in words_list)
                is_doc_intent = bool(set(words_list) & doc_keywords) or any(ext in prompt_clean.lower() for ext in [".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"])

                if getattr(self, "rag", None) and not is_pure_greeting:
                    try:
                        doc_matches = self.rag.search(prompt_clean, limit=3)
                        if doc_matches:
                            rag_text = "[Documentos Locais Relevantes]:\n" + "\n\n".join(
                                f"📄 {d.file_name} (Pág. {d.page_number}):\n\"{d.snippet}\""
                                for d in doc_matches
                            )
                            context_parts.append(rag_text)
                    except Exception as exc:
                        logger.debug(f"Erro ao consultar RAG no chat: {exc}")

                search_results = []
                if self.config.web_search_enabled and not is_pure_greeting and self.search_client.is_search_needed(prompt_clean):
                    clean_q = self.search_client.clean_search_query(prompt_clean)
                    search_results = self.search_client.search(clean_q, max_results=3)
                    if search_results:
                        context_parts.append(self.search_client.format_results_for_prompt(search_results))

                context_summary = "\n\n".join(p for p in context_parts if p)

                explanation, actions = self.llm_provider.chat(
                    prompt_clean,
                    app_list=app_names,
                    context_summary=context_summary,
                    history=history,
                )

                # Se foram encontrados documentos locais relevantes com intenção documental e a IA não gerou ação de abrir documento
                if doc_matches and not is_pure_greeting and is_doc_intent and not any(a.action_type == ActionType.OPEN_DOCUMENT for a in actions):
                    top_doc = doc_matches[0]
                    actions.append(
                        DesktopAction(
                            ActionType.OPEN_DOCUMENT,
                            top_doc.file_path,
                            {"page_number": top_doc.page_number},
                            description=f"Abrir {top_doc.file_name} (Pág. {top_doc.page_number})",
                        )
                    )

                # Se houve busca na web e a IA não gerou ação de link, oferece a fonte primária
                if search_results and not any(a.action_type == ActionType.OPEN_URL for a in actions):
                    primary = search_results[0]
                    actions.append(
                        DesktopAction(
                            ActionType.OPEN_URL,
                            primary.url,
                            description=f"Abrir fonte: {primary.title[:45]}...",
                        )
                    )

                # Higienização de resposta com abertura de URL: evita poluir o chat com trechos crus de busca web
                if any(a.action_type == ActionType.OPEN_URL for a in actions):
                    if re.search(r":\s*\[|\b\[\d+\]", explanation):
                        first_chunk = re.split(r":\s*\[|\b\[\d+\]|\n", explanation)[0].strip()
                        first_chunk = re.sub(r"[:\-–—\s]+$", "", first_chunk).strip()
                        if len(first_chunk) >= 8:
                            if not re.search(r"\b(abrir?|aberto|abriu|acessei|acessar|abrindo)\b", first_chunk, re.I):
                                explanation = f"{first_chunk}. Abri a página no seu navegador para você conferir as opções."
                            else:
                                explanation = f"{first_chunk}."

                # Se o usuário pediu para salvar/gerar relatório em arquivo e a IA não gerou a ação diretamente
                if any(w in low for w in [
                    "salve em um arquivo", "salve num arquivo", "crie um arquivo",
                    "salve o relatorio", "salve o relatório", "gere um relatorio",
                    "gere um relatório", "salvar relatorio", "salvar relatório",
                    "salve em arquivo", "salve num documento", "salve em documento",
                    "escreva um relatorio", "escreva um relatório", "salvar em arquivo"
                ]) and not any(a.action_type == ActionType.WRITE_FILE for a in actions):
                    slug = re.sub(r"[^a-z0-9]+", "_", low[:30]).strip("_") or "relatorio"
                    fname = f"{slug}.md"
                    actions.append(
                        DesktopAction(
                            ActionType.WRITE_FILE,
                            fname,
                            {
                                "filename": fname,
                                "content": explanation,
                                "directory": "~/Documentos/Relatorios",
                            },
                            description=f"Salvar relatório em '~/Documentos/Relatorios/{fname}'",
                        )
                    )

                if not actions:
                    actions = [
                        DesktopAction(
                            ActionType.ANSWER,
                            explanation,
                            description="Resposta do Zorin Copilot",
                        )
                    ]

                ocr_act = next((a for a in actions if a.action_type == ActionType.SMART_OCR), None)
                ext_text = ocr_act.target if ocr_act else None
                ext_kind = ocr_act.params.get("kind", "texto") if ocr_act else "text"
                return ActionPlan(
                    thought=explanation,
                    actions=actions,
                    raw_response=explanation,
                    extracted_text=ext_text,
                    extracted_kind=ext_kind,
                )
            except Exception as exc:
                logger.error(f"Erro na consulta ao provedor LLM: {exc}")
                return ActionPlan(
                    thought=f"Ocorreu um erro ao comunicar com a IA ({self.config.provider}):\n\n{exc}",
                    actions=[
                        DesktopAction(
                            ActionType.ANSWER,
                            str(exc),
                            description="Erro na consulta de IA",
                        )
                    ],
                )

        # =========================================================================
        # 3. CAMADA DE FALLBACK INTELIGENTE (Sem Chave de IA configurada)
        # =========================================================================

        # Atalhos comuns de serviços da web quando o usuário pergunta sobre eles
        web_shortcuts = [
            (r"\bgmail\b", "Gmail", "https://mail.google.com", "Para acessar o Gmail no Zorin OS, você pode abrir o navegador web e acessar o site mail.google.com, ou usar um cliente de e-mail como Thunderbird ou Geary."),
            (r"\byoutube\b", "YouTube", "https://youtube.com", "O YouTube pode ser acessado pelo navegador web."),
            (r"\bwhatsapp\b", "WhatsApp Web", "https://web.whatsapp.com", "Você pode acessar o WhatsApp através do WhatsApp Web no navegador."),
            (r"\bgithub\b", "GitHub", "https://github.com", "Você pode acessar o GitHub diretamente pelo navegador."),
        ]

        for pattern, name, url, expl in web_shortcuts:
            if re.search(pattern, low):
                return ActionPlan(
                    thought=f"{expl}\n\n💡 Dica: Conecte o Google Gemini nas Configurações (⚙️) para respostas conversacionais completas.",
                    actions=[
                        DesktopAction(
                            ActionType.OPEN_URL,
                            url,
                            description=f"Abrir o {name} no navegador ({url})",
                        )
                    ],
                )

        # Se mesmo com a checagem de apps anterior não bateu, tenta busca de app
        app, app_name = AppManager.find_app(prompt_clean)
        if app:
            return ActionPlan(
                thought=f"Localizado aplicativo correspondente: {app_name}",
                actions=[
                    DesktopAction(
                        ActionType.LAUNCH_APP,
                        app_name,
                        {"app_id": app.get_id(), "executable": app.get_executable()},
                        description=f"Abrir o aplicativo '{app_name}'",
                    )
                ],
            )

        # Resposta orientativa padrão
        return ActionPlan(
            thought=(
                f"Para responder perguntas livres como '{prompt_clean}', configure um provedor de Inteligência Artificial:\n\n"
                "• Google Gemini: Gratuito e rápido (basta inserir sua chave do Google AI Studio)\n"
                "• Ollama: 100% local e offline (ex: llama3.2)\n\n"
                "Clique no ícone de engrenagem ⚙️ no canto superior para configurar."
            ),
            actions=[
                DesktopAction(
                    ActionType.ANSWER,
                    "Configure o Gemini ou Ollama no ícone de configurações ⚙️ para habilitar o raciocínio de IA.",
                    description="Provedor de IA não configurado",
                )
            ],
        )
