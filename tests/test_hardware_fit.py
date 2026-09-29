"""Testes do módulo de adequação ao hardware — §3.1 do plano.

O seletor de modelos mostrava `8.0B · Q4_K_M · 4.6 GB` e parava. São números
corretos e inúteis: quem vai baixar um modelo quer saber se ele **roda**, e o
tamanho em disco não responde isso — a pergunta é se cabe na VRAM.

Nada aqui toca hardware de verdade. Toda a sondagem é injetável justamente para
que o teste valide a **decisão** (o `level`) e não a máquina de quem roda a
suíte; as partes que leem o sistema são exercitadas com entradas fixas.

A distinção central que os testes travam: omitir um parâmetro **sonda** a
máquina, passar `None` **afirma que a informação não existe**. Sem isso o
caminho "não deu para medir" ficaria impossível de testar — e é exatamente o
caminho em que a frase precisa ser mais cuidadosa.
"""

import unittest
from unittest.mock import patch

from zorin_copilot.core import hardware_fit as hf
from zorin_copilot.core.hardware_fit import (
    MAX_RAM_SHARE,
    TIGHT_HEADROOM,
    VRAM_OVERHEAD_FACTOR,
    GpuInfo,
    describe_fit,
    detect_gpu,
    estimate_required_bytes,
    format_gb,
)

GB = 1024 ** 3


class FormatGbTest(unittest.TestCase):
    """Vírgula decimal, como o resto do app."""

    def test_uses_comma(self):
        self.assertEqual(format_gb(8 * GB), "8,0")

    def test_one_decimal_place(self):
        self.assertEqual(format_gb(int(8.573 * GB)), "8,6")

    def test_zero(self):
        self.assertEqual(format_gb(0), "0,0")


class EstimateRequiredTest(unittest.TestCase):
    """O requisito é o arquivo mais a cache de contexto, não o arquivo."""

    def test_applies_the_overhead_factor(self):
        self.assertEqual(estimate_required_bytes(GB), int(GB * VRAM_OVERHEAD_FACTOR))

    def test_overhead_is_above_the_file_size(self):
        """A cache de contexto não é gratuita: o requisito é sempre maior."""
        self.assertGreater(estimate_required_bytes(GB), GB)

    def test_invalid_sizes_are_zero_not_an_exception(self):
        """0 significa "não sei", e é o que o chamador trata como sem veredito."""
        for value in (0, -1, None):
            with self.subTest(value=value):
                self.assertEqual(estimate_required_bytes(value), 0)


