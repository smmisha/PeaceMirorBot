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
    "нихрена", "дохрена", "нихрень", "ахренеть", "охренеть", "похрен", "нахрен",
    "фига", "фиг", "фигу", "фиге", "фигой", "фиговый", "фигово", "пофиг", "пофигу",
    # Words containing "очко"
    "очко", "очка", "очке", "очку", "очком", "очках", "очки", "очков",
    # Words containing "еб"
    "колебание", "колебания", "колебаний", "колебаться",
    "хлеб", "хлеба", "хлебушек", "жребий", "жребия", "серебро", "стебель", "гребень", "гребля", "гребли", "греблей",
    "тебе", "себе", "тебя", "себя", "мебель", "ребенок", "ребёнок", "ребята",
    "требование", "требования", "потребитель", "требуется", "требуют", "требую",
    "небуду", "небудет", "небудем", "небудь", "чебурашка", "чебурек", "чебуреки",
    "волшебно", "волшебный", "волшебная", "волшебное", "волшебные", "волшебник", "волшебница", "волшебство",
    # Words containing "ругаться"
    "ругаться", "ругаются", "ругаюсь", "поругалась", "поругался", "ругань",
    # Words containing "ерунда"
    "ерунда", "ерундой", "ерунду", "ерунде", "еруннда", "ерундистика",
    # Euphemisms / Colloquial words
    "капец", "пипец", "ппц", "капецц", "пипецц",
    "скотина", "скот", "скотины", "скотинаа",
    # Words containing "дроч"
    "задрочить",  # keep only explicit forms, not "подросток" etc.
    # Words containing "тварь" — allow as animal term in some contexts
    "тварина",
    # Names and benign words (юля, буй, буек)
    "юля", "юли", "юле", "юлю", "юлей",
    "буй", "буёк", "буек", "буйный", "буйство", "буйства", "буйствами",
}

# Regex pattern for Russian profanity and insult roots
RUSSIAN_PROFANITY_REGEX = re.compile(
    r'\b\w*'
    r'('
    r'хуй|хуйн|хуя|хуе|хуё|хуи|хул|хуу|хер|'
    r'пизд|пидар|пидор|'
    r'блядь|бляд|\bбля\b|\bблят\b|'
    r'еба|ебет|ебёт|еби|ебан|ебат|ёба|ёбн|ебу|ебы|ебн|отъеб|разъеб|заеб|проеб|наеб|доеб|съеб|уеб|поеб|подъеб|выеб|'
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


# Strict Regex pattern for core Russian profanity (hard mat) - CANNOT BE WHITELISTED
HARD_PROFANITY_REGEX = re.compile(
    r'('
    r'хуй|хуйн|хуя|хуе|хуё|хуи|хул|хуу|хер|'
    r'пизд|пзд|пидар|пидор|'
    r'блядь|бляд|\bбля\b|\bблят\b|'
    r'еба|ебет|ебёт|еби|ебан|ебат|ёба|ёбн|ебу|ебы|ебн|отъеб|разъеб|заеб|проеб|наеб|доеб|съеб|уеб|поеб|подъеб|выеб|'
    r'мудак|мудил|'
    r'гандон|гондон|'
    r'долбоеб|долбоёб|'
    r'залуп'
    r')',
    re.IGNORECASE
)

LATIN_HARD_PROFANITY_REGEX = re.compile(
    r'('
    r'xuy|xuj|huj|xui|xyi|xue|xuya|xuyn|huyn|xueta|hueta|xuesos|huesos|'
    r'pizd|pzd|pizdet|pizdec|pizdos|pizdu|pizdat|pizdab|'
    r'blyat|blyad|blia|blya|bliat|bliad|bitch|'
    r'ebat|ebal|ebanut|ebalnik|ebani|ebala|ebuc|yebat|yebal|yeban|yebut|'
    r'mudak|mudil|gandon|gondon'
    r')',
    re.IGNORECASE
)


import unicodedata


def clean_invisible(text: str) -> str:
    """Removes invisible zero-width spaces, soft hyphens, and combining diacritics used for bypassing filters."""
    text_nfkd = unicodedata.normalize('NFKD', text)
    cleaned = []
    for ch in text_nfkd:
        cat = unicodedata.category(ch)
        if cat in ('Mn', 'Cf') or ord(ch) in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x00AD):
            continue
        cleaned.append(ch)
    return "".join(cleaned)


def normalize_text(text: str) -> str:
    """Normalizes text by lowercasing, stripping invisible chars, and mapping Latin lookalikes."""
    text_clean = clean_invisible(text)
    text_lower = text_clean.lower()
    normalized_chars = []
    for ch in text_lower:
        normalized_chars.append(CHAR_MAP.get(ch, ch))
    return "".join(normalized_chars)


