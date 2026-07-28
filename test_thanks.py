import unittest
from handlers.messages import THANKS_STRONG_REGEX, THANKS_WEAK_REGEX, MAX_WEAK_THANKS_WORDS


def is_thanks(text: str) -> bool:
    stripped = text.strip()
    is_short = len(stripped.split()) <= MAX_WEAK_THANKS_WORDS
    is_question = stripped.endswith("?")
    return (
        bool(THANKS_STRONG_REGEX.search(text))
        or stripped == "+"
        or (bool(THANKS_WEAK_REGEX.search(text)) and is_short and not is_question)
    )


class TestThanks(unittest.TestCase):
    def test_real_gratitude_counts(self):
        for t in ["Спасибо большое!", "спс", "респект тебе", "Молодец!",
                  "принято", "+1", "плюсую", "+"]:
            self.assertTrue(is_thanks(t), f"должно засчитаться: {t}")

    def test_ordinary_speech_does_not_count(self):
        for t in ["Ну ты же принял и лечился так?",
                  "принял таблетки утром и вечером",
                  "я не понял вопрос совсем",
                  "он молодец конечно но решение принял странное",
                  "принял?"]:
            self.assertFalse(is_thanks(t), f"НЕ должно засчитаться: {t}")


if __name__ == "__main__":
    unittest.main()
