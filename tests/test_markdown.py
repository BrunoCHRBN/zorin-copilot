"""Testes do renderer de Markdown -> markup do Pango (item 2.6 do plano de UI).

O teste mais importante aqui é `test_output_is_valid_pango_markup`: markup inválido faz
o Gtk.Label descartar a resposta inteira, então validar contra o próprio parser do
Pango é a garantia que interessa.
"""

import html
import os
import re
import sys
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from zorin_copilot.ui.markdown import format_markdown_to_markup  # noqa: E402


class MarkdownRendererTest(unittest.TestCase):
    # -- básicos ----------------------------------------------------------

    def test_empty_input(self):
        self.assertEqual(format_markdown_to_markup(""), "")
        self.assertEqual(format_markdown_to_markup(None), "")

    def test_plain_text_passes_through(self):
        self.assertEqual(format_markdown_to_markup("olá mundo"), "olá mundo")

    def test_bold(self):
        self.assertIn("<b>negrito</b>", format_markdown_to_markup("texto **negrito** aqui"))

    def test_bold_with_underscore(self):
        self.assertIn("<b>negrito</b>", format_markdown_to_markup("texto __negrito__ aqui"))

    def test_italic(self):
        self.assertIn("<i>itálico</i>", format_markdown_to_markup("texto *itálico* aqui"))

    def test_strikethrough(self):
        self.assertIn("<s>riscado</s>", format_markdown_to_markup("texto ~~riscado~~ aqui"))

    def test_inline_code(self):
        self.assertIn("<tt><b>ip a</b></tt>", format_markdown_to_markup("use `ip a` agora"))

    def test_snake_case_is_not_italicized(self):
        """Um sublinhado no meio de palavra não pode virar itálico."""
        out = format_markdown_to_markup("chame meu_arquivo.py e _realce_")
        self.assertIn("meu_arquivo.py", out)
        self.assertIn("<i>realce</i>", out)

    # -- blocos -----------------------------------------------------------

    def test_heading_levels(self):
        self.assertIn('<span size="x-large"><b>Título</b></span>', format_markdown_to_markup("# Título"))
        self.assertIn('<span size="large"><b>Sub</b></span>', format_markdown_to_markup("## Sub"))
        self.assertIn("<b>Subsub</b>", format_markdown_to_markup("### Subsub"))

    def test_hash_without_space_is_not_heading(self):
        self.assertEqual(format_markdown_to_markup("#sem espaço"), "#sem espaço")

    def test_unordered_list(self):
        out = format_markdown_to_markup("- um\n- dois")
        self.assertEqual(out, "• um\n• dois")

    def test_nested_list_is_indented(self):
        out = format_markdown_to_markup("- um\n  - filho")
        self.assertIn("  • filho", out)

    def test_ordered_list_keeps_numbers(self):
        out = format_markdown_to_markup("1. primeiro\n2. segundo")
        self.assertIn("1. primeiro", out)
        self.assertIn("2. segundo", out)

    def test_fenced_code_is_not_formatted(self):
        """Conteúdo de bloco de código não deve sofrer formatação inline."""
        out = format_markdown_to_markup("```\n**não boldar**\n```")
        self.assertIn("<tt>**não boldar**</tt>", out)
        self.assertNotIn("<b>", out)

    def test_unclosed_fence_does_not_break(self):
        out = format_markdown_to_markup("```python\nsem fechar")
        self.assertIn("sem fechar", out)

    def test_horizontal_rule(self):
        self.assertTrue(format_markdown_to_markup("---").strip())

    def test_quote_marks_every_line(self):
        """A barra de citação precisa estar em todas as linhas, não só na primeira."""
        out = format_markdown_to_markup("> primeira\n> segunda")
        self.assertEqual(out, "┃ primeira\n┃ segunda")

    def test_quote_renders_inline_markup(self):
        self.assertIn("<b>negrito</b>", format_markdown_to_markup("> **negrito**"))

    def test_table_aligns_columns(self):
        out = format_markdown_to_markup(
            "| Esquerda | Direita |\n|:---------|--------:|\n| a | b |"
        )
        # Remove o wrapper <tt> antes de comparar posições de coluna.
        inner = out.replace("<tt>", "").replace("</tt>", "")
        header, row = inner.split("\n")[0], inner.split("\n")[2]
        self.assertEqual(header.index("│"), row.index("│"))
        # Coluna esquerda: texto + espaços. Direita: espaços + texto.
        self.assertRegex(row, r"^a\s+│")
        self.assertTrue(row.endswith("b"))

    # -- links ------------------------------------------------------------

    def test_link_with_ampersand_is_escaped_once(self):
        """Duplo escape geraria '&amp;amp;' e quebraria a URL."""
        out = format_markdown_to_markup("[site](https://x.com?a=1&b=2)")
        self.assertIn('href="https://x.com?a=1&amp;b=2"', out)
        self.assertNotIn("&amp;amp;", out)

    def test_autolink_bare_url(self):
        out = format_markdown_to_markup("acesse https://example.com hoje")
        self.assertIn('<a href="https://example.com">https://example.com</a>', out)

    def test_link_url_is_not_autolinked_again(self):
        """O autolink não pode envolver o href que acabou de ser gerado."""
        out = format_markdown_to_markup("[a](https://example.com)")
        self.assertEqual(out.count("<a href"), 1)

    def test_image_becomes_label(self):
        self.assertIn("[imagem: foto]", format_markdown_to_markup("![foto](https://x/i.png)"))

    # -- segurança e robustez ---------------------------------------------

    def test_html_is_escaped(self):
        out = format_markdown_to_markup("<script>alert(1)</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_pango_injection_is_escaped(self):
        """Saída de modelo não pode injetar markup do Pango."""
        out = format_markdown_to_markup('<span size="999">grande</span>')
        self.assertNotIn('<span size="999">', out)
        self.assertIn("&lt;span", out)

    def test_control_characters_are_dropped(self):
        out = format_markdown_to_markup("texto\x00com\x07nulos")
        self.assertNotIn("\x00", out)
        self.assertNotIn("\x07", out)

    def test_never_raises_on_weird_input(self):
        weird = [
            "**", "__", "~~", "`", "```", "|", "||", "|-|", "> ", "- ", "1.",
            "**a", "*a", "[a](", "[](x)", "![]()", "######", "\n\n\n", "   ",
            "a" * 5000, "- " * 500, "|" * 200, "`" * 50,
        ]
        for text in weird:
            with self.subTest(text=text[:20]):
                self.assertIsInstance(format_markdown_to_markup(text), str)

    # -- validação de verdade: o Gtk.Label precisa aceitar -----------------

    def test_output_is_accepted_by_gtk_label(self):
        """Markup inválido faz o Gtk.Label manter o texto anterior, não o novo.

        Este é o teste que importa: comprova que o widget realmente renderiza o
        markup e que nada do conteúdo foi descartado no caminho.
        """
        label = Gtk.Label()
        corpus = [
            "# Título\n\nParágrafo com **negrito** e *itálico*.",
            "- item um\n- item dois\n  - aninhado\n\n1. primeiro\n2. segundo",
            "```bash\nls -la | grep x\necho \"olá & adeus\"\n```",
            "> citação com **negrito**\n> segunda linha\n\ntexto depois",
            "| Coluna A | Coluna B |\n|---|---:|\n| valor | 42 |\n| outro | 7 |",
            "Veja [docs](https://exemplo.com/a?b=1&c=2) e https://solto.com",
            "Use `código` aqui, ~~riscado~~ e _itálico_.",
            "---\n\ntexto\n\n---",
            "![alt](https://x/img.png) misturado com **texto**",
            "texto com <tags> & entidades \"aspas\" e 'ápice'",
            "snake_case e __bold__ e 100% < 200 & > 50",
            "```\n```",
            "> - lista dentro de citação\n> - outro item",
        ]
        for text in corpus:
            with self.subTest(text=text[:40]):
                markup = format_markdown_to_markup(text)
                expected = html.unescape(re.sub(r"<[^>]+>", "", markup))

                label.set_text("<sentinela>")  # valor que só muda se o markup for válido
                label.set_markup(markup)

                self.assertNotEqual(
                    label.get_text(),
                    "<sentinela>",
                    f"Gtk.Label rejeitou o markup de {text!r}: {markup!r}",
                )
                self.assertEqual(label.get_text(), expected)


class MarkdownBackwardCompatibilityTest(unittest.TestCase):
    """O renderer antigo era só regex; estes casos precisam continuar funcionando."""

    def test_bold_still_b(self):
        self.assertIn("<b>x</b>", format_markdown_to_markup("**x**"))

    def test_inline_code_still_tt_b(self):
        self.assertIn("<tt><b>código</b></tt>", format_markdown_to_markup("`código`"))

    def test_link_still_anchor(self):
        self.assertIn('<a href="https://x.com">', format_markdown_to_markup("[t](https://x.com)"))

    def test_simple_answer_has_no_block_wrapper(self):
        self.assertEqual(format_markdown_to_markup("tudo certo"), "tudo certo")


if __name__ == "__main__":
    unittest.main()
