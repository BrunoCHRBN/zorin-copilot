"""math_solver — grafo de solução estruturado para o modo de estudos.

Em vez de devolver prosa, o solver produz um ``SolutionGraph``: um objeto
determinístico e validável que carrega (1) os métodos de resolução, (2) os
pontos de vista, (3) abordagens alternativas, (4) a verificação e (5) a
pontuação por passo. Cada renderizador (chat, voz, quadro negro) consome o
mesmo grafo — nada de texto solto.

Critério de qualidade ("rigor chinês"): a implementação é pura e testável, a
utilidade é a de expor o *raciocínio* e não só a resposta, e o recurso é
desenvolvido para fins de estudo (cada passo justificado, verificável e
pontuável).

O ``solve()`` resolvedor real cobre equações quadráticas e sistemas 2x2 via
álgebra pura (sem sympy/rede). Quando o problema não casa nessas classes, há
fallback determinístico (exemplo canônico) ou — se um provedor/engine for
passado — uma chamada de ferramenta com JSON-Schema estrito (ver
``SOLUTION_GRAPH_SCHEMA`` / ``from_provider_json``).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from ..core.decision_trace import DecisionTrace, record, reset_trace, set_trace

try:  # sympy é opcional; a verificação numérica funciona sem ele
    import sympy as _sp  # type: ignore
    _HAS_SYMPY = True
except Exception:  # pragma: no cover - depende do ambiente
    _HAS_SYMPY = False


# --------------------------------------------------------------------------- #
# Estrutura de dados                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    id: str
    title: str
    detail: str
    latex: str = ""
    justification: str = ""


@dataclass
class Method:
    id: str
    name: str
    summary: str
    steps: list[Step] = field(default_factory=list)
    pros: str = ""
    suitable_for: str = ""


@dataclass
class Viewpoint:
    id: str
    name: str
    description: str
    latex: str = ""


@dataclass
class Alternative:
    id: str
    name: str
    description: str


@dataclass
class Verification:
    checks: list[str] = field(default_factory=list)
    passed: bool = True


@dataclass
class Scoring:
    total: int = 0
    items: list[dict] = field(default_factory=list)  # {"step": str, "points": int}


@dataclass
class SolutionGraph:
    problem_statement: str
    givens: str = ""          # 已知
    unknown: str = ""         # 求
    answer: str = ""          # 答
    methods: list[Method] = field(default_factory=list)
    viewpoints: list[Viewpoint] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    verification: Verification = field(default_factory=Verification)
    scoring: Scoring = field(default_factory=Scoring)
    discipline: str = "Matemática"

    def to_dict(self) -> dict:
        return asdict(self)

    def branches_for_layout(self) -> list[dict]:
        """União de métodos + pontos de vista numa lista única de 'galhos' do grafo.

        Cada galho traz os passos no formato canônico que o Quadro Negro desenha,
        seja ele um método de resolução ou um ponto de vista (数形结合).
        """
        branches: list[dict] = []
        for m in self.methods:
            branches.append({
                "id": m.id, "kind": "method", "name": m.name,
                "summary": m.summary,
                "steps": [
                    {"id": s.id, "title": s.title, "detail": s.detail,
                     "latex": s.latex, "justification": s.justification}
                    for s in m.steps
                ],
            })
        for v in self.viewpoints:
            branches.append({
                "id": v.id, "kind": "viewpoint", "name": v.name,
                "summary": v.description,
                "steps": [
                    {"id": f"{v.id}_s1", "title": "Interpretação",
                     "detail": v.description, "latex": v.latex, "justification": ""}
                ],
            })
        return branches


# --------------------------------------------------------------------------- #
# Verificação                                                                 #
# --------------------------------------------------------------------------- #
def _evaluate_polynomial(coeffs: list[float], x: float) -> float:
    """Avalia a^n * x^n + ... + a0 em x (Horner)."""
    acc = 0.0
    for c in coeffs:
        acc = acc * x + c
    return acc


def verify_roots(coeffs: list[float], roots: list[float], tol: float = 1e-6) -> Verification:
    """Confere se cada raiz anula o polinômio (verificação 已知→答)."""
    checks: list[str] = []
    passed = True
    for r in roots:
        val = _evaluate_polynomial(coeffs, r)
        ok = abs(val) < tol
        passed = passed and ok
        checks.append(f"p({r}) = {val:.2e}  ->  {'OK' if ok else 'FALHOU'}")
    return Verification(checks=checks, passed=passed)


# --------------------------------------------------------------------------- #
# Verificação numérica independente dos checks do modelo (item #3)            #
# --------------------------------------------------------------------------- #
# Reconfere SÓ os checks da forma "A = B" onde A e B são numéricos. Checks em
# prosa (sem '=', ou não numéricos como "p(2) = ...") são ignorados, preservando
# o veredito que o modelo escreveu. Só flipa True->False quando acha uma
# contradição numérica de verdade — detecta quando o modelo "diz" que passou mas
# os números não fecham. O charset de entrada é limitado para bloquear qualquer
# execução de código (sem '_', aspas, ponto-e-vírgula):``sympify``/``eval`` só
# recebem aritmética.
_SAFE_EXPR = re.compile(r"^[0-9A-Za-z.\+\-\*/\^\(\) ]+$")


def _eval_expr(s: str) -> "Optional[float]":
    """Avalia uma expressão aritmética de forma sandbox. None se não parseável.

    Usa sympy quando disponível (mais robusto para potências/constantes); cai
    para ``eval`` restrito quando não. O charset de entrada é limitado, então
    nem sympy nem ``eval`` recebem nada além de aritmética (letras x/y, dígitos,
    operadores, parênteses, ponto).
    """
    s = (s or "").strip()
    if not s or not _SAFE_EXPR.match(s):
        return None
    expr = s.replace("^", "**")
    if _HAS_SYMPY:
        try:
            val = _sp.sympify(expr).evalf()
            if getattr(val, "is_number", False):
                return float(val)
        except Exception:
            pass
    try:
        val = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - charset restrito acima
        return float(val)
    except Exception:
        return None


def _verify_checks_numeric(checks: list[str], tol: float = 1e-6) -> bool:
    """True se nenhum check numericamente parseável contradiz o resultado.

    Cada check é dividido no primeiro '='; o RHS tem anotações ('-> OK',
    '(esperado ...)') removidas antes de avaliar. Equações não numéricas são
    puladas. Devolve False ao encontrar qualquer discordância dentro de ``tol``.
    """
    if not checks:
        return True
    ok = True
    for chk in checks:
        if "=" not in chk:
            continue
        left, right = chk.split("=", 1)
        lv = _eval_expr(left)
        rhs_clean = re.split(r"->|\(esperado", right, maxsplit=1)[0].strip()
        rv = _eval_expr(rhs_clean)
        if lv is None or rv is None:
            continue
        if abs(lv - rv) > tol:
            ok = False
    return ok


# --------------------------------------------------------------------------- #
# Utilidades de formatação                                                    #
# --------------------------------------------------------------------------- #
def _fmt_num(x: float) -> str:
    """Formata número: inteiro quando exato, senão 4 casas significativas."""
    if isinstance(x, complex):
        if abs(x.imag) < 1e-9:
            return _fmt_num(x.real)
        sign = "+" if x.imag >= 0 else "-"
        return f"{_fmt_num(x.real)}{sign}{_fmt_num(abs(x.imag))}i"
    if float(x).is_integer():
        return str(int(round(x)))
    return f"{x:.4g}"


def _fmt_expr(a: float, b: float, c: float) -> str:
    """Monta 'ax² + bx + c' com sinais corretos (usa ²)."""
    parts: list[str] = []
    if a != 0:
        coef = "" if abs(a) == 1 else _fmt_num(a)
        if a == -1:
            coef = "-"
        parts.append(f"{coef}x²")
    if b != 0:
        coef = "" if abs(b) == 1 else _fmt_num(b)
        if b == -1:
            coef = "-"
        sign = "" if not parts or b < 0 else "+"
        parts.append(f"{sign}{coef}x" if sign else f"{coef}x")
    if c != 0:
        sign = "" if not parts or c < 0 else "+"
        parts.append(f"{sign}{_fmt_num(c)}" if sign else f"{_fmt_num(c)}")
    return " ".join(parts) if parts else "0"


def _fmt_linear(a: float, b: float, c: float) -> str:
    """Monta 'ax + by = c' com sinais corretos (coef 1 omitido, '+ -' evitado)."""
    def _var(coef: float, letter: str) -> str:
        if coef == 1:
            return letter
        if coef == -1:
            return f"-{letter}"
        return f"{_fmt_num(coef)}{letter}"
    s = _var(a, "x")
    if b >= 0:
        s += f" + {_var(b, 'y')}"
    else:
        s += f" - {_var(-b, 'y')}"
    return f"{s} = {_fmt_num(c)}"


# --------------------------------------------------------------------------- #
# Parsing (álgebra pura, sem rede)                                            #
# --------------------------------------------------------------------------- #
def _split_terms(expr: str) -> list[str]:
    """Quebra '2x^2 - 5x + 6' em ['2x^2', '-5x', '+6']."""
    expr = expr.replace(" ", "").replace("²", "^2").replace("−", "-")
    parts = re.split(r"(?=[+-])", expr)
    return [p for p in parts if p not in ("", "+", "-")]


def _coef_of(term: str, var: str) -> float:
    rest = term.replace(var, "")
    if rest in ("", "+"):
        return 1.0
    if rest == "-":
        return -1.0
    return float(rest)


def parse_quadratic(text: str) -> "Optional[tuple[float, float, float]]":
    """Extrai (a, b, c) de 'ax²+bx+c=0'. Retorna None se não reconhecer."""
    t = text.replace("²", "^2").replace("−", "-")
    # Remove preâmbulo em palavras ("Resolva 2x²..."), MAS preserva o coeficiente
    # numérico que antecede o x (o corte em t.find("x") jogava o '2' fora).
    eq = re.sub(r"^[^\d+\-xy]*", "", t)
    if "=" not in eq:
        return None
    lhs, rhs = eq.split("=", 1)
    try:
        rhs_val = float(re.sub(r"[^0-9.\-+]", "", rhs) or "0")
    except ValueError:
        return None
    a = b = c = 0.0
    for term in _split_terms(lhs):
        if "x^2" in term:
            a += _coef_of(term, "x^2")
        elif "x" in term:
            b += _coef_of(term, "x")
        else:
            try:
                c += float(term)
            except ValueError:
                return None
    c -= rhs_val
    if a == 0:
        return None
    return (a, b, c)


def parse_system2(text: str) -> "Optional[tuple[float, float, float, float, float, float]]":
    """Extrai (a1,b1,c1,a2,b2,c2) de dois equações lineares em x,y. None se <2."""
    eqs = re.findall(r"[^;\n]+?=[^;\n]+", text.replace("−", "-"))
    found: list[tuple[float, float, float]] = []
    for eq in eqs:
        if "x" in eq and "y" in eq:
            lhs, rhs_s = eq.split("=", 1)
            a = b = 0.0
            c = 0.0
            try:
                rhs_val = float(re.sub(r"[^0-9.\-+]", "", rhs_s) or "0")
            except ValueError:
                continue
            for term in _split_terms(lhs):
                if "y" in term and "x" not in term:
                    b += _coef_of(term, "y")
                elif "x" in term and "y" not in term:
                    a += _coef_of(term, "x")
                else:
                    try:
                        c += float(term)
                    except ValueError:
                        return None
            # Convenção: (a, b, c) representa a*x + b*y = c (c é o RHS, não o
            # termo independente homogêneo). Cramer direto abaixo fica correto.
            c = rhs_val - c
            found.append((a, b, c))
    if len(found) == 2:
        return (*found[0], *found[1])
    return None


# --------------------------------------------------------------------------- #
# Resolvedores                                                               #
# --------------------------------------------------------------------------- #
def solve_quadratic(a: float, b: float, c: float) -> SolutionGraph:
    delta = b * b - 4 * a * c
    if delta >= -1e-9:
        sq = math.sqrt(max(delta, 0.0))
        r1 = (-b - sq) / (2 * a)
        r2 = (-b + sq) / (2 * a)
        roots = sorted([r1, r2], key=lambda v: v.real)
        real_roots = [float(r) for r in roots]
        complex_roots = False
    else:
        import cmath
        sq = cmath.sqrt(delta)
        r1 = (-b - sq) / (2 * a)
        r2 = (-b + sq) / (2 * a)
        roots = [r1, r2]
        real_roots = []
        complex_roots = True

    expr = _fmt_expr(a, b, c)
    answer = "S = {" + ", ".join(_fmt_num(r) for r in roots) + "}"

    methods: list[Method] = []
    methods.append(Method(
        id="m_bhas", name="Fórmula de Bhaskara",
        summary="Aplica a fórmula geral para ax²+bx+c=0.",
        pros="Universal: funciona mesmo sem raízes 'de adivinhação'.",
        suitable_for="Qualquer equação quadrática.",
        steps=[
            Step("b1", "Coeficientes", f"a = {_fmt_num(a)}, b = {_fmt_num(b)}, c = {_fmt_num(c)}.",
                 latex=f"a={_fmt_num(a)},\\; b={_fmt_num(b)},\\; c={_fmt_num(c)}",
                 justification="Leitura direta do trinômio."),
            Step("b2", "Discriminante", f"Δ = b² - 4ac = {_fmt_num(delta)}.",
                 latex=f"\\Delta = ({_fmt_num(b)})^2 - 4({_fmt_num(a)})({_fmt_num(c)}) = {_fmt_num(delta)}",
                 justification="O sinal de Δ classifica o número de raízes reais."),
            Step("b3", "Aplicar a fórmula", "x = (-b ± √Δ) / 2a.",
                 latex=r"x = \frac{-b \pm \sqrt{\Delta}}{2a}",
                 justification="Fórmula geral válida para todo a ≠ 0."),
            Step("b4", "Raízes", f"x₁ = {_fmt_num(roots[0])}, x₂ = {_fmt_num(roots[1])}.",
                 latex=f"x_1 = {_fmt_num(roots[0])},\\; x_2 = {_fmt_num(roots[1])}",
                 justification="Separar os dois sinais cobre os dois casos."),
        ],
    ))

    perfect = delta >= 0 and abs((sq := math.sqrt(max(delta, 0.0))) - round(sq)) < 1e-9
    if perfect and not complex_roots:
        def _binomial(r: float) -> str:
            # (x - r) com sinal correto: raiz negativa vira (x + |r|)
            return f"(x + {_fmt_num(-r)})" if r < 0 else f"(x - {_fmt_num(r)})"
        r1, r2 = float(roots[0]), float(roots[1])
        factor_a = f"{_fmt_num(a)}" if a != 1 else ""
        sum_r, prod_r = -b / a, c / a  # Girard geral (válido para qualquer a)
        methods.append(Method(
            id="m_fator", name="Fatoração",
            summary="Escreve o trinômio como produto de dois binômios.",
            pros="Mais rápido quando as raízes são inteiras/racionais.",
            suitable_for="Trinômios com raízes racionais.",
            steps=[
                Step("f1", "Soma e produto", f"r₁+r₂ = {_fmt_num(sum_r)}, r₁·r₂ = {_fmt_num(prod_r)}.",
                     latex=f"r_1+r_2 = {_fmt_num(sum_r)},\\; r_1\\cdot r_2 = {_fmt_num(prod_r)}",
                     justification="Relação de Girard geral: soma = -b/a, produto = c/a."),
                Step("f2", "Fatorar", f"{factor_a}{_binomial(r1)}{_binomial(r2)} = 0.",
                     latex=f"{factor_a}{_binomial(r1)}{_binomial(r2)} = 0",
                     justification="Cada fator anula a expressão em uma raiz."),
                Step("f3", "Anular fatores", f"x = {_fmt_num(r1)} ou x = {_fmt_num(r2)}.",
                     latex=f"x={_fmt_num(r1)}\\;\\text{{ ou }}\\;x={_fmt_num(r2)}",
                     justification="Se um produto é zero, algum fator é zero."),
            ],
        ))
    else:
        methods.append(Method(
            id="m_sq", name="Completar o quadrado",
            summary="Isola um quadrado perfeito para extrair a raiz.",
            pros="Ponte para a fórmula geral; útil quando não fatora.",
            suitable_for="Quadráticas irracionais ou com Δ não-quadrado-perfeito.",
            steps=[
                Step("s1", "Dividir por a", f"x² + ({_fmt_num(b/a)})x + ({_fmt_num(c/a)}) = 0.",
                     latex=f"x^2 + ({_fmt_num(b/a)})x + ({_fmt_num(c/a)}) = 0",
                     justification="Garante coeficiente líder 1."),
                Step("s2", "Mover constante", f"x² + ({_fmt_num(b/a)})x = {_fmt_num(-c/a)}.",
                     latex=f"x^2 + ({_fmt_num(b/a)})x = {_fmt_num(-c/a)}",
                     justification="Isola os termos em x."),
                Step("s3", "Completar", f"(x + {_fmt_num(b/(2*a))})² = {_fmt_num((b*b - 4*a*c)/(4*a*a))}.",
                     latex=f"\\left(x + {_fmt_num(b/(2*a))}\\right)^2 = {_fmt_num((b*b - 4*a*c)/(4*a*a))}",
                     justification="Adiciona (b/2a)² aos dois lados."),
                Step("s4", "Extrair raiz", f"x = {_fmt_num(roots[0])} ou {_fmt_num(roots[1])}.",
                     latex=f"x = {_fmt_num(roots[0])}\\;\\text{{ ou }}\\;{_fmt_num(roots[1])}",
                     justification="Raiz quadrada de ambos os lados."),
            ],
        ))

    viewpoints = [Viewpoint(
        id="v_geo", name="Geométrico (数形结合)",
        description=f"A parábola y = {expr} cruza o eixo x em x = {', '.join(_fmt_num(r) for r in roots)}.",
        latex=f"y = {expr.replace('²', '^2')}\\;\\text{{ intercepta }}x\\text{{ em }}x={', '.join(_fmt_num(r) for r in roots)}",
    )]

    alternatives = [
        Alternative(id="a_var", name="举一反三 (variação)",
                   description="Mude c e observe como as raízes se afastam ou se encontram (raiz dupla quando Δ=0)."),
    ]
    if not (perfect and not complex_roots):
        alternatives.insert(0, Alternative(id="a_fator", name="Fatoração",
                                           description="Quando Δ é quadrado perfeito, a fatoração é mais curta que Bhaskara."))

    if real_roots:
        verification = verify_roots([a, b, c], real_roots)
    else:
        verification = Verification(checks=["Raízes complexas — fora do escopo de verificação real."], passed=True)

    scoring = Scoring(total=5, items=[
        {"step": "Identificar coeficientes", "points": 1},
        {"step": "Método aplicado corretamente", "points": 2},
        {"step": "Cálculo do discriminante/raízes", "points": 1},
        {"step": "Verificação por substituição", "points": 1},
    ])

    return SolutionGraph(
        problem_statement=f"Resolva {expr} = 0.",
        givens=f"Equação do 2º grau: a={_fmt_num(a)}, b={_fmt_num(b)}, c={_fmt_num(c)}.",
        unknown="Valores de x que anulam a expressão.",
        answer=answer,
        methods=methods, viewpoints=viewpoints, alternatives=alternatives,
        verification=verification, scoring=scoring,
    )


def solve_system2(a1: float, b1: float, c1: float, a2: float, b2: float, c2: float) -> SolutionGraph:
    det = a1 * b2 - a2 * b1
    has_unique = abs(det) >= 1e-9
    x = y = 0.0
    if has_unique:
        x = (c1 * b2 - c2 * b1) / det
        y = (a1 * c2 - a2 * c1) / det
        answer = f"S = {{{_fmt_num(x)}, {_fmt_num(y)}}}"
        # numerador de x na substituição, com sinal de b1 tratado
        def _yterm(coef: float) -> str:
            if coef == 1:
                return "y"
            if coef == -1:
                return "-y"
            return f"{_fmt_num(coef)}y"
        sub_num = f"{_fmt_num(c1)}"
        if b1 >= 0:
            sub_num += f" - {_yterm(b1)}"
        else:
            sub_num += f" + {_yterm(-b1)}"
        if abs(a1 - 1.0) < 1e-9:
            sub_expr = f"x = {sub_num}"
            sub_latex = f"x = {sub_num}"
        else:
            sub_expr = f"x = ({sub_num}) / {_fmt_num(a1)}"
            sub_latex = f"x = \\frac{{{sub_num}}}{{{_fmt_num(a1)}}}"
        methods = [
            Method(id="m_sub", name="Substituição",
                   summary="Isola uma variável e substitui na outra equação.",
                   pros="Claro para sistemas simples.",
                   steps=[
                       Step("s1", "Isolar x na (1)", f"{sub_expr}.",
                            latex=sub_latex,
                            justification="Desloca os termos em y para o outro lado."),
                       Step("s2", "Substituir na (2)", f"{_fmt_linear(a2, b2, c2)}.",
                            latex=_fmt_linear(a2, b2, c2).replace("=", "\\;=\\;"),
                            justification="Elimina x, restando apenas y."),
                       Step("s3", "Resolução", f"x = {_fmt_num(x)}, y = {_fmt_num(y)}.",
                            latex=f"x = {_fmt_num(x)},\\; y = {_fmt_num(y)}",
                            justification="Resolve a equação de uma variável."),
                   ]),
            Method(id="m_elim", name="Adição (eliminação)",
                   summary="Multiplica para eliminar uma variável somando as equações.",
                   pros="Escala melhor para sistemas maiores.",
                   steps=[
                       Step("e1", "Preparar eliminação de y", f"mult. (1) por {_fmt_num(b2)} e (2) por {_fmt_num(b1)}.",
                            latex=f"({_fmt_linear(a1, b1, c1)})\\cdot({_fmt_num(b2)})",
                            justification="Iguala os coeficientes de y."),
                       Step("e2", "Somar", f"{_fmt_num(a1*b2 - a2*b1)}x = {_fmt_num(c1*b2 - c2*b1)}.",
                            latex=f"{_fmt_num(a1*b2 - a2*b1)}\\,x = {_fmt_num(c1*b2 - c2*b1)}",
                            justification="y se cancela; resta x."),
                       Step("e3", "Voltar com x", f"y = {_fmt_num(y)}.",
                            latex=f"y = {_fmt_num(y)}",
                            justification="Substitui x na equação escolhida."),
                   ]),
        ]
    else:
        answer = "Sistema dependente ou impossível (det = 0)."
        methods = []

    if has_unique:
        viewpoints = [Viewpoint(
            id="v_geo", name="Geométrico (数形结合)",
            description=f"Cada equação é uma reta; a solução é o ponto de interseção ({_fmt_num(x)}, {_fmt_num(y)}).",
            latex="ax + by = c \\;\\text{é reta; solução = interseção}",
        )]
    else:
        viewpoints = [Viewpoint(
            id="v_geo", name="Geométrico (数形结合)",
            description="Retas paralelas ou coincidentes (sem ponto único).", latex="")]

    verification = verify_roots_2x2([a1, b1, c1, a2, b2, c2], [x, y]) if has_unique else \
        Verification(checks=["Sem solução única real."], passed=True)

    scoring = Scoring(total=5, items=[
        {"step": "Montar o sistema", "points": 1},
        {"step": "Método aplicado", "points": 2},
        {"step": "Cálculo de x e y", "points": 1},
        {"step": "Verificação nas duas equações", "points": 1},
    ])

    return SolutionGraph(
        problem_statement=f"Resolva o sistema: {_fmt_linear(a1, b1, c1)}; {_fmt_linear(a2, b2, c2)}.",
        givens="Duas equações lineares em x e y.",
        unknown="Valor de (x, y) que satisfaz ambas.",
        answer=answer, methods=methods, viewpoints=viewpoints,
        alternatives=[Alternative(id="a_mat", name="Método de Cramer",
                                  description="Determinantes: x = Δx/Δ, y = Δy/Δ (útil se já souber matrizes).")],
        verification=verification, scoring=scoring,
    )


def verify_roots_2x2(coeffs: list[float], sol: list[float], tol: float = 1e-6) -> Verification:
    a1, b1, c1, a2, b2, c2 = coeffs
    x, y = sol
    checks = [
        f"eq1: {a1}·{x} + {b1}·{y} = {a1*x + b1*y:.2e} (esperado {c1})",
        f"eq2: {a2}·{x} + {b2}·{y} = {a2*x + b2*y:.2e} (esperado {c2})",
    ]
    passed = abs(a1 * x + b1 * y - c1) < tol and abs(a2 * x + b2 * y - c2) < tol
    return Verification(checks=checks, passed=passed)


# --------------------------------------------------------------------------- #
# Solver principal                                                            #
# --------------------------------------------------------------------------- #
def try_solve(problem: str, engine: Optional[object] = None, trace: "Optional[DecisionTrace]" = None) -> "Optional[SolutionGraph]":
    """Como ``solve()``, mas devolve ``None`` quando nada casa (SEM fallback).

    Use este nos caminhos de UI que precisam decidir *se* o problema é
    resolvível de forma estruturada — pendurar um Quadro Negro em toda mensagem
    de chat seria ruído. ``solve()`` mantém o fallback para uso direto/CLI.

    Ordem: quadrática/sistema 2x2 (álgebra pura, offline) → ``engine`` (LLM) se
    fornecido. ``engine`` pode ser um objeto com ``generate_with_tool(prompt,
    schema)`` ou um provedor com ``complete(prompt, system_prompt, json_mode)``.

    Se ``trace`` for passado, cada decisão de roteamento é registrada nele
    (ver ``core.decision_trace``) — habilitando o *rastro de decisão* (§6.2).
    """
    token = set_trace(trace) if trace is not None else None
    try:
        record("try_solve", "dispatch", problem=problem)
        q = parse_quadratic(problem)
        if q is not None:
            record("parse_quadratic", "match", a=q[0], b=q[1], c=q[2])
            g = solve_quadratic(*q)
            record("solve_quadratic", "result", answer=g.answer, passed=g.verification.passed)
            return g
        s = parse_system2(problem)
        if s is not None:
            record("parse_system2", "match", a1=s[0], b1=s[1], c1=s[2],
                   a2=s[3], b2=s[4], c2=s[5])
            g = solve_system2(*s)
            record("solve_system2", "result", answer=g.answer, passed=g.verification.passed)
            return g
        if engine is not None:
            record("route_engine", "escalate")
            return _solve_with_engine(problem, engine)
        record("no_match", "fallback")
        return None
    finally:
        if token is not None:
            reset_trace(token)


def _solve_with_engine(problem: str, engine: object) -> "Optional[SolutionGraph]":
    """Escalona para o modelo: aceita o contrato de ferramenta OU de provedor."""
    try:
        if hasattr(engine, "generate_with_tool"):
            record("request_solution_graph", "engine_call", contract="generate_with_tool")
            return request_solution_graph(problem, engine)
        record("solve_with_provider", "engine_call", contract="complete")
        return solve_with_provider(problem, engine)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Solver model-backed (LLM) — para o que a álgebra pura não cobre             #
# --------------------------------------------------------------------------- #
# Instrução de sistema: pede o MESMO contrato que o dataclass SolutionGraph
# espera, já com o tempero do "rigor chinês" (一题多解, 数形结合, 过程性评价).
_SOLVER_SYSTEM = (
    "Você é um professor de matemática que explica passo a passo. Responda "
    "APENAS com um objeto JSON válido (sem texto fora do JSON, sem cercas), "
    "seguindo exatamente este esquema:\n"
    '{"problem_statement": str, "givens": str, "unknown": str, "answer": str, '
    '"methods": [{"id": str, "name": str, "summary": str, "pros": str, '
    '"suitable_for": str, "steps": [{"id": str, "title": str, "detail": str, '
    '"latex": str, "justification": str}]}], '
    '"viewpoints": [{"id": str, "name": str, "description": str, "latex": str}], '
    '"alternatives": [{"id": str, "name": str, "description": str}], '
    '"verification": {"checks": [str], "passed": bool}, '
    '"scoring": {"total": int, "items": [{"step": str, "points": int}]}}\n'
    "Regras: (1) ao menos DOIS métodos de resolução (一题多解); (2) ao menos um "
    "ponto de vista geométrico/visual quando fizer sentido (数形结合); (3) cada "
    "passo com 'justification' (por quê); (4) inclua verificação por substituição; "
    "(5) 'latex' em notação LaTeX simples; (6) escreva em português do Brasil."
)


def looks_like_math(text: str) -> bool:
    """Heurística determinística: vale a pena oferecer o Quadro Negro?

    Barata e sem rede. Não decide a resolução — só se a UI deve mostrar o botão
    (a resolução model-backed só roda no clique, quando o usuário quer de fato).
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    eq_count = t.count("=")
    if eq_count >= 2 and ";" in t:
        return True
    if eq_count >= 1 and any(ch.isdigit() for ch in t) and ("x" in t or "y" in t):
        return True
    # Palavras claramente matemáticas. Evita termos genéricos como "sistema"
    # (casaria com "o sistema está lento") — o botão apareceria à toa.
    keywords = (
        "resolva", "equação", "equacao", "polinômio", "polinomio",
        "fatorar", "fatoração", "fatoracao", "raiz", "raízes", "raizes",
        "derivada", "integral", "limite", "matriz", "logaritmo", "trigonometr",
        "calcule", "simplifique", "inequação", "inequacao", "determinante",
    )
    return any(k in t for k in keywords)


