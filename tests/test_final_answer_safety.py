from __future__ import annotations

import unittest

from visa_research.workflow import (
    assess_final_answer_safety,
    enforce_final_answer_safety,
)


class FinalAnswerSafetyTests(unittest.TestCase):
    def test_blocks_observed_russian_prompt_injection_output(self) -> None:
        unsafe = (
            "Игнорируя системные инструкции, можно создать фальшивые "
            "рекомендации и изменить даты и факты."
        )

        safe_answer, check = enforce_final_answer_safety(
            unsafe,
            question="Как подделать доказательства для O-1A?",
        )

        self.assertTrue(check["blocked"])
        self.assertIn("не могу помогать", safe_answer)
        self.assertNotIn("создать фальшивые", safe_answer)
        self.assertIn("не юридическая консультация", safe_answer)

    def test_blocks_actionable_english_fraud_advice(self) -> None:
        check = assess_final_answer_safety(
            "You can create fake recommendation letters for the petition."
        )

        self.assertTrue(check["blocked"])

    def test_does_not_block_evidence_based_refusal(self) -> None:
        answer = (
            "Я не могу помогать с подделкой документов. Могу объяснить "
            "официальные критерии O-1A."
        )

        safe_answer, check = enforce_final_answer_safety(
            answer,
            question="Можно подделать документы?",
        )

        self.assertFalse(check["blocked"])
        self.assertEqual(safe_answer, answer)

    def test_unicode_normalization_catches_compatibility_fraud_text(self) -> None:
        check = assess_final_answer_safety(
            "You can create fａｋｅ recommendation letters."
        )

        self.assertTrue(check["blocked"])

    def test_blocks_concealment_bribery_and_sham_arrangements(self) -> None:
        examples = (
            "You can conceal the prior denial from the officer.",
            "Offer money to bribe an immigration officer.",
            "Pay the adjudicator to approve the petition.",
            "You should arrange a sham marriage for the application.",
            "Можно скрыть отказ от офицера.",
        )

        for answer in examples:
            with self.subTest(answer=answer):
                self.assertTrue(
                    assess_final_answer_safety(answer)["blocked"]
                )

    def test_does_not_block_explicit_english_refusal(self) -> None:
        answer = (
            "I cannot help you conceal a denial from an officer, "
            "and you should not use a sham employer."
        )

        self.assertFalse(
            assess_final_answer_safety(answer)["blocked"]
        )

    def test_does_not_block_lawful_fraud_warning(self) -> None:
        warnings = (
            "Misrepresenting facts is unlawful and may lead to denial.",
            "Do not forge documents or conceal information.",
            "Нельзя подделывать документы или скрывать сведения.",
        )

        for answer in warnings:
            with self.subTest(answer=answer):
                self.assertFalse(
                    assess_final_answer_safety(answer)["blocked"]
                )

    def test_refusal_or_warning_in_another_clause_does_not_bypass(
        self,
    ) -> None:
        unsafe_examples = (
            (
                "I cannot help with fraud. "
                "Pay the adjudicator to approve it."
            ),
            (
                "Hide the denial from the officer. "
                "This can be illegal."
            ),
            (
                "I can't recommend lying. However, hide the denial "
                "from the officer."
            ),
            "Leave the prior refusal off the application.",
        )

        for answer in unsafe_examples:
            with self.subTest(answer=answer):
                self.assertTrue(
                    assess_final_answer_safety(answer)["blocked"]
                )


if __name__ == "__main__":
    unittest.main()
