#!/usr/bin/env python3
"""Task 164 fix1: read-only dry run of the take prompts of one vnew project.

Runs inside the app pod (image layout /app/src). It loads the plan and the characters the same way generation does
(plan.read_plan, generate._take_payload -> _char_by_id -> characters.load_char), builds the take prompts for every
take and every part (anime and real) WITHOUT submitting, and prints each look line (vibe prompt) or ASSETS person /
COSTUME sentence (old prompt) plus the first 300 characters of every shot.

Nothing is paid and nothing is written:
- every AWS call that is not Get*/Head*/List*/Describe* raises (boto3 before-call hook), so no S3 write, no Bedrock;
- the LLM (_converse), fal HTTP, Seedream/Seedance submits raise; plan/char saves raise; ledger charges are no-ops;
- the real-run costume edit (task 160, paid fal edit) is replaced by the unchanged front photo, so the prompt has the
  same image labels as a real run without calling fal.
Missing panel-reading caches are NOT filled (the LLM is off); the script reports that.

Usage (in the pod, from /app):
  python /tmp/dry_take_prompt.py <pid> [--owner OWNER] [--variant anime|real|both]
Offline (fixture, no AWS at all):
  python tools/dry_take_prompt.py <pid> --plan-file plan.json [--chars-file chars.json] [--owner o]
  chars.json = {charId: char.json body}.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import sys

SHOT_CHARS = 300


class DryRunBlocked(RuntimeError):
    """A paid call or a write was attempted during the dry run."""


def _add_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.getcwd(), "/app", os.path.join(here, "..", "app")):
        path = os.path.abspath(path)
        if os.path.isdir(os.path.join(path, "src", "vnew")) and path not in sys.path:
            sys.path.insert(0, path)


_add_paths()


class _Capture(logging.Handler):
    """Collects vnew.gen.costume_invalid and other warnings printed after each prompt."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            pass


def install_guards(*, offline: bool = False) -> dict:
    """Block paid calls and writes. Returns {"blocked": [..], "undo": callable}. offline: S3 reads fail at once too."""
    from src.vnew import characters as char_mod
    from src.vnew import generate as gen
    from src.vnew import plan as plan_mod
    from src.vpoc import fal as fal_mod
    from src.vpoc import llm as llm_mod
    from src.vpoc import store as store_mod

    blocked: list[str] = []
    saved: list[tuple[object, str, object]] = []

    def setattr_saved(obj, name: str, value) -> None:
        saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def block(what: str):
        def _blocked(*_a, **_k):
            blocked.append(what)
            raise DryRunBlocked(f"dry run: {what} is blocked")
        return _blocked

    setattr_saved(llm_mod, "_converse", block("llm._converse"))
    setattr_saved(fal_mod, "_request", block("fal._request"))
    setattr_saved(gen, "_seedream", block("generate._seedream"))
    setattr_saved(gen, "_submit_video", block("generate._submit_video"))
    setattr_saved(gen, "_seedream_costume", lambda prompt, front, *, size: bytes(front))  # dry placeholder, no fal call
    for name in ("_charge_costume_ref", "_charge_cast_notes"):
        setattr_saved(gen, name, lambda *a, **k: None)
    setattr_saved(plan_mod, "mutate_plan", block("plan.mutate_plan"))
    setattr_saved(plan_mod, "save_plan", block("plan.save_plan"))
    for name in ("save_char", "mutate_char"):
        if hasattr(char_mod, name):
            setattr_saved(char_mod, name, block("characters." + name))
    store = store_mod.STORE
    for name in ("put_bytes", "write_json"):
        setattr_saved(store, name, block("STORE." + name))
    if offline:
        def offline_read(*_a, **_k):
            raise OSError("dry run offline: no S3")
        for name in ("get_bytes", "read_json", "exists", "list_keys", "list_prefixes"):
            setattr_saved(store, name, offline_read)
    else:
        import boto3

        def before_call(model=None, **_kw):
            name = str(getattr(model, "name", "") or "")
            if not name.startswith(("Get", "Head", "List", "Describe")):
                blocked.append("aws:" + name)
                raise DryRunBlocked(f"dry run: AWS {name} is blocked")

        if boto3.DEFAULT_SESSION is None:
            boto3.setup_default_session()
        boto3.DEFAULT_SESSION.events.register("before-call", before_call)
        store_mod._CLIENTS.clear()  # noqa: SLF001 — clients made after this carry the hook

    def undo() -> None:
        for obj, name, value in reversed(saved):
            setattr(obj, name, value)

    return {"blocked": blocked, "undo": undo}


