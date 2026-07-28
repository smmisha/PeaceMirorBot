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
    # Words containing "тварь" — allow benign words like "утварь"
    "тварина", "утварь", "утвари", "утварью",
    # Names and benign words (юля, буй, буек)
    "юля", "юли", "юле", "юлю", "юлей",
    "буй", "буёк", "буек", "буйный", "буйство", "буйства", "буйствами",
}

# --- Profanity roots ---------------------------------------------------------
# ВАЖНО: корни "еб", "ху", "хер" ищутся ТОЛЬКО с начала слова (или после реальной
# приставки). Без этого якоря фрагмент попадал в середину обычных слов и бот банил
# "хлеба", "требую", "волшебный", "парикмахер", "жребий", "колебания", "чебуреки".

# Приставки, после которых корень остаётся матерным (заебал, доебался, разъебать).
# Односимвольные "с"/"в" сюда НЕ входят: иначе "себе" читается как "с"+"ебе".
_PFX_EB = r'(?:за|по|на|до|у|вы|про|при|пере|недо|ни|раз|разъ|отъ|подъ|съ|сь|объ|изъ)?'
_PFX_HU = r'(?:ни|до|по|на|о|а|не|за|пере|у|вы|от|под|раз)?'

# "еб"/"ёб" + гласная/н/л — покрывает ебать, ебло, ебнул, ёбаный, ебут, ебись
_ROOT_EB = r'[её]б(?:[аеёиуы]|н|л|\b)'
# "ху" + й/я/е/ё/и/у — НЕ трогает худой, хутор, хурма, хуже, художник
_ROOT_HU = r'ху[йяеёиу]'

_HARD_CORE = (
    rf'\b{_PFX_HU}{_ROOT_HU}|'
    rf'\b{_PFX_EB}{_ROOT_EB}|'
    r'долбо[её]б|'
    r'пизд|пзд|пидар|пидор|'
    r'блядь|бляд|\bбля\b|\bблят\b|'
    r'мудак|мудил|'
    r'гандон|гондон|'
    r'залуп'
)

# Мягкие корни: оскорбления и грубость, которые МОЖНО занести в белый список
_SOFT_EXTRA = (
    rf'\b{_PFX_HU}хер|'
    r'шлюх|сука|сучк|сучин|'
    r'ублюд|выродок|гнид|тварь|паскуд|дроч|'
    r'мразь|мрази'
)

# Regex pattern for Russian profanity and insult roots
RUSSIAN_PROFANITY_REGEX = re.compile(f'(?:{_HARD_CORE}|{_SOFT_EXTRA})', re.IGNORECASE)

# Strict Regex pattern for core Russian profanity (hard mat) - CANNOT BE WHITELISTED
HARD_PROFANITY_REGEX = re.compile(f'(?:{_HARD_CORE})', re.IGNORECASE)

# --- Latin / translit --------------------------------------------------------
# Чисто латинские слова НЕ прогоняются через CHAR_MAP (иначе eBay -> "ебау",
# xerox -> "херох", hue -> "хуе"), поэтому здесь перечислены транслит-варианты явно.
_LATIN_PFX = r'(?:za|po|na|do|u|vy|pro|pere|ni|raz|ot|pod)?'
_LATIN_HARD_CORE = (
    rf'\b{_LATIN_PFX}(?:xu[yjiea]|xuya|xuyn|xyi|xyu|hu[yj]|huya|huyn|huesos|xuesos|hueta|xueta)|'
    rf'\b{_LATIN_PFX}(?:[yj]?eb(?:at|al|an|ash|et|ut|u4|uc|lan|lo|nut|nul|is))|'
    r'\bpizd|\bpzd|\bpidor|\bpidar|'
    r'\bblya|\bblia|\bblyad|\bblyat|\bbliad|\bbliat|'
    r'\bdolboeb|\bdolbaeb|\bzalup|'
    r'\bmudak|\bmudil|\bgandon|\bgondon'
)
_LATIN_SOFT_EXTRA = r'\bsuka|\bcuka|\bcyka|\bcuko|\bsuchk|\bbitch|\bmraz'

LATIN_PROFANITY_REGEX = re.compile(f'(?:{_LATIN_HARD_CORE}|{_LATIN_SOFT_EXTRA})', re.IGNORECASE)
LATIN_HARD_PROFANITY_REGEX = re.compile(f'(?:{_LATIN_HARD_CORE})', re.IGNORECASE)

