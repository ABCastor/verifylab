"""Run the Lean planted defects through plain practice (no vl), for comparison: `python3 PATH WORKDIR`.

Arm A "grep":    lake build + rg '\b(sorry|admit|axiom)\b' over the sources.
Arm B "careful": arm A + `#print axioms` on the claimed theorem, accepting only the 3 standard axioms.
Plain practice has no trusted target: the claim is checked against the candidate's own copy of it.
"""
import re, shutil, subprocess, sys, tomllib
from pathlib import Path

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "lean-planted"
WORK = Path(sys.argv[1])
STD = {"propext", "Quot.sound", "Classical.choice"}
rows = []
for case in sorted((FIX / "cases").iterdir()):
    meta = tomllib.loads((case / "case.toml").read_text())
    lean = meta.get("lean", {})
    d = WORK / case.name
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(FIX, d, ignore=shutil.ignore_patterns("cases", ".lake"))
    if (case / "overlay").is_dir():
        shutil.copytree(case / "overlay", d, dirs_exist_ok=True)
    target = lean.get("target", "")
    theorems = lean.get("theorems", [])
    if not target or not (d / target).is_file() or ("proofs" in lean) == ("solution" in lean):
        rows.append((case.name, meta.get("expect"), "n/a (malformed claim)", "n/a", "n/a")); continue
    text = (d / target).read_text()
    if "proofs" in lean:
        imports = "".join(f"import {m}\n" for m in lean.get("imports", []))
        body = text
        for name, proof in lean["proofs"].items():
            short = name.split(".")[-1]
            body = re.sub(rf"(theorem {short}\b[^\n]*(?:\n[ \t][^\n]*)*?:=\s*)sorry", lambda m: m.group(1) + f"({proof})", body, count=1)
        claim = imports + body
    else:
        claim = f"import {lean['solution']}\n"
    claim += "".join(f"\n#print axioms {t}\n" for t in theorems)
    (d / "Fixture" / "Claim.lean").write_text(claim)
    deps = re.findall(r"^import (\S+)", claim, re.M)
    pre = subprocess.run(["lake", "build", *deps], cwd=d, capture_output=True, text=True)
    build = subprocess.run(["lake", "env", "lean", "Fixture/Claim.lean"], cwd=d, capture_output=True, text=True)
    out = build.stdout + build.stderr
    grep = subprocess.run(["rg", "-n", r"\b(sorry|admit|axiom)\b", "Fixture", "-g", "*.lean", "-g", "!Claim.lean"], cwd=d, capture_output=True, text=True)
    ok_build = build.returncode == 0 and pre.returncode == 0 and "declaration uses 'sorry'" not in out
    grep_hit = bool(grep.stdout.strip())
    axioms = set(re.findall(r"[A-Za-z_][\w.]*", " ".join(re.findall(r"depends on axioms: \[([^\]]*)\]", out))))
    arm_a = "ACCEPTS" if ok_build and not grep_hit else "rejects"
    arm_b = "ACCEPTS" if arm_a == "ACCEPTS" and axioms <= STD else "rejects"
    rows.append((case.name, meta.get("expect"), arm_a, arm_b, ",".join(sorted(axioms - STD)) or "-"))
w = max(len(r[0]) for r in rows)
print(f"{'case':{w}}  vl-expects  plain-grep  plain-careful  extra-axioms")
for r in rows:
    print(f"{r[0]:{w}}  {r[1]:10}  {r[2]:10}  {r[3]:13}  {r[4]}")
