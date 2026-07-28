import unittest
from conflict_detector import normalize_text, collapse_repeated, find_violation, is_hard_profanity


class TestConflictDetector(unittest.TestCase):

    def test_normalize_text(self):
        self.assertEqual(normalize_text("xuy"), "хуу")
        self.assertEqual(normalize_text("xui"), "хуи")

    def test_collapse_repeated(self):
        self.assertEqual(collapse_repeated("хххуууййй"), "хуй")
        self.assertEqual(collapse_repeated("приветтт"), "привет")

    def test_is_hard_profanity(self):
        self.assertTrue(is_hard_profanity("пиздец"))
        self.assertTrue(is_hard_profanity("пздц"))
        self.assertTrue(is_hard_profanity("п3дц"))
        self.assertTrue(is_hard_profanity("хуй"))
        self.assertTrue(is_hard_profanity("xuy"))
        self.assertTrue(is_hard_profanity("ебать"))
        self.assertFalse(is_hard_profanity("тварь"))
        self.assertFalse(is_hard_profanity("рисунок"))

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

    def test_no_false_positives_on_normal_words(self):
        """Регрессия: корни мата не должны ловиться в середине обычных слов."""
        clean_words = [
            # "ебн"/"ебу"/"еба"/"еби" внутри слова
            "волшебно", "волшебный", "волшебная", "волшебник", "волшебница",
            "хлеба", "купи хлеба", "хлебушек", "чебуреки", "чебурашка",
            "требую", "требуют", "требуется", "потребность", "потребности",
            "колебания", "колебаться", "жребий", "тебе", "себе", "беби",
            # "хер" внутри слова и в топонимах
            "парикмахер", "парикмахерская", "Херсон", "Херсонская область",
            "херувим", "Хербалайф", "Хертфорд", "херес",
            # "хул"/"ху" в обычных словах
            "хулиган", "хулиганить", "худой", "хутор", "хурма", "похудеть",
            "похуже", "Хуан Карлос", "река Хуанхэ",
            # приставка + обычный корень
            "победа", "поеду", "убежал", "наем", "подебатим", "Гондурас",
            # чисто латинские слова не должны прогоняться через CHAR_MAP
            "eBay", "я на ebay купил", "xerox", "hue", "debate",
            # прочее
            "сукно", "сучковатый", "рисунок", "посуда", "утварь", "гребля", "2+2=4",
        ]
        for text in clean_words:
            has_violation, matched = find_violation(text)
            self.assertFalse(has_violation, f"Ложное срабатывание на '{text}' (матч: {matched})")

    def test_prefixed_profanity_still_detected(self):
        """Мат с приставками и обходами должен ловиться."""
        dirty = [
            "заебал", "наебал", "уебан", "выебываться", "съебался", "разъебал",
            "доебался", "приебался", "ебло", "ебнутый", "ёбаный", "долбоёб",
            "похуй", "нахуя", "охуеть", "нихуя", "херня", "нахер",
            "х.у.й", "х у й", "хххуууййй", "cuka", "cyka", "pizdec", "nahuy", "zaebal",
        ]
        for text in dirty:
            has_violation, _ = find_violation(text)
            self.assertTrue(has_violation, f"Пропущен мат: '{text}'")

    def test_safe_words_are_not_silently_disabled(self):
        """Белый список должен реально работать — раньше 30 из 186 записей были мертвы."""
        from conflict_detector import SAFE_WORDS, _is_safe_word
        dead = [w for w in SAFE_WORDS if not _is_safe_word(w)]
        self.assertEqual(dead, [], f"Записи белого списка не работают: {dead[:10]}")

    def test_custom_allowed_words(self):
        # Non-profanity words CAN be whitelisted
        allowed = ["тварь"]
        has_violation, _ = find_violation("Какая интересная тварь", custom_allowed_words=allowed)
        self.assertFalse(has_violation)

        # Hard profanity CANNOT be whitelisted even if present in allowed list
        allowed_fake = ["пиздец", "пздц"]
        has_violation, matched = find_violation("Полный пиздец", custom_allowed_words=allowed_fake)
        self.assertTrue(has_violation)
        self.assertEqual(matched, "пиздец")


if __name__ == "__main__":
    unittest.main()

