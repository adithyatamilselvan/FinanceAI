"""Lightweight, transparent rule-based categorisation. No ML model is trained or claimed.

Order of precedence: (1) user-learned rules (from manual corrections), (2) built-in keyword rules.
"""
import re

from ..util import merchant_key, normalize_description

EXPENSE_CATEGORIES = [
    "Housing", "Food", "Groceries", "Transport", "Healthcare", "Shopping", "Entertainment", "Education",
    "Utilities", "Insurance", "Travel", "Loan Repayment", "Subscriptions", "Other",
]
INCOME_CATEGORIES = ["Salary", "Business Income", "Interest", "Other Income"]

# Fixed = roughly the same obligation every month. Everything else is variable.
FIXED_CATEGORIES = {"Housing", "Utilities", "Insurance", "Loan Repayment", "Subscriptions"}
# Essential = needs; the optimiser never cuts these below a high floor.
ESSENTIAL_CATEGORIES = {"Housing", "Groceries", "Healthcare", "Utilities", "Insurance", "Loan Repayment", "Education"}

# Order matters: first match wins, so specific categories precede broad ones.
_EXPENSE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Loan Repayment", ["emi", "loan", "repayment", "bajaj finance", "home loan", "car loan"]),
    ("Subscriptions", ["netflix", "spotify", "prime video", "amazon prime", "hotstar", "youtube premium", "subscription",
                        "icloud", "google one", "apple com bill", "adobe", "microsoft 365", "chatgpt", "disney"]),
    ("Insurance", ["insurance", "lic ", "premium", "policybazaar", "hdfc ergo", "star health", "icici lombard"]),
    ("Travel", ["irctc", "makemytrip", "goibibo", "cleartrip", "indigo", "air india", "airlines", "airline", "hotel",
                "oyo", "airbnb", "flight", "booking com", "vistara", "railway"]),
    ("Housing", ["rent", "landlord", "housing society", "society maintenance", "maintenance charges", "nobroker"]),
    ("Utilities", ["electricity", "bescom", "water bill", "gas bill", "broadband", "airtel", "jio", "vodafone", "bsnl",
                   "recharge", "internet", "act fibernet", "tata power", "mahanagar gas", "utility"]),
    ("Groceries", ["bigbasket", "blinkit", "zepto", "grofers", "dmart", "supermarket", "grocery", "groceries",
                   "reliance fresh", "instamart", "more retail", "vegetables", "kirana", "milk"]),
    ("Food", ["swiggy", "zomato", "restaurant", "cafe", "coffee", "starbucks", "mcdonald", "kfc", "domino", "pizza",
              "dining", "bakery", "burger", "biryani", "food", "eatery", "dunzo"]),
    ("Transport", ["uber", "ola cabs", "ola ", "rapido", "metro", "fuel", "petrol", "diesel", "fastag", "parking",
                   "bus", "cab", "indian oil", "hpcl", "bpcl", "shell", "toll"]),
    ("Healthcare", ["pharmacy", "apollo", "hospital", "clinic", "medplus", "1mg", "pharmeasy", "doctor", "diagnostic",
                    "medical", "dental", "lab"]),
    ("Education", ["tuition", "school", "college", "university", "course", "udemy", "coursera", "exam fee", "books"]),
    ("Entertainment", ["cinema", "pvr", "inox", "bookmyshow", "movie", "gaming", "steam", "concert", "playstation", "xbox"]),
    ("Shopping", ["amazon", "flipkart", "myntra", "ajio", "nykaa", "meesho", "mall", "store", "shopping", "decathlon", "ikea"]),
]
_INCOME_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Salary", ["salary", "payroll", "sal credit", "wages"]),
    ("Interest", ["interest", "int pd", "int credit", "int cr", "dividend"]),
    ("Business Income", ["invoice", "client payment", "business", "consulting fee", "freelance"]),
]
_TRANSFER_HINTS = ["self transfer", "own account", "transfer to self", "to self", "own acct", "a c transfer",
                   "credit card payment", "cc payment", "creditcard payment", "card bill payment", "sweep"]
_REFUND_HINTS = ["refund", "reversal", "reversed", "chargeback", "cashback", "rev "]


def _compile(words: list[str]) -> re.Pattern:
    return re.compile("|".join(r"(?<![a-z0-9])" + re.escape(w.strip()) + (r"(?![a-z0-9])" if not w.endswith(" ") else "") for w in words))


_EXP_PATTERNS = [(c, _compile(w)) for c, w in _EXPENSE_KEYWORDS]
_INC_PATTERNS = [(c, _compile(w)) for c, w in _INCOME_KEYWORDS]
_TRANSFER_RE = _compile(_TRANSFER_HINTS)
_REFUND_RE = _compile(_REFUND_HINTS)


def is_transfer_text(desc: str) -> bool:
    return bool(_TRANSFER_RE.search(normalize_description(desc)))


def is_refund_text(desc: str) -> bool:
    return bool(_REFUND_RE.search(normalize_description(desc)))


def categorize(description: str, kind: str, user_rules: dict[str, str] | None = None) -> tuple[str, str]:
    """Return (category, source) where source is 'user_rule' | 'keyword' | 'default'.

    kind: 'expense' | 'income' | 'refund' | 'transfer'. Transfers get the category 'Transfer'.
    """
    if kind == "transfer":
        return "Transfer", "default"
    norm = " " + normalize_description(description) + " "
    if user_rules:
        key = merchant_key(description)
        if key and key in user_rules:
            return user_rules[key], "user_rule"
        for kw, cat in user_rules.items():
            if kw and f" {kw} " in norm:
                return cat, "user_rule"
    if kind == "income":
        for cat, pat in _INC_PATTERNS:
            if pat.search(norm):
                return cat, "keyword"
        return "Other Income", "default"
    for cat, pat in _EXP_PATTERNS:
        if pat.search(norm):
            return cat, "keyword"
    return "Other", "default"


def learn_rule_key(description: str) -> str:
    """The key stored when a user corrects a category (merchant key of the description)."""
    return merchant_key(description)


def load_rules(db, uid) -> dict[str, str]:
    return {r["keyword"]: r["category"] for r in db.categories.find({"user_id": uid, "kind": "rule"})}
