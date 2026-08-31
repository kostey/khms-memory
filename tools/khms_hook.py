#!/usr/bin/env python3
"""KHMS harness hook — the RETRIEVAL FLOOR.

One executable, wired to five harness events (see claude-code/README.md):

  SessionStart        report an unreviewed inbox — the review is the day's first duty
  UserPromptSubmit    score the operator's message, inject matching cards
  PreToolUse:Bash     run precheck.sh automatically for a named risky-command class
  PreToolUse:Edit|Write   score the file path and the edited text
  PostToolUse:Bash / PostToolUseFailure   score the command and its error lines

Why a floor at all: a rule that says "consult the base first" fails by never
firing, not by being wrong, and the moments it fails in are exactly the moments
the actor is too deep in a problem to remember it exists. So the cheap part is
automated, on a budget. It does NOT discharge the duty to search explicitly
before a hypothesis or a proposal — the hook cannot fire on a sentence that has
not been said yet.

Design rules this file obeys:
  * FAIL OPEN. Any exception is logged and swallowed; a broken memory system must
    never break the session it is trying to help.
  * EVERY DECISION IS LOGGED, including the silent ones, with the reason a card
    above the bar was not injected (dedup / cap / threshold). A hook that stops
    firing otherwise looks exactly like a quiet day.
  * BOUNDED. Thresholds, at most two cards, a character cap, a rate cap per
    window, a per-card dedup TTL — all of them §7 configuration, listed together
    at the top and meant to be recalibrated against your own .inject.log.

Kill switch: touch $KHMS_ROOT/tools/.hooks-off   (or export KHMS_HOOKS_OFF=1)
"""
import datetime
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import khms_paths as P      # noqa: E402
import khms_search as ks    # noqa: E402

# --------------------------------------------------------------- parameters
# Spec §7: calibrate these against your own logs; do not treat them as rules.
THRESHOLD = {                  # minimum score for the top card, per event
    "UserPromptSubmit": 18.0,
    "PostToolUse": 16.0,
    "PostToolUseFailure": 15.0,
    "PreToolUse": 16.0,
}
SECOND_CARD_MIN = 14.0         # a second card rides along only above this
MAX_PRIMARY = 2                # cards per injection, before correctors/umbrella
CHAR_CAP = 900                 # hard cap on injected characters
RATE_MAX = 3                   # injections per RATE_WINDOW …
RATE_WINDOW = 600.0            # … seconds
BYPASS_MIN = 18.0              # a top hit this good buys ONE extra ride per window
BYPASS_PER_WINDOW = 1
QUERY_COOLDOWN = 900.0         # do not re-answer the same query text for 15 min
DEDUP_TTL = 12 * 3600.0        # a card may be re-injected after this long
DAMP_ALPHA = 0.5               # length damping exponent (0 disables)
DAMP_PIVOT = 85                # ≈ median distinct-token count of a card body
MIN_PROMPT_LEN = 12
PRECHECK_COOLDOWN = 1800.0     # per session, per tag set
PRECHECK_CAP = 800             # chars
PRECHECK_TIMEOUT = 8           # seconds
LOG_Q = 200                    # chars of the query kept in the audit log

# Length damping (why it exists): the score is a SUM of per-token idf, so it grows
# with the number of distinct tokens a card happens to contain, and long grab-bag
# cards win queries they have nothing to do with. Pivoted sqrt normalisation, with
# the pivot set to the base's own median, leaves a typical card untouched.

# Which tool events are worth scoring at all. Text that matches neither of these
# is not searched, so the hook stays quiet on routine `ls` and `git status`.
ERROR_RE = re.compile(
    r"error|fail|traceback|exception|denied|timeout|refused|no such|not found"
    r"|segfault|abort|cannot|unable|invalid|dead|inactive", re.I)
# DOMAIN_RE is the one thing you MUST edit for your own work: it is the list of
# words that mean "this is about the domain the base knows something about".
# Too broad and every keystroke searches; too narrow and the floor never fires.
DOMAIN_RE = re.compile(
    r"deploy|config|service|daemon|sensor|driver|firmware|schema|migration"
    r"|pipeline|calibrat|threshold|timeout|latency|checksum", re.I)