class DescribeFitTest(unittest.TestCase):
    """A ordem de decisão: GPU, depois RAM, depois "não cabe"."""

    def test_fits_comfortably_in_vram(self):
        gpu = GpuInfo(name="Radeon RX 7600", vram_bytes=8 * GB)
        # Atenção à unidade: `describe_fit` recebe o tamanho do **arquivo** e
        # multiplica por VRAM_OVERHEAD_FACTOR para chegar ao requisito. 0,6 da
        # VRAM deixa o requisito em 0,72 dela — dentro da folga.
        verdict = describe_fit(int(0.6 * 8 * GB), gpu=gpu, ram_bytes=16 * GB)
        self.assertEqual(verdict.level, "gpu")
        self.assertIn("Cabe", verdict.line)
        self.assertIn("8,0 GB", verdict.line)
        self.assertIn("Radeon RX 7600", verdict.line)

    def test_reports_tight_when_headroom_is_small(self):
        """Cabe, mas sem folga: o modelo faz offload e a velocidade cai."""
        gpu = GpuInfo(name="Radeon RX 7600", vram_bytes=8 * GB)
        # Requisito logo abaixo da VRAM, acima do limite de folga.
        verdict = describe_fit(int(0.78 * 8 * GB), gpu=gpu, ram_bytes=16 * GB)
        self.assertEqual(verdict.level, "tight")
        self.assertIn("justo", verdict.line)

    def test_tight_boundary_is_governed_by_the_headroom_constant(self):
        """O limite entre "cabe" e "cabe justo" é a constante, não um número solto.

        Se TIGHT_HEADROOM for ajustada com dados reais, este teste continua
        valendo — ele é escrito em função dela.
        """
        vram = 8 * GB
        gpu = GpuInfo(name="Radeon RX 7600", vram_bytes=vram)
        threshold = vram * (1.0 - TIGHT_HEADROOM)  # já em "requisito"

        # Truncar na conversão para tamanho de arquivo garante ficar abaixo.
        under = int((threshold - 1) / VRAM_OVERHEAD_FACTOR)
        over = int((threshold + 1) / VRAM_OVERHEAD_FACTOR) + 1

        self.assertEqual(describe_fit(under, gpu=gpu, ram_bytes=16 * GB).level, "gpu")
        self.assertEqual(describe_fit(over, gpu=gpu, ram_bytes=16 * GB).level, "tight")

    def test_falls_back_to_cpu_when_vram_is_too_small(self):
        gpu = GpuInfo(name="Radeon RX 7600", vram_bytes=8 * GB)
        verdict = describe_fit(12 * GB, gpu=gpu, ram_bytes=32 * GB)
        self.assertEqual(verdict.level, "cpu")
        self.assertIn("Não cabe", verdict.line)

    def test_insufficient_when_it_fits_nowhere(self):
        """Passar da RAM sem swap trava o sistema — o texto precisa avisar."""
        gpu = GpuInfo(name="Radeon RX 7600", vram_bytes=8 * GB)
        verdict = describe_fit(64 * GB, gpu=gpu, ram_bytes=16 * GB)
        self.assertEqual(verdict.level, "insufficient")
        self.assertIn("travar", verdict.line)

    def test_ram_share_boundary(self):
        """A fração de RAM é um limite, não uma sugestão."""
        at_the_limit = int(16 * GB * MAX_RAM_SHARE / VRAM_OVERHEAD_FACTOR)
        over_the_limit = int(17 * GB * MAX_RAM_SHARE / VRAM_OVERHEAD_FACTOR)

        self.assertEqual(
            describe_fit(at_the_limit, gpu=None, ram_bytes=16 * GB).level, "cpu"
        )
        self.assertEqual(
            describe_fit(over_the_limit, gpu=None, ram_bytes=16 * GB).level,
            "insufficient",
        )

    def test_no_gpu_but_ram_is_enough(self):
        verdict = describe_fit(4 * GB, gpu=None, ram_bytes=16 * GB)
        self.assertEqual(verdict.level, "cpu")
        self.assertIn("Sem GPU dedicada", verdict.line)
        self.assertIn("16,0 GB", verdict.line)

    def test_gpu_without_measurable_vram_falls_back_to_ram(self):
        """GPU presente, VRAM ilegível (integrada, driver exótico): só fala de RAM."""
        gpu = GpuInfo(name="Iris Xe Graphics", vram_bytes=None)
        verdict = describe_fit(4 * GB, gpu=gpu, ram_bytes=16 * GB)
        self.assertEqual(verdict.level, "cpu")

    def test_unknown_when_nothing_could_be_measured(self):
        """Sem dado nenhum, o veredito não afirma que cabe nem que não cabe."""
        verdict = describe_fit(4 * GB, gpu=None, ram_bytes=None)
        self.assertEqual(verdict.level, "unknown")
        self.assertIn("Não foi possível medir", verdict.line)

    def test_unknown_when_the_size_is_unknown(self):
        verdict = describe_fit(0, gpu=GpuInfo(vram_bytes=8 * GB), ram_bytes=16 * GB)
        self.assertEqual(verdict.level, "unknown")
        self.assertEqual(verdict.required_bytes, 0)

    def test_omitted_arguments_probe_the_machine(self):
        """Omitir não é o mesmo que afirmar ausência: aqui a máquina é sondada."""
        gpu = GpuInfo(name="Falsa", vram_bytes=64 * GB)
        with patch.object(hf, "detect_gpu", return_value=gpu) as probe, patch.object(
            hf, "detect_ram_bytes", return_value=16 * GB
        ):
            verdict = describe_fit(4 * GB)
        probe.assert_called_once()
        self.assertEqual(verdict.level, "gpu")

    def test_explicit_none_does_not_probe(self):
        """O outro lado da distinção: `None` afirma que a informação não existe."""
        with patch.object(hf, "detect_gpu") as probe, patch.object(
            hf, "detect_ram_bytes"
        ) as ram_probe:
            verdict = describe_fit(4 * GB, gpu=None, ram_bytes=None)
        probe.assert_not_called()
        ram_probe.assert_not_called()
        self.assertEqual(verdict.level, "unknown")

    def test_required_bytes_is_reported_for_the_caller(self):
        """O número do requisito mora no veredito, para não ser repetido em prosa."""
        verdict = describe_fit(4 * GB, gpu=None, ram_bytes=16 * GB)
        self.assertEqual(verdict.required_bytes, estimate_required_bytes(4 * GB))

    def test_line_is_short_enough_for_a_subtitle(self):
        """`line` é a segunda linha de um subtítulo, não um parágrafo."""
        gpu = GpuInfo(name="Radeon RX 7600", vram_bytes=8 * GB)
        for size, label in ((4 * GB, "gpu"), (12 * GB, "cpu"), (64 * GB, "insufficient")):
            with self.subTest(level=label):
                verdict = describe_fit(size, gpu=gpu, ram_bytes=16 * GB)
                self.assertLessEqual(len(verdict.line), 110)

    def test_never_raises_on_absurd_input(self):
        for size in (0, -1, 10 ** 15):
            with self.subTest(size=size):
                self.assertIsInstance(describe_fit(size, gpu=None, ram_bytes=8 * GB), hf.FitVerdict)


