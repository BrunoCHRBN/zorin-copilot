"""Testes unitários para a CLI dedicada hy4."""

import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from zorin_copilot.hy4 import DEFAULT_MODEL, DEFAULT_URL, load_config, stream_chat


class Hy4CLITest(unittest.TestCase):
    @patch.dict("os.environ", {"HY4_API_KEY": "ck_env_test", "HY4_MODEL": "hy4-custom"})
    def test_load_config_from_env(self):
        cfg = load_config()
        self.assertEqual(cfg["api_key"], "ck_env_test")
        self.assertEqual(cfg["model"], "hy4-custom")

    @patch("zorin_copilot.hy4.urllib.request.urlopen")
    def test_stream_chat_yields_reasoning_and_content(self, mock_urlopen):
        chunk1 = json.dumps({"choices": [{"delta": {"reasoning_content": "analisando..."}}]})
        chunk2 = json.dumps({"choices": [{"delta": {"content": "Resultado final."}}]})
        lines = [
            f"data: {chunk1}\n".encode("utf-8"),
            f"data: {chunk2}\n".encode("utf-8"),
            b"data: [DONE]\n",
        ]

        mock_context = MagicMock()
        mock_context.__enter__.return_value = lines
        mock_urlopen.return_value = mock_context

        results = list(
            stream_chat(
                api_url=DEFAULT_URL,
                api_key="ck_test",
                model=DEFAULT_MODEL,
                messages=[{"role": "user", "content": "olá"}],
            )
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], ("reasoning", "analisando..."))
        self.assertEqual(results[1], ("content", "Resultado final."))


if __name__ == "__main__":
    unittest.main()