# Risky-command class: these run precheck.sh by themselves, because "run precheck
# before dangerous things" is a duty that fails exactly when it is needed. Tags
# must be tags your base actually uses.
RISKY = [
    (re.compile(r"rsync\b[^\n|;]*--delete"), ("deployment",)),
    (re.compile(r"\bgit\s+(push\s+(-f|--force)|tag\s+(-f|--force))"), ("git",)),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r"), ("filesystem",)),
    (re.compile(r"\bsystemctl\b|\bservice\s+\w+\s+(stop|restart)"), ("service", "deployment")),
    (re.compile(r"\b(docker|podman)\s+(system\s+prune|rm|rmi)\b"), ("containers",)),
    (re.compile(r"\bkubectl\s+delete\b"), ("containers", "deployment")),
    (re.compile(r"\bterraform\s+(apply|destroy)\b"), ("deployment",)),
    (re.compile(r"\bdrop\s+(table|database)\b", re.I), ("database",)),
    (re.compile(r"\bdd\s+if="), ("filesystem",)),
    (re.compile(r"(^|[\s;&|'\"])[\w./-]*deploy/"), ("deployment",)),
]

REPORT_DIRECTIVE = (
    "\n\nREQUIRED in your reply, one line: "
    "`recall: <what I searched for> -> {found}`\n"
    "If a card is relevant, open it whole (memory/know/<id>.md) and follow its links. "
    "If nothing fits, say so — \"nothing on record\" is a valid and useful answer.\n"
    "\nALSO REQUIRED whenever you assert anything about the STATE of a system "
    "(running / stopped, deployed, measured X): "
    "`verified: <the command or file:line whose output I just read>`\n"
    "A process id, an exit code, a value in a config file or a subagent's report are "
    "signals about another layer, not verification of the state itself. If you cannot "
    "name the command, write \"unverified\" — that is a valid answer; a silent claim is not.\n"
    "A telemetry/diagnostics VALUE counts as verification only together with its "
    "freshness evidence (stale flag, sample age, publisher liveness): a perfectly "
    "constant physical reading is a freeze suspect before it is a stability claim.\n"
    "\nAND THE SECOND HALF OF THAT SAME DUTY, one more line: "
    "`if I were wrong: <how that output would have looked DIFFERENT>`\n"
    "The `verified:` line asks only whether something was run, never whether its output "
    "supports the claim — and a check that passes whether or not the thing it guards is "
    "healthy is not a check. Four traps it is there to catch: an empty search result is "
    "NOT evidence of absence until that same pattern has shown it can find what it is "
    "looking for; a metric of the form \"count over the last N seconds\" must not be read "
    "until N seconds after an intervention, or the window still covers the period before "
    "the fix; `ps -o pcpu` is an average over the process's whole LIFETIME, not its load "
    "right now (`top -bn2` measures that); and when COMPARING TWO MACHINES you must name "
    "the ONE instrument used on both, because two instruments measure two quantities and "
    "the difference between them means nothing.\n"
)


def now():
    return time.time()


def log(event, query, action, results=None, skips=None, qh=""):
    results = results or []
    top = f"{results[0][2]['id']}:{results[0][0]:.1f}" if results else "-"
    if skips:
        action += " skipped=" + ",".join(f"{k}:{w}" for k, w in skips[:3])
    q = re.sub(r"\s+", " ", query or "")[:LOG_Q]
    try:
        with open(P.INJECT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')}"
                    f" | {event} | q={q} | nhits={len(results)} | top={top} | {action}"
                    f"{(' qh=' + qh) if qh else ''}\n")
    except OSError:
        pass


def load_state(session_id):
    os.makedirs(P.HOOK_STATE, exist_ok=True)
    path = os.path.join(
        P.HOOK_STATE,
        re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "nosession") + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    for k, v in (("injected", {}), ("recent", []), ("queries", {}),
                 ("bypass", []), ("precheck", {})):
        st.setdefault(k, v)
    return path, st


def save_state(path, st):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except OSError:
        pass


def emit(event, text):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": text}}))