class GpuInfoTest(unittest.TestCase):
    def test_label_is_empty_without_a_name(self):
        self.assertEqual(GpuInfo().label(), "")

    def test_label_trims(self):
        self.assertEqual(GpuInfo(name="  Radeon  ").label(), "Radeon")


class ShortGpuNameTest(unittest.TestCase):
    """`lspci` devolve a descrição inteira; o subtítulo precisa de um nome."""

    def test_extracts_the_marketing_name(self):
        raw = (
            "Advanced Micro Devices, Inc. [AMD/ATI] Navi 33 "
            "[Radeon RX 7600/7600 XT/7600M XT/7600S/7700S / PRO W7600] (rev cf)"
        )
        self.assertEqual(hf._short_gpu_name(raw), "Radeon RX 7600")

    def test_drops_the_revision_code(self):
        raw = "NVIDIA Corporation [GeForce RTX 3070] (rev a1)"
        self.assertEqual(hf._short_gpu_name(raw), "GeForce RTX 3070")

    def test_intel_integrated(self):
        raw = "Intel Corporation Raptor Lake-P [Iris Xe Graphics] (rev 04)"
        self.assertEqual(hf._short_gpu_name(raw), "Iris Xe Graphics")

    def test_falls_back_to_stripping_the_vendor(self):
        """Sem nome comercial, ao menos o fornecedor sai da frente."""
        raw = "Advanced Micro Devices, Inc. [AMD/ATI] Device 164e"
        self.assertEqual(hf._short_gpu_name(raw), "Device 164e")

    def test_empty_input(self):
        self.assertEqual(hf._short_gpu_name(""), "")


