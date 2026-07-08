"""
loopkit.memory — shared loop memory (flow-level, build once).

Closes the stateless-agent gap (design §7.3): spawned agents forget everything between runs;
this module is the disk that doesn't. Three tiers (AI Foundation analogue):

  registry  — thread_id -> latest run state                    (registry.json)
  session   — per-thread event history, append-only            (sessions/<thread>.jsonl)
  semantic  — VERIFIED solutions keyed by normalized ticket    (cache.json)

Semantic recall is EXACT-match on normalized (goal, dod) — the honest MVP; similarity/embedding
recall is a later upgrade (needs an embedding endpoint). Single-process use; no file locking.
Only verified work is cached (and, for risky tickets, only after human approval) — the
store-gate principle: never cache what wasn't proven.
"""
import json, hashlib, pathlib, re, threading, time


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def ticket_key(goal: str, dod: str) -> str:
    return hashlib.sha1(f"{_norm(goal)}|{_norm(dod)}".encode()).hexdigest()[:16]


def _safe(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)


class Memory:
    def __init__(self, directory: str):
        self.dir = pathlib.Path(directory)
        (self.dir / "sessions").mkdir(parents=True, exist_ok=True)
        self.reg_path = self.dir / "registry.json"
        self.cache_path = self.dir / "cache.json"
        self._lock = threading.Lock()        # slack_app runs one thread per ticket

    def _load(self, path: pathlib.Path) -> dict:
        return json.loads(path.read_text()) if path.exists() else {}

    # --- registry: thread -> latest run state ---
    def register(self, thread_id: str, **fields):
        with self._lock:
            reg = self._load(self.reg_path)
            cur = reg.get(thread_id, {})
            cur.update(fields, updated_at=time.time())
            reg[thread_id] = cur
            self.reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=1))

    def get_run(self, thread_id: str) -> dict:
        return self._load(self.reg_path).get(thread_id, {})

    # --- session: per-thread history on disk ---
    def append_event(self, thread_id: str, event: dict):
        p = self.dir / "sessions" / f"{_safe(thread_id)}.jsonl"
        with open(p, "a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def events(self, thread_id: str) -> list:
        p = self.dir / "sessions" / f"{_safe(thread_id)}.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    # --- human-door audit trail (four-eyes evidence on disk) ---
    def audit(self, thread_id: str, approver: str, decision: bool):
        self.append_event(thread_id, {"stage": "human_door", "approver": approver,
                                      "approved": decision, "at": time.time()})
        self.register(thread_id, approver=approver, approved=decision)

    # --- semantic cache: verified solutions only ---
    def store(self, goal: str, dod: str, artifact: str):
        with self._lock:
            cache = self._load(self.cache_path)
            cache[ticket_key(goal, dod)] = {"goal": goal, "dod": dod, "artifact": artifact,
                                            "verified_at": time.time()}
            self.cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))

    def recall(self, goal: str, dod: str):
        hit = self._load(self.cache_path).get(ticket_key(goal, dod))
        return hit["artifact"] if hit else None