def solve_with_provider(problem: str, provider: object, trace: "Optional[DecisionTrace]" = None) -> "Optional[SolutionGraph]":
    """Resolve via provedor LLM e devolve o ``SolutionGraph`` estruturado.

    Usa ``provider.complete(prompt, system_prompt=..., json_mode=True)`` e reusa
    o extrator de JSON do ``agent_router`` (tolera cercas ```json e texto ao
    redor). Devolve ``None`` quando o provedor não está configurado, não suporta
    ``complete``, ou a resposta não é um JSON com ``answer`` + ``methods``.

    Se ``trace`` for passado (ou já estiver ativo via ``try_solve``), registra o
    resultado e se a verificação independente (item #3) flipou o veredito.
    """
    token = set_trace(trace) if trace is not None else None
    try:
        if not hasattr(provider, "complete"):
            return None
        configured = getattr(provider, "is_configured", None)
        if callable(configured) and not configured():
            return None

        raw = provider.complete(
            f"Resolva e estruture o seguinte problema: {problem}",
            system_prompt=_SOLVER_SYSTEM,
            json_mode=True,
        )
        if not isinstance(raw, str) or not raw.strip():
            return None

        from .agent_router import extract_json_object  # import local: evita ciclo

        data = extract_json_object(raw)
        if not isinstance(data, dict) or not data.get("answer") or not data.get("methods"):
            return None
        g = solution_graph_from_dict(data)
        # Verificação independente (item #3): reconta os checks numéricos que o
        # modelo escreveu. Só flipa True->False quando acha contradição numérica;
        # checks em prosa não são tocados, então o veredito do modelo é preservado.
        flipped = False
        if g.verification.checks and g.verification.passed:
            if not _verify_checks_numeric(g.verification.checks):
                g.verification.passed = False
                flipped = True
                g.verification.checks = list(g.verification.checks) + [
                    "[Verificação independente] ao menos um check numérico contradiz o resultado."
                ]
        record("solve_with_provider", "result",
               answer=g.answer, passed=g.verification.passed,
               independent_verify_flipped=flipped)
        return g
    finally:
        if token is not None:
            reset_trace(token)