# ------------------------------------------------------------------ scoring
def prepare_cards(cards):
    """Attach the distinct-token count used for length damping, memoised per
    (card id, body length). Cards are immutable once approved and a body that
    does change changes its length, so the key is sound; a corrupt cache only
    costs the recomputation."""
    cache = {}
    try:
        with open(P.LEN_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    dirty = False
    for c in cards:
        key = f"{c['id']}:{len(c['nbody'])}"
        n = cache.get(key)
        if n is None:
            n = max(1, len(set(ks.tokens(c["nbody"]))))
            cache[key] = n
            dirty = True
        c["_ntok"] = n
    if dirty:
        try:
            tmp = P.LEN_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            os.replace(tmp, P.LEN_CACHE)
        except OSError:
            pass
    return cards


def damp(score, ntok):
    if DAMP_ALPHA <= 0:
        return score
    return score * min(1.0, math.pow(DAMP_PIVOT / float(ntok), DAMP_ALPHA))


MAX_WARN = 2                   # correction pointers per served card
WARN_CHARS = 110               # of the corrector's first line


def warn_line(cor):
    """The reverse edge, one line: who corrects the card just served."""
    return f"  ! CORRECTED BY {cor['id']}: {cor['first'][:WARN_CHARS]}"


def card_line(c, score=None, prefix="- "):
    flags = []
    if c["status"] in ("refuted", "challenged", "superseded"):
        flags.append("!" + c["status"].upper())
    if "gotcha" in c["tags"]:
        flags.append("gotcha")
    flag = ("[" + ",".join(flags) + "] ") if flags else ""
    sc = f" ({score:.0f})" if score is not None else ""
    return f"{prefix}{c['id']}{sc} {flag}{c['first'][:100]}"


def correctors_of(c, by_id, rev):
    """Cards that correct c: the reverse edges (someone's contradicts/supersedes
    points AT c) plus c's own forward refuted_by. Never raises — a base with one
    malformed link must degrade to "no correctors", not cost the injection."""
    out, seen = [], set()
    try:
        for cor in ([by_id[k] for k in (c["links"].get("refuted_by") or []) if k in by_id]
                    + list(rev.get(c["id"], []))):
            if cor["id"] != c["id"] and cor["id"] not in seen:
                seen.add(cor["id"])
                out.append(cor)
    except Exception:
        return []
    return out


def build_injection(cands, cards, injected, t, skips):
    """At most MAX_PRIMARY fresh cards, each optionally preceded by the principle
    it supports and followed by whatever corrects it. A refuted card is never
    served alone: the correction is the useful half.

    Every served card carries its correctors, WHATEVER ITS STATUS. This used to
    happen only for cards already marked challenged/refuted/superseded — but a
    correction arrives before anyone re-statuses the old card, and `active` is
    exactly the state in which a stale card is believed. In the source deployment
    that gap cost four days: a card corrected in August kept its `active` July
    predecessors, and they were served to the operator as the current state of
    the machine, correctly, by the rules as they then stood."""
    by_id = {c["id"]: c for c in cards}
    rev = ks.reverse_correctors(cards)
    lines, used, primaries = [], [], 0
    warn_chars = 0

    def fresh(cid):
        ts = injected.get(cid)
        return ts is None or (t - ts) >= DEDUP_TTL

    for score, _matched, c in cands:
        if primaries >= MAX_PRIMARY:
            skips.append((c["id"], "slots-full"))
            continue
        if not fresh(c["id"]):
            skips.append((c["id"], "dedup"))
            continue
        if c["id"] in used:
            continue
        umbrella = None
        for pid in c["links"]["supports"]:
            u = by_id.get(pid)
            if u and u["level"] == "derived" and u["status"] == "active":
                umbrella = u
                break
        if umbrella and umbrella["id"] not in used and fresh(umbrella["id"]):
            lines.append(card_line(umbrella, prefix="- principle: "))
            used.append(umbrella["id"])
        lines.append(card_line(c, score))
        used.append(c["id"])
        primaries += 1
        correctors = correctors_of(c, by_id, rev)
        if c["status"] in ("challenged", "refuted", "superseded"):
            # unchanged: the refuter is served as a full card line and marked
            # injected, because a refuted card without it is worse than nothing.
            for cor in correctors[:1]:
                if cor["id"] not in used:
                    lines.append(card_line(cor, prefix="  -> corrected by: "))
                    used.append(cor["id"])
                    correctors = [x for x in correctors if x["id"] != cor["id"]]
        # An ACTIVE card that something corrects gets a POINTER, not a card: it
        # consumes no primary slot and is not marked injected, so it can repeat
        # as often as its subject comes up and the card behind it can still be
        # served in full later.
        try:
            for cor in correctors[:MAX_WARN]:
                if cor["id"] in used:
                    continue
                line = warn_line(cor)
                lines.append(line)
                warn_chars += len(line) + 1
        except Exception:
            pass        # a pointer that cannot be built is dropped, never raised
    if not lines:
        return None, []
    text = ("KHMS (auto-recall — cards that may bear on what is happening):\n"
            + "\n".join(lines)
            + "\nIf one is relevant, open it whole (memory/know/<id>.md) and follow its links.")
    # BUDGET: the correction pointers are EXEMPT from CHAR_CAP — the cap on
    # everything else is unchanged (cap plus exactly the characters the pointers
    # added). They are not extra retrieval: they correct content that is being
    # injected anyway, and a truncated correction is the failure this exists to
    # prevent. Bounded by construction: at most MAX_WARN per card, WARN_CHARS
    # each, i.e. ~280 characters against a 900-character cap.
    return text[:CHAR_CAP + warn_chars], used


# ------------------------------------------------------------------- events
def handle_session_start():
    """The morning review is the first duty of the day, so an unreviewed inbox is
    the one thing worth saying before any work starts."""
    unreviewed = []
    try:
        for f in sorted(os.listdir(P.INBOX)):
            m = re.match(r"^(\d{4}-\d{2}-\d{2})(-weekly)?\.md$", f)
            if m and not os.path.exists(os.path.join(P.TOOLS_DIR, f".reviewed-{m.group(1)}")):
                unreviewed.append(f)
    except OSError:
        return
    if unreviewed:
        emit("SessionStart",
             "KHMS: UNREVIEWED INBOX: " + ", ".join(unreviewed)
             + " — review it (approve_inbox.py, build_views.py, report to the operator) "
               "BEFORE other work. Mark it done with: touch tools/.reviewed-<date>")
        log("SessionStart", "inbox-check", f"inject inbox={','.join(unreviewed)}")
    else:
        log("SessionStart", "inbox-check", "silent (inbox clean)")


def risky_tags(cmd):
    out = []
    for rx, tags in RISKY:
        if rx.search(cmd or ""):
            for t in tags:
                if t not in out:
                    out.append(t)
    return out


def run_precheck(tags):
    try:
        r = subprocess.run([os.path.join(P.TOOLS_DIR, "precheck.sh"), *tags],
                           capture_output=True, text=True, timeout=PRECHECK_TIMEOUT)
        out = (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out or out.startswith("precheck: nothing on record"):
        return None
    return out[:PRECHECK_CAP]


def try_precheck(payload, st, t):
    """PreToolUse(Bash) only. The output counts against the injection budget but
    is not blocked by it: the whole point is the command that is about to delete
    something, and that command must not lose its warning to a busy window. The
    per-session, per-tag-set cooldown is what bounds this path instead."""
    cmd = str((payload.get("tool_input") or {}).get("command", ""))
    tags = risky_tags(cmd)
    if not tags:
        return None
    key = "+".join(tags)
    if t - st["precheck"].get(key, 0) < PRECHECK_COOLDOWN:
        return None
    st["recent"] = [x for x in st["recent"] if t - x < RATE_WINDOW]
    body = run_precheck(tags)
    st["precheck"][key] = t
    if not body:
        return None
    st["recent"].append(t)
    return ("KHMS precheck (risky command class: " + key + ") — active policies, "
            "gotchas and refuted dead ends:\n" + body
            + "\nIf any of it bears on this command, open the card before running it.")


def extract_bash_text(payload):
    ti = payload.get("tool_input") or {}
    cmd = str(ti.get("command", ""))[:200]
    out = payload.get("tool_output")
    if isinstance(out, dict):
        out = json.dumps(out, ensure_ascii=False)
    out = str(out or "")
    err_lines = [ln for ln in out[-2500:].splitlines() if ERROR_RE.search(ln)][:3]
    text = cmd + " " + " ".join(err_lines)
    if not DOMAIN_RE.search(text) and not err_lines:
        return None
    return text[:400]


def main():
    if os.environ.get("KHMS_HOOKS_OFF") == "1" or os.path.exists(P.HOOKS_OFF):
        return
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name", "")

    if event == "SessionStart":
        handle_session_start()
        return
    if event not in THRESHOLD:
        return

    tool = payload.get("tool_name")
    if event == "UserPromptSubmit":
        raw = str(payload.get("prompt", ""))
        raw = re.sub(r"<[^>]{1,400}>", " ", raw)          # strip harness/channel tags
        if len(raw.strip()) < MIN_PROMPT_LEN:
            log(event, raw, "skip (short)")
            return
        query = raw[:500]
    elif event == "PreToolUse" and tool in ("Edit", "Write"):
        ti = payload.get("tool_input") or {}
        text = " ".join([str(ti.get("file_path", "")),
                         str(ti.get("old_string", ""))[:200],
                         str(ti.get("new_string", ""))[:200],
                         str(ti.get("content", ""))[:200]])
        if not DOMAIN_RE.search(text):
            return
        query = text[:400]
    else:
        query = extract_bash_text(payload)
        if query is None and not (event == "PreToolUse" and tool == "Bash"):
            return

    path, st = load_state(payload.get("session_id", ""))
    t = now()

    # The risky-command class checks itself, BEFORE retrieval, so a destructive
    # command never loses its warning to the ordinary card budget.
    if event == "PreToolUse" and tool == "Bash":
        pc = try_precheck(payload, st, t)
        if pc is not None:
            save_state(path, st)
            emit(event, pc)
            log(event, str((payload.get("tool_input") or {}).get("command", ""))[:200],
                "PRECHECK")
            return
        if query is None:
            save_state(path, st)
            return

    qh = hashlib.sha1(query.encode("utf-8", "replace")).hexdigest()[:12]
    thr = THRESHOLD[event]

    if t - st["queries"].get(qh, 0) < QUERY_COOLDOWN:
        save_state(path, st)
        log(event, query, "skip (query cooldown)", qh=qh)
        return
    st["queries"][qh] = t
    st["recent"] = [x for x in st["recent"] if t - x < RATE_WINDOW]
    st["bypass"] = [x for x in st["bypass"] if t - x < RATE_WINDOW]

    capped = len(st["recent"]) >= RATE_MAX
    bypass_allowed = (capped and event == "UserPromptSubmit"
                      and len(st["bypass"]) < BYPASS_PER_WINDOW)
    if capped and not bypass_allowed:
        save_state(path, st)
        log(event, query, "skip (rate cap)", qh=qh)
        return

    cards = prepare_cards(ks.load_cards())
    expanded, _added = ks.expand_query(query, ks.load_glossary())
    raw_results, _ = ks.search(expanded, cards=cards, exclude_meta=True, topn=8)
    results = sorted(((damp(s, c["_ntok"]), m, c) for s, m, c in raw_results),
                     key=lambda r: -r[0])
    ks.log_query(query, results, src=f"hook:{event}")

    top_score = results[0][0] if results else 0.0
    skips = []

    if capped:
        # The rate cap used to spend the window first-come, dropping the best
        # question of the hour because three routine ones arrived first. A top hit
        # that clears a high bar buys one extra ride per window; everything else is
        # still dropped, but now the score is in the log, so the miss is diagnosable.
        if top_score < BYPASS_MIN:
            save_state(path, st)
            if results:
                skips.append((results[0][2]["id"], f"cap top={top_score:.1f}"))
            if event == "UserPromptSubmit":
                emit(event, REPORT_DIRECTIVE.format(
                    found="nothing above the bar (rate cap) — the base is silent on this"))
            log(event, query, "skip (rate cap)", results, skips, qh)
            return
        st["bypass"].append(t)

    cands = [r for r in results[:4] if r[0] >= SECOND_CARD_MIN] if results else []
    if not results or top_score < thr:
        if results:
            skips.append((results[0][2]["id"], f"threshold {top_score:.1f}<{thr:.0f}"))
        cands = []

    if not cands:
        save_state(path, st)
        if event == "UserPromptSubmit":
            emit(event, REPORT_DIRECTIVE.format(
                found="nothing above the bar — the base is silent on this"))
        log(event, query, "silent (below threshold)", results, skips, qh)
        return

    text, used = build_injection(cands, cards, st["injected"], t, skips)
    if not text:
        save_state(path, st)
        if event == "UserPromptSubmit":
            emit(event, REPORT_DIRECTIVE.format(
                found="nothing new (the relevant cards already came up this session)"))
        log(event, query, "silent (all deduped)", results, skips, qh)
        return

    for kid in used:
        st["injected"][kid] = t
    st["recent"].append(t)
    save_state(path, st)
    if event == "UserPromptSubmit":
        text += REPORT_DIRECTIVE.format(found="see the cards above")
    emit(event, text)
    log(event, query, f"INJECT [{','.join(used)}] chars={len(text)}", results, skips, qh)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                      # fail open, always
        try:
            with open(P.INJECT_LOG, "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')}"
                        f" | ERROR | {type(e).__name__}: {e}\n")
        except OSError:
            pass
        sys.exit(0)
