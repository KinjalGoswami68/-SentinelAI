"""
Fail if a tracked text file is not UTF-8 without a BOM.

Windows PowerShell 5.1 writes UTF-16 LE (`>`, Out-File) or ANSI (Set-Content) by
default; README.md was UTF-16 from June to November 2026 and GitHub showed it as
binary. Runs in verify-deploy; locally: python scripts/check_encoding.py
"""
import subprocess
import sys

TEXT = (".py", ".md", ".txt", ".yml", ".yaml", ".json", ".jsonl", ".toml", ".cfg", ".ini",
        ".gitignore", ".gitattributes", ".editorconfig", ".python-version", "Procfile")


def main():
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    bad = []
    for f in files:
        if not f.endswith(TEXT):
            continue
        b = open(f, "rb").read()
        if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
            bad.append(f"{f}: UTF-16")
        elif b[:3] == b"\xef\xbb\xbf":
            bad.append(f"{f}: UTF-8 with BOM")
        else:
            try:
                b.decode("utf-8")
            except UnicodeDecodeError as e:
                bad.append(f"{f}: not UTF-8 (byte {e.start})")
    for line in bad:
        print(line)
    print(f"{len(bad)} file(s) not UTF-8 without BOM" if bad else "all tracked text files are UTF-8 without BOM")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
