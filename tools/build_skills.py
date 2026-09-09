"""
Build standalone .skill packages from this plugin's skills.

WHY THIS EXISTS
---------------
The skills share one FCP7 XML writer in `lib/`, which keeps a single
implementation and stops the copies drifting apart. But sharing means a skill
cannot run on its own: lifted out of the plugin, the import fails.

That matters because uploading a `.skill` file into Claude is far easier for a
non-technical user than installing a plugin. So the plugin stays the source of
truth for development, and this script produces self-contained `.skill` files for
delivery: it stages a copy of the skill, drops `lib/fcp7xml.py` in beside the
scripts, and packages that.

No source rewriting is involved. The skills look for the shared module both
alongside themselves and at the plugin's `lib/`, so the same file works in either
layout, and there is no generated variant of the code to keep in step.

Usage:
    python tools/build_skills.py                 # build every skill
    python tools/build_skills.py rough-cut       # build one
    python tools/build_skills.py --out DIR       # where the .skill files land
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LIB = PLUGIN_ROOT / "lib"
SKILLS = PLUGIN_ROOT / "skills"

# The packager ships with the skill-creator skill rather than this plugin, so it
# is located at run time instead of vendored.
PACKAGER_GLOBS = [
    Path(os.environ.get("APPDATA", "")) / "Claude" / "local-agent-mode-sessions" / "skills-plugin",
    Path.home() / ".claude" / "plugins",
]


def find_packager():
    """Locate skill-creator's package_skill.py, which turns a directory into a
    .skill archive."""
    for base in PACKAGER_GLOBS:
        if not base.exists():
            continue
        for hit in base.rglob("skills/skill-creator/scripts/package_skill.py"):
            return hit.resolve()
    return None


def stage_skill(name, staging):
    src = SKILLS / name
    if not src.is_dir():
        raise SystemExit("No such skill: %s (looked in %s)" % (name, SKILLS))

    dest = staging / name
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # Inline the shared modules next to the scripts that import them.
    scripts = dest / "scripts"
    inlined = []
    if scripts.is_dir():
        for module in sorted(LIB.glob("*.py")):
            shutil.copy2(module, scripts / module.name)
            inlined.append(module.name)
    return dest, inlined


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("skills", nargs="*", help="Skill names; default is all of them.")
    ap.add_argument("--out", default=str(PLUGIN_ROOT / "dist"))
    args = ap.parse_args()

    names = args.skills or sorted(p.name for p in SKILLS.iterdir() if p.is_dir())
    packager = find_packager()
    if not packager:
        raise SystemExit(
            "Could not find skill-creator's package_skill.py. Install/enable the "
            "skill-creator skill, or package the staged directories by hand.")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    built = []
    for name in names:
        with tempfile.TemporaryDirectory() as tmp:
            staged, inlined = stage_skill(name, Path(tmp))
            print("staging %s (inlined: %s)" % (name, ", ".join(inlined) or "nothing"))
            result = subprocess.run(
                [sys.executable, "-m", "scripts.package_skill", str(staged)],
                cwd=str(packager.parents[1]), env=env,
                capture_output=True, text=True,
                # The packager prints emoji; the console default codec on Windows
                # cannot decode them and raises while reading the pipe.
                encoding="utf-8", errors="replace")
            if result.returncode != 0:
                print(result.stdout)
                print(result.stderr, file=sys.stderr)
                raise SystemExit("Packaging failed for %s" % name)

            produced = packager.parents[1] / ("%s.skill" % name)
            if not produced.is_file():
                raise SystemExit("Packager reported success but %s is missing" % produced)
            shutil.move(str(produced), str(out_dir / produced.name))
            built.append(out_dir / produced.name)

    print()
    for b in built:
        print("built %s (%.1f KB)" % (b, b.stat().st_size / 1024))
    print("\nThese are self-contained: upload one into Claude and it runs without "
          "the plugin installed.")


if __name__ == "__main__":
    main()