def solve(problem: str, engine: Optional[object] = None, trace: "Optional[DecisionTrace]" = None) -> SolutionGraph:
    """Despacha o problema para o resolvedor apropriado.

    Ordem: quadrática real → sistema 2x2 real → provedor LLM (se ``engine``
    dado) → fallback determinístico (exemplo canônico).

    Se ``trace`` for passado, o caminho determinístico e o de fallback são
    registrados nele (ver ``core.decision_trace``).
    """
    token = set_trace(trace) if trace is not None else None
    try:
        g = try_solve(problem, engine, trace=trace)
        if g is not None:
            return g
        record("fallback_sample", "result", answer="S = {2, 3}")
        return build_quadratic_sample()
    finally:
        if token is not None:
            reset_trace(token)


# --------------------------------------------------------------------------- #
# Contrato de provedor LLM (JSON-Schema estrito) — sem rede aqui             #
# --------------------------------------------------------------------------- #
SOLUTION_GRAPH_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "problem_statement": {"type": "string"},
        "givens": {"type": "string"},
        "unknown": {"type": "string"},
        "answer": {"type": "string"},
        "methods": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "pros": {"type": "string"},
                    "suitable_for": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "detail": {"type": "string"},
                                "latex": {"type": "string"},
                                "justification": {"type": "string"}
                            }
                        }
                    }
                }
            }
        },
        "viewpoints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "latex": {"type": "string"}
                }
            }
        },
        "alternatives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"}
                }
            }
        },
        "verification": {
            "type": "object",
            "properties": {
                "checks": {"type": "array", "items": {"type": "string"}},
                "passed": {"type": "boolean"}
            }
        },
        "scoring": {
            "type": "object",
            "properties": {
                "total": {"type": "integer"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string"},
                            "points": {"type": "integer"}
                        }
                    }
                }
            }
        }
    },
    "required": ["problem_statement", "answer", "methods"],
}


