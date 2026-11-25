"""Deterministic ingredient line parser and canonicalizer for fridge matching."""
from dataclasses import dataclass
import re

UNICODE_FRACTIONS = {
    "¼": 0.25, "½": 0.5, "¾": 0.75,
    "⅓": 1 / 3, "⅔": 2 / 3,
    "⅕": 0.2, "⅖": 0.4, "⅗": 0.6, "⅘": 0.8,
    "⅙": 1 / 6, "⅚": 5 / 6,
    "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}

UNITS = {
    "tsp", "tsps", "teaspoon", "teaspoons",
    "tbsp", "tbsps", "tbs", "tablespoon", "tablespoons",
    "cup", "cups", "c",
    "oz", "ounce", "ounces", "fl oz", "fluid ounce", "fluid ounces",
    "lb", "lbs", "pound", "pounds",
    "g", "gm", "gms", "gram", "grams",
    "kg", "kgs", "kilogram", "kilograms",
    "ml", "mls", "milliliter", "milliliters", "millilitre", "millilitres",
    "l", "liter", "liters", "litre", "litres",
    "pt", "pint", "pints", "qt", "quart", "quarts", "gal", "gallon", "gallons",
    "pinch", "pinches", "dash", "dashes", "drop", "drops",
    "clove", "cloves", "head", "heads", "bulb", "bulbs",
    "bunch", "bunches", "sprig", "sprigs", "stalk", "stalks", "rib", "ribs",
    "slice", "slices", "piece", "pieces", "strip", "strips", "wedge", "wedges",
    "can", "cans", "tin", "tins", "jar", "jars", "bottle", "bottles",
    "package", "packages", "pkg", "packet", "packets", "pack", "packs", "box", "boxes",
    "bag", "bags", "handful", "handfuls", "knob", "knobs", "thumb", "inch", "inches", "cm",
    "stick", "sticks", "sheet", "sheets", "leaf", "leaves", "ear", "ears", "fillet", "fillets",
    "rasher", "rashers", "link", "links", "pod", "pods", "strip", "strips",
}

UNIT_CANON = {
    "teaspoon": "tsp", "teaspoons": "tsp", "tsps": "tsp",
    "tablespoon": "tbsp", "tablespoons": "tbsp", "tbsps": "tbsp", "tbs": "tbsp",
    "cups": "cup", "c": "cup",
    "ounce": "oz", "ounces": "oz",
    "pound": "lb", "pounds": "lb", "lbs": "lb",
    "gram": "g", "grams": "g", "gm": "g", "gms": "g",
    "kilogram": "kg", "kilograms": "kg", "kgs": "kg",
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml", "millilitres": "ml", "mls": "ml",
    "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "cloves": "clove", "bunches": "bunch", "sprigs": "sprig", "stalks": "stalk",
    "slices": "slice", "cans": "can", "tins": "tin", "sticks": "stick", "leaves": "leaf",
}

LEADING_ADJECTIVES = {
    "fresh", "freshly", "dried", "dry", "ground", "whole", "raw", "cooked", "uncooked",
    "large", "medium", "small", "tiny", "big", "extra-large", "medium-sized", "good-quality",
    "good", "quality", "best", "fine", "finely", "coarse", "coarsely", "rough", "roughly",
    "thin", "thinly", "thick", "thickly", "chopped", "diced", "minced", "sliced", "grated",
    "shredded", "crushed", "peeled", "pitted", "seeded", "halved", "quartered", "cubed",
    "trimmed", "washed", "rinsed", "drained", "packed", "loosely", "lightly", "firmly",
    "unsalted", "salted", "sweet", "unsweetened", "sweetened", "plain", "pure", "organic",
    "free-range", "pasture-raised", "cold", "warm", "hot", "boiling", "chilled", "frozen",
    "thawed", "softened", "melted", "toasted", "roasted", "blanched", "ripe", "unripe",
    "boneless", "skinless", "bone-in", "skin-on", "lean", "fatty", "smoked", "cured",
    "heaped", "heaping", "level", "scant", "generous", "rounded", "about", "approx",
    "approximately", "around", "roughly", "just", "only", "plus", "extra", "more",
    "a", "an", "the", "of", "some", "few", "couple", "several",
}

# UK <-> US and common culinary aliases -> canonical name
ALIASES = {
    "aubergine": "eggplant",
    "aubergines": "eggplant",
    "courgette": "zucchini",
    "courgettes": "zucchini",
    "coriander": "cilantro",
    "coriander leaf": "cilantro",
    "coriander leaves": "cilantro",
    "fresh coriander": "cilantro",
    "spring onion": "scallion",
    "spring onions": "scallion",
    "green onion": "scallion",
    "green onions": "scallion",
    "salad onion": "scallion",
    "rocket": "arugula",
    "wild rocket": "arugula",
    "caster sugar": "sugar",
    "superfine sugar": "sugar",
    "granulated sugar": "sugar",
    "white sugar": "sugar",
    "icing sugar": "powdered sugar",
    "confectioners sugar": "powdered sugar",
    "confectioners' sugar": "powdered sugar",
    "plain flour": "all-purpose flour",
    "all purpose flour": "all-purpose flour",
    "white flour": "all-purpose flour",
    "self raising flour": "self-rising flour",
    "self-raising flour": "self-rising flour",
    "strong white bread flour": "bread flour",
    "strong bread flour": "bread flour",
    "double cream": "heavy cream",
    "whipping cream": "heavy cream",
    "heavy whipping cream": "heavy cream",
    "single cream": "light cream",
    "prawn": "shrimp",
    "prawns": "shrimp",
    "king prawn": "shrimp",
    "king prawns": "shrimp",
    "tiger prawn": "shrimp",
    "tiger prawns": "shrimp",
    "beef mince": "ground beef",
    "minced beef": "ground beef",
    "lamb mince": "ground lamb",
    "minced lamb": "ground lamb",
    "pork mince": "ground pork",
    "minced pork": "ground pork",
    "bicarbonate of soda": "baking soda",
    "bicarb": "baking soda",
    "cornflour": "cornstarch",
    "rapeseed oil": "canola oil",
    "groundnut oil": "peanut oil",
    "extra virgin olive oil": "olive oil",
    "extra-virgin olive oil": "olive oil",
    "light olive oil": "olive oil",
    "kosher salt": "salt",
    "sea salt": "salt",
    "flaky sea salt": "salt",
    "flaky salt": "salt",
    "fine sea salt": "salt",
    "table salt": "salt",
    "maldon salt": "salt",
    "black peppercorn": "black pepper",
    "black peppercorns": "black pepper",
    "peppercorn": "black pepper",
    "peppercorns": "black pepper",
    "cracked black pepper": "black pepper",
    "ground black pepper": "black pepper",
    "garlic clove": "garlic",
    "garlic cloves": "garlic",
    "clove garlic": "garlic",
    "cloves garlic": "garlic",
    "yellow onion": "onion",
    "brown onion": "onion",
    "white onion": "onion",
    "spanish onion": "onion",
    "tinned tomato": "canned tomato",
    "tinned tomatoes": "canned tomato",
    "canned tomatoes": "canned tomato",
    "chopped tomatoes": "canned tomato",
    "crushed tomatoes": "canned tomato",
    "plum tomatoes": "canned tomato",
    "san marzano tomatoes": "canned tomato",
    "passata": "tomato puree",
    "tomato passata": "tomato puree",
    "tomato purée": "tomato puree",
    "parmigiano reggiano": "parmesan",
    "parmigiano-reggiano": "parmesan",
    "parmigiano": "parmesan",
    "parmesan cheese": "parmesan",
    "pecorino romano": "pecorino",
    "chicken stock": "chicken broth",
    "vegetable stock": "vegetable broth",
    "veg stock": "vegetable broth",
    "beef stock": "beef broth",
    "broad bean": "fava bean",
    "broad beans": "fava bean",
    "french bean": "green bean",
    "french beans": "green bean",
    "runner bean": "green bean",
    "runner beans": "green bean",
    "mangetout": "snow pea",
    "swede": "rutabaga",
    "gammon": "ham",
    "streaky bacon": "bacon",
    "back bacon": "bacon",
    "pancetta": "pancetta",
    "flat-leaf parsley": "parsley",
    "flat leaf parsley": "parsley",
    "italian parsley": "parsley",
    "curly parsley": "parsley",
    "red chilli": "red chile",
    "red chillies": "red chile",
    "chilli": "chile",
    "chillies": "chile",
    "chilli flakes": "red pepper flakes",
    "chili flakes": "red pepper flakes",
    "crushed red pepper": "red pepper flakes",
    "crushed red pepper flakes": "red pepper flakes",
    "dried chilli flakes": "red pepper flakes",
    "garbanzo bean": "chickpea",
    "garbanzo beans": "chickpea",
    "bell pepper": "bell pepper",
    "red pepper": "red bell pepper",
    "green pepper": "green bell pepper",
    "yellow pepper": "yellow bell pepper",
    "capsicum": "bell pepper",
    "scallions": "scallion",
    "shallots": "shallot",
    "carrots": "carrot",
    "onions": "onion",
    "lemons": "lemon",
    "limes": "lime",
    "oranges": "orange",
    "eggs": "egg",
    "egg yolks": "egg yolk",
    "egg whites": "egg white",
    "tomatoes": "tomato",
    "cherry tomatoes": "cherry tomato",
    "potatoes": "potato",
    "new potatoes": "potato",
    "sweet potatoes": "sweet potato",
    "mushrooms": "mushroom",
    "chickpeas": "chickpea",
    "lentils": "lentil",
    "anchovies": "anchovy",
    "anchovy fillets": "anchovy",
    "capers": "caper",
    "olives": "olive",
    "black olives": "olive",
    "green olives": "olive",
    "kalamata olives": "olive",
    "chicken thighs": "chicken thigh",
    "chicken breasts": "chicken breast",
    "chicken legs": "chicken leg",
    "chicken wings": "chicken wing",
    "pork chops": "pork chop",
    "lamb shanks": "lamb shank",
    "bay leaves": "bay leaf",
    "thyme sprigs": "thyme",
    "rosemary sprigs": "rosemary",
    "sage leaves": "sage",
    "basil leaves": "basil",
    "mint leaves": "mint",
    "lemon juice": "lemon",
    "lemon zest": "lemon",
    "lime juice": "lime",
    "lime zest": "lime",
    "orange juice": "orange",
    "orange zest": "orange",
    "natural yogurt": "yogurt",
    "greek yoghurt": "greek yogurt",
    "yoghurt": "yogurt",
}

STAPLES = {
    "salt", "black pepper", "pepper", "water", "ice", "ice water", "warm water", "hot water",
    "olive oil", "vegetable oil", "canola oil", "neutral oil", "sunflower oil", "cooking oil",
    "butter", "sugar", "brown sugar", "all-purpose flour", "flour", "garlic", "onion",
    "bay leaf", "red pepper flakes", "baking powder", "baking soda", "vanilla extract", "vanilla",
    "soy sauce", "white wine vinegar", "red wine vinegar", "apple cider vinegar", "vinegar",
    "cornstarch", "honey", "dijon mustard", "mustard",
}

QTY_RE = re.compile(
    r"^\s*(?P<qty>(?:\d+\s+)?\d+/\d+|\d+(?:\.\d+)?|[¼½¾⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|\d+\s*[¼½¾⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])"
    r"(?:\s*(?:[-–—]|to)\s*(?:\d+\s+)?(?:\d+/\d+|\d+(?:\.\d+)?|[¼½¾⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]))?\s*",
    re.IGNORECASE,
)

PAREN_RE = re.compile(r"\([^)]*\)")
SPLIT_PREP_RE = re.compile(r"[,;]\s*|\s+-\s+|\s+–\s+|\s+—\s+")
TRAILING_PHRASE_RE = re.compile(
    r"\b(?:to taste|for serving|for garnish|for frying|for dusting|for greasing|for brushing|"
    r"as needed|if needed|plus more.*|or more.*|or to taste|divided|optional|"
    r"at room temperature|room temperature|cut into.*|sliced into.*|chopped into.*|"
    r"peeled and.*|washed and.*|trimmed and.*|drained and.*|rinsed and.*|patted dry)\b.*$",
    re.IGNORECASE,
)


@dataclass
class ParsedIngredient:
    raw_text: str
    canonical: str | None
    quantity: float | None
    unit: str | None
    prep: str | None
    optional: bool


def _parse_num(s: str) -> float | None:
    s = s.strip()
    total = 0.0
    for ch, val in UNICODE_FRACTIONS.items():
        if ch in s:
            s = s.replace(ch, "").strip()
            total += val
    if not s:
        return total or None
    parts = s.split()
    for p in parts:
        if "/" in p:
            a, b = p.split("/", 1)
            try:
                total += float(a) / float(b)
            except (ValueError, ZeroDivisionError):
                return None
        else:
            try:
                total += float(p)
            except ValueError:
                return None
    return total


def _singularize(name: str) -> str:
    if name in ALIASES:
        return ALIASES[name]
    if len(name) > 4 and name.endswith("ies"):
        cand = name[:-3] + "y"
        return ALIASES.get(cand, cand)
    if len(name) > 4 and name.endswith("es") and not name.endswith(("cheese", "sauce", "paste", "puree", "chives", "leaves")):
        if name.endswith(("oes", "ches", "shes", "xes")):
            cand = name[:-2]
            return ALIASES.get(cand, cand)
    if len(name) > 3 and name.endswith("s") and not name.endswith(("ss", "us", "is", "as", "os", "cous", "humus", "molasses", "oats", "greens", "beans", "peas", "berries", "noodles", "flakes", "crumbs")):
        cand = name[:-1]
        if cand in ALIASES:
            return ALIASES[cand]
        return cand
    return name


def canonicalize_name(text: str) -> str | None:
    s = text.lower().strip()
    s = PAREN_RE.sub(" ", s)
    s = TRAILING_PHRASE_RE.sub("", s)
    # Handle "X or Y" -> keep first choice for primary matching
    if " or " in s:
        s = s.split(" or ", 1)[0]
    s = re.sub(r"[^a-z0-9\s'-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" '-")
    if not s:
        return None
    if s in ALIASES:
        return ALIASES[s]
    # Strip leading adjectives/units/fillers
    words = s.split()
    while len(words) > 1 and (words[0] in LEADING_ADJECTIVES or words[0] in UNITS):
        cand = " ".join(words)
        if cand in ALIASES:
            return ALIASES[cand]
        words.pop(0)
    # Strip leading "of" again if exposed
    while len(words) > 1 and words[0] in ("of", "a", "an", "the"):
        words.pop(0)
    if not words:
        return None
    cleaned = " ".join(words)
    if cleaned in ALIASES:
        return ALIASES[cleaned]
    cleaned = _singularize(cleaned)
    if len(cleaned) < 2 or len(cleaned) > 60:
        return None
    return cleaned


def parse_ingredient(raw: str) -> ParsedIngredient:
    raw_clean = " ".join(str(raw).split())
    optional = "optional" in raw_clean.lower()

    work = raw_clean
    # Remove leading bullets/dashes
    work = re.sub(r"^[\-\*\u2022]+\s*", "", work)
    # Strip parenthetical conversions like "(250g)" before qty/unit parsing
    work_noparen = PAREN_RE.sub(" ", work)
    work_noparen = re.sub(r"\s+", " ", work_noparen).strip()

    qty = None
    m = QTY_RE.match(work_noparen)
    if m:
        qty = _parse_num(m.group("qty"))
        work_noparen = work_noparen[m.end():].strip()
        # Handle attached metric unit like "200g" or "50ml"
        m_att = re.match(r"^([a-zA-Z]+)\.?\s+(.*)$", work_noparen)
        if m_att and m_att.group(1).lower() in UNITS:
            pass
    else:
        # Handle leading "200g flour" with no space
        m_glued = re.match(r"^(\d+(?:\.\d+)?)(g|kg|ml|l|oz|lb|lbs|tsp|tbsp)\b\.?\s*(.*)$", work_noparen, re.IGNORECASE)
        if m_glued:
            qty = float(m_glued.group(1))
            work_noparen = f"{m_glued.group(2)} {m_glued.group(3)}".strip()

    unit = None
    tokens = work_noparen.split()
    if tokens:
        t0 = tokens[0].rstrip(".").lower()
        if t0 in UNITS:
            unit = UNIT_CANON.get(t0, t0)
            tokens = tokens[1:]
            if tokens and tokens[0].lower() == "of":
                tokens = tokens[1:]
    rest = " ".join(tokens).strip()

    parts = SPLIT_PREP_RE.split(rest, maxsplit=1)
    main_part = parts[0].strip()
    prep = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None

    canonical = canonicalize_name(main_part)
    return ParsedIngredient(
        raw_text=raw_clean,
        canonical=canonical,
        quantity=qty,
        unit=unit,
        prep=prep,
        optional=optional,
    )


def is_staple_name(canonical: str) -> bool:
    return canonical in STAPLES
