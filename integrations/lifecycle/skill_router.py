"""Route an owner prompt to one skill with a System One Choice, and inject that skill.

Skills that skillpack hides from the model (`disable-model-invocation: true`) cost no
standing context, but nothing can load them unless the owner types `/name`. On each
owner prompt this module asks one Choice over the whole skill catalog, and when the
winner is a hidden skill with enough confidence it returns the skill's instructions
for the lifecycle hook to inject. Visible skills stay with the model: the full catalog
is offered only so that a prompt meant for a visible skill does not win a hidden one.

Egress: the first `prompt_chars` characters of the owner prompt, the working
directory's basename and the catalog descriptions go to TypeSafe (`jev_url`), then to
the local Kev server (`kev_url`) if TypeSafe fails. Excluded paths, slash commands and
disabled configurations are refused before any request. Any failure leaves the memory
result unchanged; the router never blocks a prompt.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.request

DEFAULTS = dict(
    enabled=True,
    skills_dir="~/git/skillpack/skills",
    jev_url="https://api.typesafe.ai/v1/systemone",
    jev_model="jev-1.13.0",
    jev_env_file="~/.config/typesafe/env",
    kev_url="http://100.113.63.58:8008/v1/systemone",
    timeout=1.5,
    threshold=0.70,
    prompt_chars=1500,
    # Claude Code delivers about 10,000 characters of additionalContext and drops the
    # rest (measured 2026-09-24: 9,000 arrived whole, 11,000 lost its tail).
    context_chars=9500,
    exclude_paths=["~/git/council"],
)
# Hard ceiling on the combined hook context: memory's CONTEXT_BYTES and the Codex
# hooks' additionalContextLimit. Past it even a pointer is not injected.
CONTEXT_BYTES_MAX = 12000
NONE = "none"
NONE_TEXT = "No listed skill fits; answer directly."
INSTRUCTIONS = ("Which one skill (a packaged workflow the agent can load) should the agent use for "
                "this owner prompt? Pick none if no skill clearly applies.")
USER_AGENT = "cairn-skill-router/1"
# Turns the harness or another process submits in the owner's place: background-task
# and Monitor notifications, Cairn inbox wakeups, compaction summaries and slash-command
# echoes. Routing them spends a TypeSafe call on text nobody typed and injects skills
# nobody asked for (measured 2026-09-25: 270 of 277 matched turns in one looping
# session were notifications, and both of its injections came from them).
MACHINE_PREFIXES = ("<task-notification>", "<system-reminder>", "<channel source=", "<local-command-", "<command-name>",
                    "<command-message>", "This session is being continued from a previous conversation",
                    "Base directory for this skill:", "# /", "Your claude.ai usage limit has reset",
                    "[Request interrupted by user")
MACHINE_MARKERS = ("<task-notification>", "[SYSTEM NOTIFICATION")
MAX_FILES = 10


def settings(config):
    """Router settings, or None when the installation did not enable it."""
    block = config.get("skill_router")
    if not isinstance(block, dict) or not block.get("enabled", True):
        return None
    if os.environ.get("CAIRN_SKILL_ROUTER") == "0":
        return None
    return dict(DEFAULTS, **block)


def expand(path):
    return Path(os.path.expanduser(str(path)))


def excluded(event, opts):
    """True when the working directory or explicit project is inside an excluded path."""
    places = [Path(event["cwd"]).resolve()]
    if event.get("project_path"):
        places.append(Path(event["project_path"]).resolve())
    for raw in opts.get("exclude_paths") or ():
        root = expand(raw).resolve()
        if any(place == root or root in place.parents for place in places):
            return True
    return False


def parse_frontmatter(text):
    """({key: value}, body) for a `---` fenced header; (None, text) otherwise.

    Top-level scalars only. Indented continuation lines and `>`/`|` block scalars
    fold into their key's value; a key with an empty value (a nested mapping such as
    `metadata:`) keeps an empty string and its indented lines are ignored.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    fields, folding = {}, None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return ({key: value.strip().strip('"').strip("'") for key, value in fields.items()},
                    "\n".join(lines[i + 1:]).strip("\n"))
        if line[:1] in (" ", "\t"):
            if folding and line.strip():
                fields[folding] = (fields[folding] + " " + line.strip()).strip()
            continue
        folding = None
        if ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if value in (">", "|", ">-", "|-", ">+", "|+"):
                fields[key], folding = "", key
            else:
                fields[key], folding = value, (key if value else None)
    return None, text