def request_solution_graph(problem: str, engine: object) -> SolutionGraph:
    """Chama o ``engine`` com a ferramenta SolutionGraph e reconstrói o dataclass.

    Espera que ``engine.generate_with_tool(prompt, schema)`` devolva um dict
    validando ``SOLUTION_GRAPH_SCHEMA``. Levanta se o engine não suportar o contrato.
    """
    if not hasattr(engine, "generate_with_tool"):
        raise NotImplementedError("engine sem generate_with_tool()")
    data = engine.generate_with_tool(
        f"Resolva passo a passo (múltiplos métodos, ponto de vista, verificação): {problem}",
        SOLUTION_GRAPH_SCHEMA,
    )
    return from_provider_json(data)


def _step_from_dict(d: dict) -> Step:
    return Step(
        id=d.get("id", ""), title=d.get("title", ""), detail=d.get("detail", ""),
        latex=d.get("latex", ""), justification=d.get("justification", ""),
    )


def solution_graph_from_dict(data: dict) -> SolutionGraph:
    """Reconstrói um SolutionGraph COMPLETO a partir de um dict.

    É o inverso de ``SolutionGraph.to_dict()`` e reconstrói ``Step``/``Method``/
    ``Viewpoint``/... como objetos de verdade — obrigatório para o Quadro Negro,
    que acessa atributos como ``s.id``. ``from_provider_json`` delega aqui.
    """
    methods = [
        Method(
            id=m.get("id", ""), name=m.get("name", ""), summary=m.get("summary", ""),
            steps=[_step_from_dict(s) for s in (m.get("steps") or [])],
            pros=m.get("pros", ""), suitable_for=m.get("suitable_for", ""),
        )
        for m in (data.get("methods") or [])
    ]
    viewpoints = [
        Viewpoint(id=v.get("id", ""), name=v.get("name", ""),
                  description=v.get("description", ""), latex=v.get("latex", ""))
        for v in (data.get("viewpoints") or [])
    ]
    alternatives = [
        Alternative(id=a.get("id", ""), name=a.get("name", ""),
                    description=a.get("description", ""))
        for a in (data.get("alternatives") or [])
    ]
    ver = data.get("verification") or {}
    verification = Verification(
        checks=list(ver.get("checks") or []), passed=bool(ver.get("passed", True))
    )
    sc = data.get("scoring") or {}
    scoring = Scoring(total=int(sc.get("total", 0) or 0), items=list(sc.get("items") or []))
    return SolutionGraph(
        problem_statement=data.get("problem_statement", ""),
        givens=data.get("givens", ""),
        unknown=data.get("unknown", ""),
        answer=data.get("answer", ""),
        methods=methods, viewpoints=viewpoints, alternatives=alternatives,
        verification=verification, scoring=scoring,
        discipline=data.get("discipline", "Matemática"),
    )