def find_owner(pid: str) -> str:
    """The owner whose users/<owner>/video/<pid>/vnew/plan.json exists (read-only Head calls)."""
    from src.vnew import plan as plan_mod
    from src.vpoc.store import STORE

    for prefix in STORE.list_prefixes("users/"):
        owner = prefix[len("users/"):].strip("/")
        if owner and not owner.startswith("_") and STORE.exists(plan_mod.plan_key(owner, pid)):
            return owner
    raise SystemExit(f"plan not found for pid={pid}")


def _take_cells(plan: dict) -> list[dict]:
    from src.vnew import generate as gen

    cells = [c for c in gen._previz_cells(plan.get("gen") or {}) if isinstance(c, dict) and c.get("cutIds")]  # noqa: SLF001
    return cells or gen._take_units(plan)  # noqa: SLF001


def _report(prompt: str, out, cast=None) -> None:
    from src.vnew import generate as gen

    lines = prompt.split("\n")
    looks = [line for line in lines if line.rstrip().endswith("(keep exactly this look).")]
    for line in looks:
        out("  LOOK    " + line)
    if any(line.startswith("Shot ") for line in lines):  # vibe prompt: say so when a person has no look line at all
        for member in cast or []:
            name = str(member.get("name") or "").strip()
            handle = str(member.get("nameEn") or member.get("roman") or "").strip() or gen._romanize_name(name) or name  # noqa: SLF001
            if name and not any(line.startswith(handle + ":") for line in looks):
                out(f"  LOOK    {handle}: (no look line: no costume and no appearance)")
    for line in lines:
        if line.startswith("ASSETS"):
            for piece in re.split(r"(?<=\.) (?=The \w+ mannequin is )", line):
                if "mannequin is" in piece:
                    out("  PERSON  " + piece.strip())
    for line in lines:
        if line.startswith("Shot "):
            out("  SHOT    " + line[:SHOT_CHARS])
        elif line.startswith("TIMELINE"):
            body = line.split(": ", 1)[-1]
            for entry in re.split(r" (?=\d+(?:\.\d+)?-\d+(?:\.\d+)?s\b)", body):
                out("  SHOT    " + entry[:SHOT_CHARS])