class DetectGpuTest(unittest.TestCase):
    """A sondagem nunca levanta e nunca sobe processo por acidente."""

    def setUp(self):
        hf.reset_cache()
        self.addCleanup(hf.reset_cache)

    def test_does_not_spawn_subprocesses_when_told_not_to(self):
        """Regra do projeto: a suíte não sobe processos."""
        with patch.object(hf, "_read_nvidia_vram") as nvidia, patch.object(
            hf, "_read_lspci_gpu_name"
        ) as lspci, patch.object(hf, "_read_sysfs_vram", return_value=8 * GB):
            info = detect_gpu(allow_subprocess=False)
        nvidia.assert_not_called()
        lspci.assert_not_called()
        self.assertEqual(info.vram_bytes, 8 * GB)

    def test_subprocesses_are_off_in_test_mode_by_default(self):
        with patch.dict("os.environ", {"ZORIN_TEST_MODE": "1"}), patch.object(
            hf, "_read_nvidia_vram"
        ) as nvidia, patch.object(hf, "_read_sysfs_vram", return_value=None):
            detect_gpu()
        nvidia.assert_not_called()

    def test_nvidia_wins_over_sysfs(self):
        with patch.object(hf, "_read_nvidia_vram", return_value=(12 * GB, "RTX 4070")), patch.object(
            hf, "_read_sysfs_vram", return_value=8 * GB
        ), patch.object(hf, "_read_lspci_gpu_name", return_value=""):
            info = detect_gpu(allow_subprocess=True)
        self.assertEqual(info.vram_bytes, 12 * GB)
        self.assertEqual(info.name, "RTX 4070")

    def test_lspci_only_supplies_the_name(self):
        with patch.object(hf, "_read_nvidia_vram", return_value=(None, "")), patch.object(
            hf, "_read_sysfs_vram", return_value=8 * GB
        ), patch.object(hf, "_read_lspci_gpu_name", return_value="Radeon RX 7600"):
            info = detect_gpu(allow_subprocess=True)
        self.assertEqual(info.vram_bytes, 8 * GB)
        self.assertEqual(info.name, "Radeon RX 7600")

    def test_returns_empty_info_when_nothing_is_found(self):
        with patch.object(hf, "_read_nvidia_vram", return_value=(None, "")), patch.object(
            hf, "_read_sysfs_vram", return_value=None
        ), patch.object(hf, "_read_lspci_gpu_name", return_value=""):
            info = detect_gpu(allow_subprocess=True)
        self.assertIsNone(info.vram_bytes)
        self.assertEqual(info.name, "")

    def test_result_is_cached(self):
        """A sondagem é chamada na abertura do seletor; não faz sentido repetir."""
        with patch.object(hf, "_read_sysfs_vram", return_value=8 * GB) as sysfs:
            detect_gpu(allow_subprocess=False)
            detect_gpu(allow_subprocess=False)
        self.assertEqual(sysfs.call_count, 1)

    def test_reset_cache_forces_a_new_probe(self):
        with patch.object(hf, "_read_sysfs_vram", return_value=8 * GB) as sysfs:
            detect_gpu(allow_subprocess=False)
            hf.reset_cache()
            detect_gpu(allow_subprocess=False)
        self.assertEqual(sysfs.call_count, 2)

    def test_never_raises_when_probing_fails(self):
        """A promessa do módulo: uma falha de hardware não derruba a lista.

        `detect_gpu` roda no caminho de abertura do seletor de modelos — se uma
        exceção escapasse, a lista de modelos não apareceria por causa de um
        detalhe de hardware.
        """
        with patch.object(hf, "_read_nvidia_vram", side_effect=OSError("boom")), patch.object(
            hf, "_read_sysfs_vram", return_value=None
        ), patch.object(hf, "_read_lspci_gpu_name", return_value=""):
            info = detect_gpu(allow_subprocess=True)
        self.assertIsInstance(info, GpuInfo)
        self.assertIsNone(info.vram_bytes)


class DetectRamTest(unittest.TestCase):
    def test_reads_a_positive_total(self):
        """`/proc/meminfo` reporta kB; a conversão para bytes é do módulo."""
        value = hf.detect_ram_bytes()
        if value is None:
            self.skipTest("/proc/meminfo indisponível nesta máquina")
        self.assertGreater(value, 0)

    def test_returns_none_when_unreadable(self):
        """Máquina sem `/proc` não é motivo para exceção — é motivo para "não sei"."""
        with patch("builtins.open", side_effect=OSError("sem /proc")):
            self.assertIsNone(hf.detect_ram_bytes())


if __name__ == "__main__":
    unittest.main()
