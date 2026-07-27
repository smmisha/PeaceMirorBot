import unittest
from conflict_detector import normalize_text, collapse_repeated, find_violation


class TestConflictDetector(unittest.TestCase):

    def test_normalize_text(self):
        self.assertEqual(normalize_text("xyp"), "хур")
        self.assertEqual(normalize_text("xуй"), "хуй")

    def test_collapse_repeated(self):
        self.assertEqual(collapse_repeated("хххуууййй"), "хуй")
        self.assertEqual(collapse_repeated("приветтт"), "привет")

    def test_profanity_detection(self):
        test_cases = [
            ("Привет всем, как дела?", False),
            ("Какой сегодня хороший день!", False),
            ("Эй ты мудак пошел прочь", True),
            ("Перестань сука", True),
            ("Ты просто долбоеб", True),
            ("хххуууййй", True),  # Multi-char repetition bypass
            ("Ты пиздюк", True),
            ("cuka", True),        # Latin lookalike bypass
            ("Ты uблюдoк", True),   # Mixed latin-cyrillic bypass
        ]
        for text, expected in test_cases:
            has_violation, matched = find_violation(text)
            self.assertEqual(has_violation, expected, f"Failed for text: '{text}'. Got: {has_violation}, matched: {matched}")

    def test_custom_bad_words(self):
        custom = ["плохоеслово", "клоун"]
        has_violation, matched = find_violation("Ты настоящий клоун!", custom_bad_words=custom)
        self.assertTrue(has_violation)
        self.assertEqual(matched, "клоун")

        has_violation, _ = find_violation("Нормальный текст без нарушений", custom_bad_words=custom)
        self.assertFalse(has_violation)


if __name__ == "__main__":
    unittest.main()
