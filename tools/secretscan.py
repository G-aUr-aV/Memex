"""Secret and card-number patterns shared by lint, the pre-commit hook and `memex capture` (stdlib only).

Kept free of vault lookups so the pre-commit hook can use it in the vault and in the framework repo alike.
"""
import re

SECRETS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Atlassian API token", re.compile(r"\bATATT[A-Za-z0-9_=-]{20,}")),
    ("quoted password", re.compile(r"(?i)\b(?:password|passwd|pwd|passphrase|passcode)\b[^\n]{0,20}?\b(?:is|was|=|:)\s*[\"'“‘][^\s\"'”’]{6,}")),
]
SOFT_SECRETS = [
    ("password/secret assignment", re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\"<>]{8,}")),
    ("Aadhaar-like number", re.compile(r"\b\d{4} \d{4} \d{4}\b")),
]
CARD = re.compile(r"(?<![\d.,])(?:\d[ -]?){14}\d(?:[ -]?\d)?(?![\d.,]?\d)")


def luhn(num: str) -> bool:
    digits = [int(c) for c in num if c.isdigit()]
    if len(digits) not in (15, 16):
        return False
    total, parity = 0, len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def hits(text: str):
    """Labels of the hard secret patterns found in text (payment cards included)."""
    found = [label for label, rx in SECRETS if rx.search(text)]
    if any(luhn(m.group(0)) for m in CARD.finditer(text)):
        found.append("payment card number")
    return found
