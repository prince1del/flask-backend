from pathlib import Path

p1 = (
    Path("app/routes/data.py").resolve().parent.parent.parent / "centralized_db.sqlite3"
)
p2 = Path("centralized_db.sqlite3").resolve()
print("endpoint_path:", p1)
print("relative_path:", p2)
print("same?", p1 == p2)