def catalog(skills_dir, cache_path=None):
    """[{name, description, hidden, dir}] for every skill directory with a SKILL.md."""
    root = expand(skills_dir)
    if not root.is_dir():
        return []
    files = sorted(p for p in root.glob("*/SKILL.md") if not p.parent.name.startswith("."))
    stats = [(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files]
    signature = hashlib.sha256(json.dumps(stats).encode()).hexdigest()
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if cached.get("signature") == signature:
                return cached["skills"]
        except (OSError, ValueError, KeyError):
            pass
    skills = []
    for path in files:
        fields, _ = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if not fields or not fields.get("description"):
            continue
        skills.append(dict(name=fields.get("name") or path.parent.name, description=fields["description"],
                           hidden=fields.get("disable-model-invocation", "").lower() == "true",
                           dir=str(path.parent.resolve())))
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = cache_path.with_name(cache_path.name + ".tmp")
        tmp.write_text(json.dumps({"signature": signature, "skills": skills}))
        tmp.replace(cache_path)
    return skills


def api_key(env_file):
    """TYPESAFE_API_KEY from a shell env file; never logged or returned elsewhere."""
    path = expand(env_file)
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line.startswith("TYPESAFE_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def request_body(event, skills, opts):
    criteria = {skill["name"]: skill["description"] for skill in skills}
    criteria[NONE] = NONE_TEXT
    state = {"prompt": event["prompt"][: int(opts["prompt_chars"])], "cwd": Path(event["cwd"]).name}
    return {"state": state, "questions": {"skill": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": criteria}}}


def post(url, body, headers, timeout):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        answer = json.load(response)["answers"]["skill"]
    return valid_answer(answer)


def valid_answer(answer):
    """The answer in the shape the router uses, or ValueError for any other shape."""
    if not isinstance(answer, dict) or not isinstance(answer.get("choice"), str):
        raise ValueError("answer lacks a string choice")
    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("answer lacks a numeric confidence")
    probabilities = answer.get("probabilities")
    if probabilities is None:
        probabilities = {}
    if not isinstance(probabilities, dict) or not all(
            isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) for k, v in probabilities.items()):
        raise ValueError("answer probabilities are not a name-to-number map")
    return {"choice": answer["choice"], "confidence": float(confidence), "probabilities": dict(probabilities)}


def ask(body, opts):
    """(leg, answer, errors): TypeSafe first, then Kev, then nothing."""
    errors = []
    key = api_key(opts["jev_env_file"])
    if key:
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT,
                   "Authorization": "Bearer " + key}
        try:
            return "jev", post(opts["jev_url"], dict(body, model=opts["jev_model"]), headers, opts["timeout"]), errors
        except Exception as exc:  # any failure falls through to the next leg
            errors.append("jev: " + type(exc).__name__)
    else:
        errors.append("jev: no key")
    if opts.get("kev_url"):
        try:
            return "kev", post(opts["kev_url"], body, {"Content-Type": "application/json", "User-Agent": USER_AGENT},
                               opts["timeout"]), errors
        except Exception as exc:
            errors.append("kev: " + type(exc).__name__)
    return None, None, errors


def skill_text(skill, answer, room):
    """Injected block for one skill: the full SKILL.md when it fits `room` UTF-8 bytes, else a pointer."""
    directory = Path(skill["dir"])
    _, body = parse_frontmatter((directory / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    files = sorted(str(p) for p in directory.rglob("*") if p.is_file() and p.name != "SKILL.md"
                   and not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(directory).parts))
    header = (f"Cairn skill router: the owner's prompt matches the skill \"{skill['name']}\" "
              f"(p={answer['confidence']:.2f}). ")
    full = (header + "Its instructions follow. Apply them if they fit the request; otherwise ignore them.\n"
            f"<skill name=\"{skill['name']}\">\n{body.strip()}\n</skill>\n"
            f"Base directory for this skill: {directory}\n"
            "Relative paths in this skill are relative to that directory.\n"
            + ("Files:\n" + "\n".join(files[:MAX_FILES]) + "\n" if files else ""))
    if len(full.encode()) <= room:
        return full, "inline"
    return (header + f"Read {directory / 'SKILL.md'} before you start, and follow it if it fits the request.\n",
            "pointer")


class Route:
    """One prompt's routing, started before memory recall and merged after it."""

    def __init__(self, config, event, state, opts):
        self.config, self.event, self.opts = config, event, opts
        self.loaded = dict(state.get("skills_loaded") or {})
        self.started = time.monotonic()
        # Both legs time out on their own; this bounds the wait after recall as well.
        self.deadline = self.started + 2 * float(opts["timeout"]) + 0.5
        self.outcome = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            cache = Path(self.config["state_dir"]) / "skill-catalog.json"
            skills = catalog(self.opts["skills_dir"], cache)
            if not skills:
                self.outcome = dict(fired=False, reason="no catalog")
                return
            leg, answer, errors = ask(request_body(self.event, skills, self.opts), self.opts)
            if not answer:
                self.outcome = dict(fired=False, reason="no leg", errors=errors)
                return
            by_name = {skill["name"]: skill for skill in skills}
            winner = by_name.get(answer["choice"])
            reason = ("none" if answer["choice"] == NONE else "unknown choice" if winner is None
                      else "below threshold" if answer["confidence"] < float(self.opts["threshold"])
                      else "visible skill" if not winner["hidden"]
                      else "already loaded" if winner["name"] in self.loaded else None)
            self.outcome = dict(fired=reason is None, reason=reason, leg=leg, errors=errors, answer=answer,
                                skill=winner)
        except Exception as exc:
            self.outcome = dict(fired=False, reason="error: " + type(exc).__name__)

    def merge(self, result, state):
        """The memory result with the chosen skill added; unchanged when nothing fires or anything fails."""
        try:
            return self._merge(result, state)
        except Exception as exc:
            state["last_route"] = dict(at=time.time(), fired=False, reason="error: " + type(exc).__name__)
            log(self.config, dict(at=time.time(), session=self.event.get("session_id"), fired=False,
                                  reason="error: " + type(exc).__name__))
            return result

    def _merge(self, result, state):
        self.thread.join(timeout=max(0.0, self.deadline - time.monotonic()))
        outcome = self.outcome or dict(fired=False, reason="timeout")
        record = dict(at=time.time(), session=self.event["session_id"], harness=self.config.get("harness") or "claude",
                      event=self.event.get("hook_event_name"), fired=outcome["fired"], reason=outcome.get("reason"),
                      leg=outcome.get("leg"), errors=outcome.get("errors") or [],
                      ms=int((time.monotonic() - self.started) * 1000))
        answer = outcome.get("answer")
        if answer:
            top = sorted(answer["probabilities"].items(), key=lambda item: -item[1])[:3]
            record.update(choice=answer["choice"], confidence=round(answer["confidence"], 4),
                          top3=[[name, round(p, 4)] for name, p in top])
        try:
            if outcome["fired"]:
                skill = outcome["skill"]
                memory_text = (result.get("hookSpecificOutput") or {}).get("additionalContext", "")
                opencode = self.config.get("harness") == "opencode"
                budget = self.config.get("context_bytes", CONTEXT_BYTES_MAX)
                if type(budget) is not int or not 1000 <= budget <= CONTEXT_BYTES_MAX:
                    budget = CONTEXT_BYTES_MAX  # the engine already refused an invalid value
                # Bytes throughout: UTF-8 bytes never undercount Claude's character cap, and the
                # total below is checked in bytes, so the room must be too (CAIRN-37).
                room = min(int(self.opts["context_chars"]), budget) - (0 if opencode else len(memory_text.encode()) + 1)
                text, mode = skill_text(skill, answer, room)
                total = len(text.encode()) + (0 if opencode else len(memory_text.encode()) + 1)
                if total > budget:
                    record.update(fired=False, reason="no room")
                    return result
                record["mode"] = mode
                self.loaded[skill["name"]] = time.time()
                state["skills_loaded"] = self.loaded
                if opencode:
                    # The OpenCode plugin parses memory from additionalContext and keeps
                    # the skill as its own retained block.
                    result = dict(result, cairn_skill={"name": skill["name"], "text": text})
                else:
                    combined = text + ("\n" + memory_text if memory_text else "")
                    result = dict(result, hookSpecificOutput={"hookEventName": self.event["hook_event_name"],
                                                              "additionalContext": combined})
            state["last_route"] = {key: record.get(key) for key in ("at", "fired", "reason", "leg", "choice",
                                                                    "confidence", "mode", "ms")}
        finally:
            log(self.config, record)
        return result


def log(config, record):
    """One line per routed prompt; holds no prompt text."""
    try:
        path = Path(config["state_dir"]) / "skill-router.jsonl"
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def machine(prompt):
    """True for text a harness or process submitted in the owner's place."""
    head = prompt.lstrip()[:400]
    return head.startswith(MACHINE_PREFIXES) or (head.startswith("<") and any(m in head for m in MACHINE_MARKERS))


def start(config, event, state):
    """A running Route for this event, or None when the router does not apply."""
    opts = settings(config)
    if opts is None or event.get("hook_event_name") not in ("SessionStart", "UserPromptSubmit"):
        return None
    prompt = event.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or prompt.lstrip().startswith("/") or machine(prompt):
        return None
    if excluded(event, opts):
        return None
    return Route(config, event, state, opts)
