"""relay-kit: one command over the kit's tools. Standard library only.

    relay-kit init <folder> --owner <name> [--lanes relay-kit=RK,research=RS] [--kit-ref v0.1.0]
    relay-kit status [--root .] [...]                 tools/status.py
    relay-kit handoff [--dir handoffs] new|set|list|check [...]   the handoff-ledger skill's script
    relay-kit ledger check [--dir governance] [...]   tools/check_ledgers.py
    relay-kit ledger append [--dir governance] decision|challenge '<json>'   tools/ledger_append.py
    relay-kit new-id --prefix C --lane AEC [...]      tools/new_id.py
    relay-kit zips [--out DIR]                        skill zips for uploading (and extracted copies)
    relay-kit --version

Each subcommand runs the same script the checkout runs (python3 tools/status.py and so on),
with the same arguments, output and exit code. Nothing else changes.
"""
import datetime as dt, json, pathlib, re, runpy, shutil, sys

from . import __version__

HERE = pathlib.Path(__file__).resolve().parent
SKILLS = HERE.parent / "skills" if (HERE.parent / "skills" / "handoff-ledger").is_dir() else HERE / "skills"
SCRIPTS = {
    "status": HERE / "status.py",
    "handoff": SKILLS / "handoff-ledger" / "scripts" / "handoff.py",
    "ledger check": HERE / "check_ledgers.py",
    "ledger append": HERE / "ledger_append.py",
    "new-id": HERE / "new_id.py",
    "zips": HERE / "extract.py",
}
KIT_REPO = "eraevous/-relay-kit"


def run_script(path, args):
    """Run a tool exactly as `python3 <path> <args>` would: same argv, output and exit code."""
    sys.argv = [str(path)] + list(args)
    runpy.run_path(str(path), run_name="__main__")


def init(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="relay-kit init", description="Create a records repository folder from the template.")
    ap.add_argument("folder", type=pathlib.Path)
    ap.add_argument("--owner", required=True, help="the person whose words authorize work (one word)")
    ap.add_argument("--lanes", default="", help="lane codes for handoff ids, e.g. relay-kit=RK,research=RS")
    ap.add_argument("--kit-ref", default=f"v{__version__}", help="relay-kit tag or commit the CI installs (default: this version's tag)")
    ap.add_argument("--kit-repo", default=KIT_REPO, help="GitHub owner/name the CI installs relay-kit from")
    a = ap.parse_args(argv)
    owner = a.owner.strip().lower()
    if not re.fullmatch(r"[\w.:-]+", owner):
        sys.exit("refused: --owner must be one word (letters, digits, . : - _)")
    lanes = {}
    for pair in filter(None, (x.strip() for x in a.lanes.split(","))):
        name, _, code = pair.partition("=")
        if not name or not re.fullmatch(r"[A-Z0-9]{1,12}", code.strip().upper()):
            sys.exit(f"refused: --lanes entry {pair!r} isn't name=CODE (CODE: 1-12 letters or digits)")
        lanes[name.strip().lower()] = code.strip().upper()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.kit_repo) or not re.fullmatch(r"[\w./-]+", a.kit_ref):
        sys.exit("refused: --kit-repo must be owner/name and --kit-ref a tag, branch or commit")
    out = a.folder.resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        sys.exit(f"refused: {out} exists and isn't an empty folder; init only fills a new or empty one")
    template = HERE / "templates" / "records"
    now = dt.datetime.now(dt.timezone.utc)
    fill = {"{{OWNER}}": owner, "{{VERSION}}": __version__, "{{KIT_REF}}": a.kit_ref,
            "{{KIT_REPO}}": a.kit_repo, "{{DATE}}": now.strftime("%Y-%m-%d")}
    written = []
    for src in sorted(p for p in template.rglob("*") if p.is_file()):
        rel = pathlib.Path(*[("." + part[4:]) if part.startswith("dot-") else part
                              for part in src.relative_to(template).parts])
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        for k, v in fill.items():
            text = text.replace(k, v)
        dst.write_text(text, encoding="utf-8", newline="\n")
        if src.stat().st_mode & 0o111 or rel.parts[0] == ".githooks":   # wheels may drop the exec bit
            dst.chmod(dst.stat().st_mode | 0o755)
        written.append(rel.as_posix())
    (out / "handoffs" / "outputs").mkdir(parents=True, exist_ok=True)
    cfg = {"owner": owner, "lanes": lanes,
           "contract_since": {"relay-kit/handoff@2": now.strftime("%Y-%m-%dT%H:%MZ")}}
    (out / "handoffs" / ".ledger.json").write_text(json.dumps(cfg, indent=1) + "\n", encoding="utf-8")
    written.append("handoffs/.ledger.json")
    print(f"Created {out} (relay-kit {__version__}):")
    for w in written:
        print(f"  {w}")
    print(f"""
Next (init doesn't run git or create anything outside {out}):
  cd {out}
  git init -b main && git config core.hooksPath .githooks
  git add . && git commit -m "Records, from relay-kit init"
  # create a PRIVATE repository on GitHub, then: git remote add origin <url> && git push -u origin main
  export RELAYKIT_ACTOR=<assistant>:<lane>#<session id>
  relay-kit status --root .""")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    if argv[0] == "--version":
        print(f"relay-kit {__version__}")
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "init":
        return init(rest)
    if cmd == "ledger":
        if not rest or rest[0] not in ("check", "append"):
            sys.exit("usage: relay-kit ledger check|append [...]")
        cmd, rest = f"ledger {rest[0]}", rest[1:]
    if cmd not in SCRIPTS:
        sys.exit(f"relay-kit: unknown command {cmd!r}; try relay-kit --help")
    if cmd == "zips":
        rest = ["--zips"] + rest
    run_script(SCRIPTS[cmd], rest)


if __name__ == "__main__":
    main()