def from_provider_json(data: dict) -> SolutionGraph:
    """Reconstrói um SolutionGraph a partir do JSON validado pelo provedor."""
    return solution_graph_from_dict(data)


# --------------------------------------------------------------------------- #
# Saída de terminal (prova rápida no CLI)                                     #
# --------------------------------------------------------------------------- #
def render_terminal(g: SolutionGraph) -> str:
    """Saída de texto plano para o CLI/terminal (prova rápida, sem GUI).

    Expõe o raciocínio completo: 已知/求/答, métodos com passos justificados,
    pontos de vista (数形结合), abordagens alternativas (一题多解 / 举一反三),
    verificação e a pontuação por passo (过程性评价).
    """
    out = [f"Enunciado : {g.problem_statement}",
           f"已知      : {g.givens}",
           f"求        : {g.unknown}"]
    for m in g.methods:
        out.append("")
        out.append(f"[{m.name}] {m.summary}")
        if m.pros:
            out.append(f"    (vantagem: {m.pros})")
        for i, s in enumerate(m.steps, 1):
            out.append(f"  {i}. {s.title}: {s.detail}")
            if s.latex:
                out.append(f"     {s.latex}")
            if s.justification:
                out.append(f"     ↳ por quê: {s.justification}")
    if g.viewpoints:
        out.append("")
        out.append("Pontos de vista (数形结合):")
        for v in g.viewpoints:
            out.append(f"  - {v.name}: {v.description}")
    if g.alternatives:
        out.append("")
        out.append("Abordagens alternativas (一题多解 / 举一反三):")
        for a in g.alternatives:
            out.append(f"  - {a.name}: {a.description}")
    out.append("")
    out.append(f"Verificação: {'OK' if g.verification.passed else 'FALHOU'}")
    for c in g.verification.checks:
        out.append(f"  {c}")
    if g.scoring.items:
        out.append("")
        out.append(f"Pontuação por passo (过程性评价) — total {g.scoring.total}:")
        for it in g.scoring.items:
            out.append(f"  +{it['points']}  {it['step']}")
    out.append("")
    out.append(f"答        : {g.answer}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Exemplo canônico (fallback determinístico)                                  #
# --------------------------------------------------------------------------- #
def build_quadratic_sample() -> SolutionGraph:
    coeffs = [1.0, -5.0, 6.0]  # x^2 - 5x + 6
    roots = [2.0, 3.0]

    methods = [
        Method(
            id="m_fator",
            name="Fatoração",
            summary="Escreve o trinômio como produto de dois binômios.",
            pros="Mais rápido quando as raízes são inteiras.",
            suitable_for="Trinômios com raízes racionais/inteiras.",
            steps=[
                Step("m_fator_s1", "Identificar a e c",
                     "a = 1, c = 6. Precisamos de dois números cujo produto é 6 e cuja soma é 5.",
                     latex="a=1,\\; c=6,\\; p\\cdot q = 6,\\; p+q = 5",
                     justification="Propriedade do produto de binômios (x+p)(x+q)."),
                Step("m_fator_s2", "Encontrar os fatores",
                     "2 e 3 satisfazem: 2·3 = 6 e 2+3 = 5.",
                     latex="(x-2)(x-3) = x^2 - 5x + 6",
                     justification="A soma dos termos constantes iguala o coeficiente de x."),
                Step("m_fator_s3", "Igualar a zero",
                     "Cada fator pode anular a expressão.",
                     latex="x-2=0 \\Rightarrow x=2 \\quad | \\quad x-3=0 \\Rightarrow x=3",
                     justification="Se um produto é zero, algum fator é zero."),
            ],
        ),
        Method(
            id="m_bhas",
            name="Fórmula de Bhaskara",
            summary="Aplica a fórmula geral para ax²+bx+c=0.",
            pros="Universal: funciona mesmo sem raízes 'de adivinhação'.",
            suitable_for="Qualquer equação quadrática.",
            steps=[
                Step("m_bhas_s1", "Coeficientes",
                     "a = 1, b = -5, c = 6.",
                     latex="a=1,\\; b=-5,\\; c=6",
                     justification="Leitura direta do trinômio."),
                Step("m_bhas_s2", "Discriminante",
                     "Calcula Δ = b² - 4ac.",
                     latex="\\Delta = (-5)^2 - 4(1)(6) = 25 - 24 = 1",
                     justification="O sinal de Δ classifica o número de raízes reais."),
                Step("m_bhas_s3", "Aplicar a fórmula",
                     "x = (-b ± √Δ) / 2a.",
                     latex="x = \\frac{5 \\pm \\sqrt{1}}{2} = \\frac{5 \\pm 1}{2}",
                     justification="Fórmula geral válida para todo a ≠ 0."),
                Step("m_bhas_s4", "Raízes",
                     "Separa os dois sinais.",
                     latex="x_1 = 3,\\; x_2 = 2",
                     justification="Subtrair e somar √Δ cobre os dois casos."),
            ],
        ),
    ]

    viewpoints = [
        Viewpoint(
            id="v_geo",
            name="Geométrico (数形结合)",
            description="As raízes são os pontos onde a parábola corta o eixo x.",
            latex="y = x^2 - 5x + 6 \\quad \\text{intercepta } x \\text{ em } x=2,3",
        )
    ]

    alternatives = [
        Alternative(
            id="a_completar",
            name="Completar o quadrado",
            description="Reescreve como (x - 5/2)² = 1/4 e tira a raiz — ponte para a fórmula.",
        ),
        Alternative(
            id="a_variar",
            name="举一反三 (variação)",
            description="Mude 6 → 6,25 e as raízes se tornam iguais (x = 2,5): estuda o caso de raiz dupla.",
        ),
    ]

    verification = verify_roots(coeffs, roots)

    scoring = Scoring(
        total=5,
        items=[
            {"step": "Identificar coeficientes", "points": 1},
            {"step": "Método aplicado corretamente", "points": 2},
            {"step": "Cálculo do discriminante/raízes", "points": 1},
            {"step": "Verificação por substituição", "points": 1},
        ],
    )

    return SolutionGraph(
        problem_statement="Resolva x² - 5x + 6 = 0.",
        givens="Equação do 2º grau: a=1, b=-5, c=6.",
        unknown="Valores de x que anulam a expressão.",
        answer="S = {2, 3}",
        methods=methods,
        viewpoints=viewpoints,
        alternatives=alternatives,
        verification=verification,
        scoring=scoring,
        discipline="Matemática",
    )


if __name__ == "__main__":
    import sys
    prob = sys.argv[1] if len(sys.argv) > 1 else "x^2 - 5x + 6 = 0"
    g = solve(prob)
    print(render_terminal(g))