# Продуктивные основы, которые всегда безопасны (Херсон, херсонская, хулиганить...).
# Нужны потому, что перечислить все падежные формы в SAFE_WORDS нереально.
SAFE_STEM_REGEX = re.compile(
    r'^(?:херсон|херувим|хербала|хертфорд|херес|'
    r'хулиган|хулига|хулит|хулы|хула\b|'
    r'волшеб|хлеб|требов|требу|потреб|чебур|жреби|колеб|парикмахер|сучков)',
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


# clean_invisible раскладывает NFKD и срезает диакритику, поэтому "й" -> "и", "ё" -> "е".
# Дублируем белый список в этом же виде, иначе "волшебный"/"буйный" туда не попадают.
SAFE_WORDS |= {clean_invisible(w) for w in SAFE_WORDS}


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

    Встроенный список SAFE_WORDS/SAFE_STEM_REGEX имеет приоритет над проверкой мата:
    он выверен вручную, и раньше проверка hard-мата шла первой и молча отключала
    30 из 186 записей белого списка ("волшебный", "хлеба", "требую", "парикмахер").

    Запрет на whitelisting мата остаётся для КАСТОМНЫХ слов из БД — чтобы админ
    не мог занести /addsafeword пиздец.
    """
    w_lower = word.lower()
    if w_lower in SAFE_WORDS or SAFE_STEM_REGEX.match(w_lower):
        return True
    if custom_allowed_words:
        allowed_set = {w.strip().lower() for w in custom_allowed_words}
        if w_lower in allowed_set and not is_hard_profanity(w_lower):
            return True
    return False


_TOKEN_REGEX = re.compile(r'[а-яёa-z0-9]+', re.IGNORECASE)
_LATIN_ONLY_REGEX = re.compile(r'^[a-z0-9]+$', re.IGNORECASE)


def _text_variants(text_lower: str) -> List[str]:
    """
    Builds variants of the text to counter splitting bypasses:
    исходный текст, текст без пунктуации-разделителей ('х.у.й'), и склейка
    одиночных букв ('х у й' -> 'хуй').
    """
    punc_stripped = re.sub(r'[\.\,_\-\*\~\+\=\/\\]+', ' ', text_lower)
    spaced_collapsed = punc_stripped
    for _ in range(5):
        spaced_collapsed = re.sub(
            r'\b([а-яёa-z0-9])\s+(?=[а-яёa-z0-9]\b)', r'\1', spaced_collapsed, flags=re.IGNORECASE
        )
    return [text_lower, punc_stripped, spaced_collapsed]


def _check_token(token: str, custom_allowed_words: Optional[List[str]] = None) -> Optional[str]:
    """
    Проверяет одно слово. Возвращает само слово при нарушении, иначе None.

    Чисто латинские слова проверяются ТОЛЬКО по явному транслит-списку и не
    прогоняются через CHAR_MAP — иначе "eBay" превращался в "ебау", "xerox" в
    "херох", "hue" в "хуе" и всё это ловилось как мат.
    """
    if _is_safe_word(token, custom_allowed_words):
        return None

    if _LATIN_ONLY_REGEX.match(token):
        for candidate in (token, collapse_repeated(token)):
            if LATIN_PROFANITY_REGEX.search(candidate):
                return token
        return None

    normalized = normalize_text(token)
    for candidate in (normalized, collapse_repeated(normalized)):
        if _is_safe_word(candidate, custom_allowed_words):
            continue
        if RUSSIAN_PROFANITY_REGEX.search(candidate) or LATIN_PROFANITY_REGEX.search(candidate):
            return token
    return None


def find_violation(
    text: str,
    custom_bad_words: Optional[List[str]] = None,
    custom_allowed_words: Optional[List[str]] = None
) -> Tuple[bool, Optional[str]]:
    """
    Checks message text for profanity, targeted insults, or custom bad words.
    Counters invisible unicode, leetspeak, punctuation splitting, and homoglyph bypasses.
    Filters out safe words (e.g. "употреблять") and allowed words to prevent false positives.
    Returns (has_violation, matched_word_or_reason).
    """
    if not text:
        return False, None

    text_lower = clean_invisible(text).lower()
    variants = _text_variants(text_lower)

    # 1. Пословная проверка каждого варианта текста
    for variant in variants:
        for token in _TOKEN_REGEX.findall(variant):
            matched = _check_token(token, custom_allowed_words)
            if matched:
                return True, matched

    # 2. Кастомный стоп-лист из БД (проверяется по тексту без безопасных слов)
    if custom_bad_words:
        base_tokens = _TOKEN_REGEX.findall(text_lower)
        unsafe_tokens = [t for t in base_tokens if not _is_safe_word(t, custom_allowed_words)]
        unsafe_text = " ".join(unsafe_tokens)
        unsafe_collapsed = " ".join(collapse_repeated(normalize_text(t)) for t in unsafe_tokens)

        for bw in custom_bad_words:
            bw_clean = bw.strip().lower()
            if not bw_clean:
                continue

            # Короткие слова — только по границам слова, иначе ловят половину словаря
            if len(bw_clean) <= 3:
                pattern = r'\b' + re.escape(bw_clean) + r'\b'
            else:
                pattern = re.escape(bw_clean)

            if re.search(pattern, unsafe_text, re.IGNORECASE) or re.search(pattern, unsafe_collapsed, re.IGNORECASE):
                return True, bw

    return False, None

