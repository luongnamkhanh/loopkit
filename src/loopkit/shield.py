"""
loopkit.shield — shared guard at boundaries (flow-level, build once).

mask(text)        redact secrets/PII before text crosses a boundary (Slack post, journal,
                  session persistence). Safe-side: may over-mask (e.g. emails in comments);
                  never under-mask code that looks like a secret.
                  NOTE: the semantic cache stores the verified artifact RAW by design (it
                  lives on-prem and masking could corrupt code); it is masked at post time.
seen_event(id)    dedupe Slack event deliveries (Slack RETRIES on slow ack -> double-run risk).

Deliberately small pattern list; add domain guards (sqlguard-style AST, etc.) only when the
domain needs them. Dedupe is in-memory, durable across restarts once init_dedupe() ran (§8.1).
"""
import pathlib, re
from collections import OrderedDict

MASK = "[REDACTED]"

_PATTERNS = [
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),                 # Slack bot/user tokens
    re.compile(r"xapp-[A-Za-z0-9-]{10,}"),                       # Slack app-level tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),                             # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),           # private key header
    # key=value secrets: value must be >=12 chars AND contain a digit (avoids masking code
    # like `token = get_token(x)`)
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?"
               r"(?=[A-Za-z0-9_\-/+]*\d)[A-Za-z0-9_\-/+]{12,}"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                      # email
    re.compile(r"(?<!\d)(?:\+?84|0)\d{9,10}(?!\d)"),             # VN phone (rough, safe-side)
]


def mask(text: str) -> str:
    if not text:
        return text
    for p in _PATTERNS:
        text = p.sub(MASK, text)
    return text


# --- Slack event dedupe (bounded, in-memory) ---
_seen: "OrderedDict[str, None]" = OrderedDict()
_MAX_SEEN = 1000
_seen_path = None            # set by init_dedupe -> ids also persist to disk (§8.1)


def init_dedupe(path):
    """§8.1 durable dedupe: load the last _MAX_SEEN ids from disk into _seen, trim the
    file to exactly those, and append every new id from now on. Call once at startup."""
    global _seen_path
    p = pathlib.Path(path)
    ids = [i for i in p.read_text().splitlines() if i][-_MAX_SEEN:] if p.exists() else []
    for i in ids:
        _seen[i] = None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(ids) + ("\n" if ids else ""))
    _seen_path = p


def seen_event(event_id: str) -> bool:
    """True if this event_id was already processed (caller should skip the delivery)."""
    if not event_id:
        return False
    if event_id in _seen:
        return True
    _seen[event_id] = None
    if _seen_path:
        with open(_seen_path, "a") as f:                    # ponytail: append-only during a
            f.write(event_id + "\n")                        # session; re-trimmed at startup
    if len(_seen) > _MAX_SEEN:
        _seen.popitem(last=False)
    return False
