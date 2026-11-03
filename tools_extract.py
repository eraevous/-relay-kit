"""Write each skill's embedded code blocks to <out>/<skill>/NN_<heading>.<ext> (default out: extracted/,
which git ignores: it's generated, never edited). Fences count only at line start. Scripts under a skill's
scripts/ folder are copied alongside. With --zips, also rebuild upload/<skill>.zip (one per skill, for
uploading to claude.ai). Only the output folders are written; skill sources are never touched.

    python3 tools_extract.py [--out DIR] [--zips]
"""
import re, pathlib, shutil, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parent
EXT = {"javascript": "js", "js": "js", "python": "py", "py": "py", "bash": "sh", "sh": "sh"}


def extract(out_root, root=ROOT):
    out_root = pathlib.Path(out_root)
    shutil.rmtree(out_root, ignore_errors=True)
    for sk in sorted((root / "skills").iterdir()):
        lines = (sk / "SKILL.md").read_text(encoding="utf-8").splitlines(keepends=True)
        i = n = 0; head = "block"
        while i < len(lines):
            l = lines[i]
            if l.startswith("#"): head = l.lstrip("#").strip()
            m = re.match(r"^```(\w+)\s*$", l)
            if m:
                j = i + 1
                while j < len(lines) and not re.match(r"^```\s*$", lines[j]): j += 1
                if m.group(1) in EXT:
                    n += 1
                    name = re.sub(r"[^\w]+", "_", re.sub(r"\.(js|py)\b", "", head)).strip("_")
                    out = out_root / sk.name / f"{n:02d}_{name}.{EXT[m.group(1)]}"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text("".join(lines[i + 1:j]), encoding="utf-8")
                i = j
            i += 1
        for s in (sk / "scripts").glob("*") if (sk / "scripts").is_dir() else []:
            if s.suffix in (".py", ".js"):
                (out_root / sk.name).mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, out_root / sk.name / s.name)
    return sorted(f for f in out_root.rglob("*") if f.is_file())


def build_zips(up, root=ROOT):
    up = pathlib.Path(up)
    shutil.rmtree(up, ignore_errors=True); up.mkdir(parents=True)
    for sk in sorted((root / "skills").iterdir()):
        with zipfile.ZipFile(up / f"{sk.name}.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(sk.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, f.relative_to(root / "skills"))
    return sorted(up.glob("*.zip"))


def main(argv):
    out = pathlib.Path(argv[argv.index("--out") + 1]) if "--out" in argv else ROOT / "extracted"
    for f in extract(out):
        print(f.relative_to(out.parent) if out.is_relative_to(ROOT) else f)
    if "--zips" in argv:
        print(f"rebuilt {len(build_zips(ROOT / 'upload'))} upload zips")


if __name__ == "__main__":
    main(sys.argv[1:])
