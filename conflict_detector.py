import re
from typing import Optional, List, Tuple, Set

# Mapping of Latin lookalikes and common leet-speak numbers to Russian Cyrillic letters
CHAR_MAP = {
    'a': 'а', '@': 'а',
    'b': 'б', '6': 'б',
    'c': 'с', '$': 'с',
    'd': 'д',
    'e': 'е', '3': 'з',
    'f': 'ф',
    'g': 'г',
    'h': 'х',
    'i': 'и', '1': 'и', '!': 'и',
    'j': 'й',
    'k': 'к',
    'l': 'л',
    'm': 'м',
    'n': 'н',
    'o': 'о', '0': 'о',
    'p': 'п',
    'r': 'р',
    's': 'с',
    't': 'т',
    'u': 'у',
    'v': 'в',
    'w': 'в',
    'x': 'х',
    'y': 'у',
    'z': 'з',
    'ё': 'е',
}

# Safe words whitelist — these contain profanity substrings but are NOT profanity.
# Prevents false positives like "употреблять" triggering "бля" or "парикмахер" triggering "хер".
SAFE_WORDS: Set[str] = {
    # Words containing "бля" / "блят" / "бляд"
    "употреблять", "употребления", "употреблении", "употребление",
    "оскорблять", "оскорбление", "оскорблений", "оскорбления",
    "рублях", "рублям", "рубля",
    "потреблять", "потребление", "потребности", "потребность",
    "углублять", "углубление", "расслаблять", "расслабление",
    "влюблять", "влюбленность", "размышлять", "размышления",
    "позволять", "отправлять", "отправление", "поздравлять", "поздравления",
    "проявлять", "проявление", "направлять", "направление",
    "оформлять", "оформление", "вставлять", "представлять", "представление",
    "заставлять", "доставлять", "доставка", "составлять", "составление",
    "ослаблять", "ослабление", "закупках", "возобновлять", "укреплять",
    "заменять", "замещать", "зачислять", "зачисление",
    "ослаблять", "ослабление",
    # Words containing "сук"
    "рисунок", "рисунка", "рисунки", "рисунках", "посуда", "посуду", "посуде",
    "сукно", "сукцессия", "рассудок", "рассудка", "рассуждения", "рассуждать",
    # Words containing "хер" / "хрен"
    "парикмахер", "парикмахерская", "сверхестественный", "сверхвысокий",
    "характер", "характера", "характеристика",
    "хрен", "хрена", "хрену", "хреном", "хреновый", "хреново", "хреновая", "хреновые", "хрень",
    "фига", "фиг", "фигу", "фиге", "фигой", "фиговый", "фигово", "пофиг", "пофигу",
    # Words containing "очко"
    "очко", "очка", "очке", "очку", "очком", "очках", "очки", "очков",
    # Words containing "еб"
    "колебание", "колебания", "колебаний", "колебаться",
    "хлеб", "хлеба", "хлебушек", "жребий", "жребия", "серебро", "стебель", "гребень",
    "тебе", "себе", "мебель", "ребенок", "ребёнок", "ребята",
    "требование", "требования", "потребитель",
    # Words containing "дроч"
    "задрочить",  # keep only explicit forms, not "подросток" etc.
    # Words containing "тварь" — allow as animal term in some contexts
    "тварина",
}

# Regex pattern for Russian profanity and insult roots
RUSSIAN_PROFANITY_REGEX = re.compile(
    r'\b\w*'
    r'('
    r'хуй|хуйн|хуя|хуе|хуё|хуи|хул|хуу|хер|'
    r'пизд|пидар|пидор|'
    r'бля|блят|бляд|'
    r'еба|ебе|ебё|еби|ебан|ебат|ёба|ёбн|ебу|ебы|ебн|'
    r'мудак|мудил|'
    r'гандон|гондон|'
    r'долбоеб|долбоёб|'
    r'шлюх|сука|сучк|сучин|'
    r'залуп|ублюд|выродок|гнид|тварь|паскуд|дроч|'
    r'мразь|мрази'
    r')'
    r'\w*\b',
    re.IGNORECASE
)

