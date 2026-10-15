"""Exercise the .bat wrappers' logic without cmd.exe.

The wrappers are Windows-only, so this checks the parts that can go
wrong on any platform: every python command they invoke must exist in
main.py's parser, every referenced .bat must exist, and no stray
parentheses may sit inside an if (...) block where cmd would split.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from main import build_parser  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
actions = build_parser()._actions
# Look the subcommand up by name rather than by index, so inserting an
# argument later cannot silently break this check.
choices = next(a for a in actions if a.dest == "command").choices
doctor_flags = {"--all-ports", "--port", "--tcp", "--timeout", "--only-config"}

problems = []
bats = sorted(ROOT.glob("*.bat"))

for p in bats:
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()

    for i, line in enumerate(lines, 1):
        # An unescaped bracket in an echoed line inside an if (...) block
        # makes cmd split the block at the wrong place, so the message
        # after it silently never prints.
        if line.strip().startswith("echo") and ("(" in line or ")" in line):
            problems.append(f"{p.name}:{i} echo contains a bracket: {line.strip()}")

    for m in re.finditer(r'^\s*"\.venv\\Scripts\\python\.exe"\s+(.+?)\s*$',
                         text, re.MULTILINE):
        # An invocation reads `"python.exe" main.py <command> [flags]`, so
        # the entry script and the subcommand are separate tokens.
        parts = m.group(1).split()
        script = parts[0]
        cmd = parts[1] if len(parts) > 1 else ""
        flag = parts[2] if len(parts) > 2 else ""
        if script == "main.py":
            if cmd not in choices:
                problems.append(f"{p.name}: 'main.py {cmd}' is not a real command")
            if flag.startswith("--") and flag not in doctor_flags:
                problems.append(f"{p.name}: 'main.py {cmd} {flag}' unknown flag")
        elif script.startswith("tools\\"):
            target = ROOT / script.replace("\\", "/")
            if not target.is_file():
                problems.append(f"{p.name}: missing script {script}")
        else:
            problems.append(f"{p.name}: unexpected entry script {script}")

    # Every .bat referenced from another .bat must exist.
    for ref in re.findall(r"([0-9]_[A-Z_]+\.bat)", text):
        if not (ROOT / ref).is_file():
            problems.append(f"{p.name}: references missing file {ref}")

    # Must end windows open so results are readable.
    if "pause" not in text:
        problems.append(f"{p.name}: has no pause, window will close on exit")

    if "cd /d \"%~dp0\"" not in text:
        problems.append(f"{p.name}: missing cd /d \"%~dp0\"")

if not bats:
    raise SystemExit("no .bat files found next to main.py -- refusing to pass")
if len(bats) < 5:
    raise SystemExit(f"expected at least 5 shortcuts, found {len(bats)}")

print(f"checked {len(bats)} .bat files")
for b in bats:
    print(f"  {b.name}")

if problems:
    print("\nPROBLEMS:")
    for x in problems:
        print(f"  - {x}")
    raise SystemExit(1)
print("\nall wrapper checks passed")