def collapse_repeated(text: str) -> str:
    """Collapses consecutive repeated characters to counter letter repetition bypasses (e.g. 'хххуууй' -> 'хуй')."""
    return re.sub(r'(.)\1+', r'\1', text)


def is_hard_profanity(word: str) -> bool:
    """
    Checks if a word is hard profanity (мат / leet profanity like пиздец, пздц, п3дц, хуй, etc.).
    Hard profanity CANNOT be whitelisted or added to safe words under any circumstances!
    """
    if not word:
        return False
    text_clean = clean_invisible(word)
    if LATIN_HARD_PROFANITY_REGEX.search(text_clean):
        return True
    normalized = normalize_text(text_clean)
    collapsed = collapse_repeated(normalized)
    punc_stripped = re.sub(r'[\.\,_\-\*\~\+\=\/\\]+', '', normalized)
    punc_collapsed = collapse_repeated(punc_stripped)

    for test_s in (normalized, collapsed, punc_stripped, punc_collapsed):
        if HARD_PROFANITY_REGEX.search(test_s) or LATIN_HARD_PROFANITY_REGEX.search(test_s):
            return True
    return False


def _is_safe_word(word: str, custom_allowed_words: Optional[List[str]] = None) -> bool:
    """
    Check if a word is in the safe words whitelist or custom allowed words.
    Hard profanity NEVER returns True here.
    """
    w_lower = word.lower()
    if is_hard_profanity(w_lower):
        return False
    if w_lower in SAFE_WORDS:
        return True
    if custom_allowed_words:
        allowed_set = {w.strip().lower() for w in custom_allowed_words}
        if w_lower in allowed_set:
            return True
    return False


def find_violation(
    text: str,
    custom_bad_words: Optional[List[str]] = None,
    custom_allowed_words: Optional[List[str]] = None
) -> Tuple[bool, Optional[str]]:
    """
    Checks message text for profanity, targeted insults, or custom bad words.
    Counters invisible unicode, leetspeak, punctuation splitting, and homoglyph bypasses.
    Filters out safe words (e.g. "употреблять") and allowed words to prevent false positives,
    UNLESS the word contains hard profanity.
    Returns (has_violation, matched_word_or_reason).
    """
    if not text:
        return False, None

    text_clean = clean_invisible(text)

    # 0. Check Latin / Translit Profanity (e.g. xuy, pizda, blyat, etc.)
    latin_match = LATIN_PROFANITY_REGEX.search(text_clean)
    if latin_match and not _is_safe_word(latin_match.group(0), custom_allowed_words):
        return True, latin_match.group(0)

    normalized = normalize_text(text_clean)
    collapsed = collapse_repeated(normalized)

    # 1. Strip punctuation symbols (e.g. 'х.у.й', 'х_у_й', 'п-и-з-д-а') preserving space separation
    punc_stripped = re.sub(r'[\.\,_\-\*\~\+\=\/\\]+', ' ', normalized)
    punc_collapsed = collapse_repeated(punc_stripped)

    # 2. Collapse single-letter spaced words (e.g. 'х у й' -> 'хуй') without chopping normal words like "я тебе"
    spaced_collapsed = punc_stripped
    for _ in range(5):
        spaced_collapsed = re.sub(r'\b([а-яa-z0-9])\s+(?=[а-яa-z0-9]\b)', r'\1', spaced_collapsed, flags=re.IGNORECASE)

    for check_str in (normalized, collapsed, punc_stripped, punc_collapsed, spaced_collapsed):
        words = re.findall(r'[а-яёa-z0-9]+', check_str, re.IGNORECASE)
        for word in words:
            if _is_safe_word(word, custom_allowed_words):
                continue
            match = RUSSIAN_PROFANITY_REGEX.search(word)
            if match:
                return True, match.group(0)
            latin_m = LATIN_PROFANITY_REGEX.search(word)
            if latin_m:
                return True, latin_m.group(0)

    # 2. Check collapsed text word by word (for repeated character bypasses like "ххууйй")
    collapsed_words = re.findall(r'[а-яёa-z0-9]+', collapsed, re.IGNORECASE)
    for word in collapsed_words:
        if _is_safe_word(word, custom_allowed_words):
            continue
        match = RUSSIAN_PROFANITY_REGEX.search(word)
        if match:
            return True, match.group(0)

    # 3. Check Custom Bad Words from DB
    if custom_bad_words:
        # Build text without safe words for custom checks
        unsafe_words = [w for w in words if not _is_safe_word(w, custom_allowed_words)]
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
            unsafe_collapsed = " ".join(w for w in collapsed_words if not _is_safe_word(w, custom_allowed_words))
            if re.search(pattern, unsafe_collapsed, re.IGNORECASE):
                return True, bw

    return False, None