# Regex pattern for Latin lookalikes and English translit profanity bypasses
LATIN_PROFANITY_REGEX = re.compile(
    r'\b\w*('
    r'xuy|xuj|huj|xui|xyi|xue|xuya|xuyn|huyn|xueta|hueta|xuesos|huesos|'
    r'pizd|pizdet|pizdec|pizdos|pizdu|pizdat|pizdab|'
    r'blyat|blyad|blia|blya|bliat|bliad|bitch|'
    r'ebat|ebal|ebanut|ebalnik|ebani|ebala|ebuc|yebat|yebal|yeban|yebut|'
    r'suka|cyka|'
    r'mudak|mudil|gandon|gondon'
    r')\w*\b',
    re.IGNORECASE
)


def normalize_text(text: str) -> str:
    """Normalizes text by lowercasing and mapping Latin lookalikes."""
    text_lower = text.lower()
    normalized_chars = []
    for ch in text_lower:
        normalized_chars.append(CHAR_MAP.get(ch, ch))
    return "".join(normalized_chars)


def collapse_repeated(text: str) -> str:
    """Collapses consecutive repeated characters to counter letter repetition bypasses (e.g. 'хххуууй' -> 'хуй')."""
    return re.sub(r'(.)\1+', r'\1', text)


def _is_safe_word(word: str) -> bool:
    """Check if a word is in the safe words whitelist."""
    return word.lower() in SAFE_WORDS


def find_violation(text: str, custom_bad_words: Optional[List[str]] = None) -> Tuple[bool, Optional[str]]:
    """
    Checks message text for profanity, targeted insults, or custom bad words.
    Filters out safe words (e.g. "употреблять") to prevent false positives.
    Returns (has_violation, matched_word_or_reason).
    """
    if not text:
        return False, None

    normalized = normalize_text(text)
    collapsed = collapse_repeated(normalized)

    # 0. Check Latin / Translit Profanity (e.g. xuy, pizda, blyat, etc.)
    latin_match = LATIN_PROFANITY_REGEX.search(text)
    if latin_match:
        return True, latin_match.group(0)

    # Extract individual words from normalized text
    words = re.findall(r'[а-яёa-z0-9]+', normalized, re.IGNORECASE)

    # 1. Check Russian Profanity Regex — word by word first (respects safe words)
    for word in words:
        if _is_safe_word(word):
            continue
        match = RUSSIAN_PROFANITY_REGEX.search(word)
        if match:
            return True, match.group(0)

    # 2. Check collapsed text word by word (for repeated character bypasses like "ххууйй")
    collapsed_words = re.findall(r'[а-яёa-z0-9]+', collapsed, re.IGNORECASE)
    for word in collapsed_words:
        if _is_safe_word(word):
            continue
        match = RUSSIAN_PROFANITY_REGEX.search(word)
        if match:
            return True, match.group(0)

    # 3. Check Custom Bad Words from DB
    if custom_bad_words:
        # Build text without safe words for custom checks
        unsafe_words = [w for w in words if not _is_safe_word(w)]
        unsafe_text = " ".join(unsafe_words)

        for bw in custom_bad_words:
            bw_clean = bw.strip().lower()
            if not bw_clean:
                continue

            if len(bw_clean) <= 3:
                pattern = r'\b' + re.escape(bw_clean) + r'\b'
            else:
                pattern = re.escape(bw_clean)

            if re.search(pattern, unsafe_text, re.IGNORECASE):
                return True, bw

            # Also check collapsed version
            unsafe_collapsed = " ".join(w for w in collapsed_words if not _is_safe_word(w))
            if re.search(pattern, unsafe_collapsed, re.IGNORECASE):
                return True, bw

    return False, None
