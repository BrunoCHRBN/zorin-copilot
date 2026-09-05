"""Testes unitários para a gestão de contatos e memória semântica do MemoryManager."""

import os
import tempfile
import unittest

from zorin_copilot.core.memory import MemoryManager


class ContactsMemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_memory.db")
        self.memory = MemoryManager(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_retrieve_contact(self):
        """Salva contato e recupera por e-mail."""
        c = self.memory.save_contact(
            name="Carlos Silva",
            email="carlos.silva@empresa.com",
            aliases=["carlos", "contador", "financeiro"],
            phone="11999998888",
            notes="Contador responsável pelas notas fiscais",
        )
        self.assertEqual(c["name"], "Carlos Silva")
        self.assertEqual(c["email"], "carlos.silva@empresa.com")
        self.assertIn("contador", c["aliases"])

        retrieved = self.memory.get_contact_by_email("carlos.silva@empresa.com")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "Carlos Silva")
        self.assertIn("financeiro", retrieved["aliases"])

    def test_save_contact_invalid_email_raises_value_error(self):
        """Validação estrita: e-mail inválido deve levantar ValueError."""
        with self.assertRaises(ValueError):
            self.memory.save_contact("Lucas", "email_invalido_sem_arroba")

    def test_save_contact_upsert(self):
        """Salvar contato com mesmo e-mail deve atualizar os dados."""
        self.memory.save_contact("Mariana", "mariana@rh.com", aliases=["rh"])
        self.memory.save_contact("Mariana Costa", "mariana@rh.com", aliases=["rh", "diretora"], notes="Nova diretora")

        retrieved = self.memory.get_contact_by_email("mariana@rh.com")
        self.assertEqual(retrieved["name"], "Mariana Costa")
        self.assertIn("diretora", retrieved["aliases"])
        self.assertEqual(retrieved["notes"], "Nova diretora")

    def test_find_contact_by_name_and_alias(self):
        """Busca flexível por nome ou apelido."""
        self.memory.save_contact("Carlos Silva", "carlos@empresa.com", aliases=["contador"])
        self.memory.save_contact("Mariana Costa", "mariana@rh.com", aliases=["rh"])

        # Busca por apelido "contador"
        res_alias = self.memory.find_contact("contador")
        self.assertEqual(len(res_alias), 1)
        self.assertEqual(res_alias[0]["name"], "Carlos Silva")

        # Busca por nome parcial "Mariana"
        res_name = self.memory.find_contact("mariana")
        self.assertEqual(len(res_name), 1)
        self.assertEqual(res_name[0]["email"], "mariana@rh.com")

        # Busca por algo que não existe
        res_none = self.memory.find_contact("inexistente")
        self.assertEqual(len(res_none), 0)

    def test_delete_contact(self):
        """Remove contato por ID e por e-mail."""
        c = self.memory.save_contact("João", "joao@teste.com")
        self.assertTrue(self.memory.delete_contact_by_email("joao@teste.com"))
        self.assertIsNone(self.memory.get_contact_by_email("joao@teste.com"))

    def test_context_summary_includes_contacts_and_anti_hallucination(self):
        """Garante que o resumo para a IA inclui contatos salvos e trava anti-alucinação."""
        self.memory.save_contact("Carlos Silva", "carlos@empresa.com", aliases=["contador"])
        summary = self.memory.get_context_summary()

        self.assertIn("Carlos Silva <carlos@empresa.com>", summary)
        self.assertIn("NUNCA invente", summary)
        self.assertIn("apelidos: contador", summary)


if __name__ == "__main__":
    unittest.main()