def run(owner: str, pid: str, plan: dict, *, variants=("anime", "real"), out=print) -> dict:
    """Build every take/part prompt (no submit). Returns {"prompts": [{take, variant, part, prompt}], "warnings": [..]}."""
    from src.vnew import generate as gen
    from src.vnew import settings as vsettings

    plan = copy.deepcopy(plan)
    gen_state = plan.get("gen") or {}
    anime_ep = str(gen_state.get("videoModel") or gen._previz_model_endpoint(plan)  # noqa: SLF001
                   or vsettings.VNEW_PREVIZ_EDIT_MODEL)
    capture = _Capture()
    logger = logging.getLogger("vpoc")
    logger.addHandler(capture)
    result: dict = {"prompts": [], "warnings": []}
    notes = (plan.get("previz") or {}).get("castNotes") or {}
    out(f"# dry run pid={pid} owner={owner} genId={gen_state.get('genId') or '-'} castNotes={len(notes)} "
        f"vibe={bool(vsettings.VNEW_VIBE_PROMPT)} anime={anime_ep}")
    try:
        for take in _take_cells(plan):
            take = {k: v for k, v in take.items() if k not in ("real", "webtoon")}
            for variant in variants:
                real = variant == "real"
                endpoint = gen._real_take_model(gen_state) if real else anime_ep  # noqa: SLF001
                caps = gen._previz_caps(endpoint)  # noqa: SLF001

                def build(sub: dict, continuity):
                    capture.lines.clear()
                    built = gen._take_payload(owner, pid, plan, sub, caps, variant="real" if real else "webtoon",  # noqa: SLF001
                                              continuity=continuity)
                    return built, list(capture.lines)

                def emit(label: str, cut_ids, built: dict, warns: list[str]) -> None:
                    out(f"== take {take.get('id')} [{variant}] {label} cuts={','.join(map(str, cut_ids))} "
                        f"endpoint={endpoint} chars={len(built['prompt'])} images={len(built.get('imageUrls') or [])}")
                    _report(built["prompt"], out, built.get("cast"))
                    shown: dict[str, int] = {}
                    for warn in warns:
                        if "costume_invalid" in warn or "cast_notes_failed" in warn or "location_cast_failed" in warn:
                            shown[warn[:300]] = shown.get(warn[:300], 0) + 1
                    for warn, count in shown.items():
                        out("  WARN    " + warn + (f" (x{count})" if count > 1 else ""))
                    result["prompts"].append({"take": take.get("id"), "variant": variant, "part": label,
                                              "prompt": built["prompt"]})
                    result["warnings"] += warns

                parts = gen._take_parts_for(plan, take, endpoint, real=real, anime=anime_ep)  # noqa: SLF001
                if not parts:
                    built, warns = build(take, None)
                    parts = gen._prompt_split_parts(plan, take, [], built)  # noqa: SLF001
                    if not parts:
                        emit("whole", gen._seg_cut_ids(take), built, warns)  # noqa: SLF001
                        continue
                parts = [dict(p) for p in parts]
                at = 0
                while at < len(parts):
                    part = parts[at]
                    sub = gen._part_take(plan, take, part)  # noqa: SLF001
                    built, warns = build(sub, {"take": take, "frame": None, "frameKey": ""})
                    finer = gen._prompt_split_parts(plan, take, [part], built)  # noqa: SLF001
                    if finer:
                        parts[at:at + 1] = finer
                        continue
                    at += 1
                    emit(f"part {at}/{len(parts)}", part.get("cutIds") or [], built, warns)
    finally:
        logger.removeHandler(capture)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only dry run of vnew take prompts (no paid calls, no writes).")
    parser.add_argument("pid")
    parser.add_argument("--owner", default="")
    parser.add_argument("--variant", choices=("anime", "real", "both"), default="both")
    parser.add_argument("--plan-file", default="", help="offline: plan JSON instead of S3")
    parser.add_argument("--chars-file", default="", help="offline: {charId: char} JSON")
    args = parser.parse_args(argv)
    offline = bool(args.plan_file)
    guards = install_guards(offline=offline)
    from src.vnew import characters as char_mod

    orig_load = char_mod.load_char
    try:
        from src.vnew import plan as plan_mod

        if offline:
            with open(args.plan_file, encoding="utf-8") as fh:
                plan = json.load(fh)
            chars = {}
            if args.chars_file:
                with open(args.chars_file, encoding="utf-8") as fh:
                    chars = json.load(fh)

            def load_char(_owner, char_id):
                if str(char_id) not in chars:
                    raise KeyError(char_id)
                return copy.deepcopy(chars[str(char_id)]), "dry"

            char_mod.load_char = load_char
            owner = args.owner or "offline"
        else:
            owner = args.owner or find_owner(args.pid)
            plan, _ = plan_mod.load_plan(owner, args.pid)
        variants = ("anime", "real") if args.variant == "both" else (args.variant,)
        run(owner, args.pid, plan, variants=variants)
    finally:
        blocked = guards["blocked"]
        print(f"# blocked calls: {len(blocked)}" + (f" ({', '.join(sorted(set(blocked)))})" if blocked else ""))
        guards["undo"]()
        char_mod.load_char = orig_load
    return 0


if __name__ == "__main__":
    sys.exit(main())
