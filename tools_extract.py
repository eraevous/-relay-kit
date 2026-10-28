"""Write each skill's embedded code blocks to extracted/<skill>/NN_<heading>.<ext>. Fences count only at line start.
With --zips, also rebuild upload/<skill>.zip (one per skill, for uploading to claude.ai)."""
import re, pathlib, shutil, sys, zipfile
root = pathlib.Path(__file__).resolve().parent
ext = {"javascript": "js", "js": "js", "python": "py", "py": "py", "bash": "sh", "sh": "sh"}
out_root = root / "extracted"
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
            if m.group(1) in ext:
                n += 1
                name = re.sub(r"[^\w]+", "_", re.sub(r"\.(js|py)\b", "", head)).strip("_")
                out = out_root / sk.name / f"{n:02d}_{name}.{ext[m.group(1)]}"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("".join(lines[i + 1:j]), encoding="utf-8")
            i = j
        i += 1
    for s in (sk / "scripts").glob("*") if (sk / "scripts").is_dir() else []:
        if s.suffix in (".py", ".js"):
            (out_root / sk.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, out_root / sk.name / s.name)
for f in sorted(out_root.rglob("*")):
    if f.is_file(): print(f.relative_to(root))

if "--zips" in sys.argv:
    up = root / "upload"
    shutil.rmtree(up, ignore_errors=True); up.mkdir()
    for sk in sorted((root / "skills").iterdir()):
        with zipfile.ZipFile(up / f"{sk.name}.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(sk.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, f.relative_to(root / "skills"))
    print(f"rebuilt {len(list(up.glob('*.zip')))} upload zips")
