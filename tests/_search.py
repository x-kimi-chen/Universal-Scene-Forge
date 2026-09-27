import io

for path in [r"app\core\mesh.py", r"app\core\exporter.py", r"app\core\ingest.py"]:
    print(f"===== {path} =====")
    lines = io.open(path, encoding="utf-8").read().splitlines()
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("import ") or s.startswith("from ") or "import" in s.split("#")[0][:40]:
            if "app." in s or s.startswith(("import", "from")):
                print(f"{i + 1}: {s[:130]}")
