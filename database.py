"""
LANGKAH 1 - DATABASE

File ini hanya mengurus database SQLite:
- membuat database dari schema.sql
- menjalankan query dengan koneksi READ-ONLY
- mengambil struktur tabel (untuk dikirim ke LLM)
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "southcity.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def init_db():
    """Buat ulang database dari schema.sql. Dipanggil sekali saat aplikasi start."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()


def run_query(sql, limit=200):
    """
    Jalankan query dan kembalikan (nama_kolom, baris).

    Koneksi dibuka dengan mode=ro (read-only). Jadi walaupun ada query
    berbahaya yang lolos, SQLite sendiri akan menolak perubahan data.
    Ini lapisan pertahanan terakhir.
    """
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        cursor = conn.execute(sql)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchmany(limit)
        return columns, rows
    finally:
        conn.close()


def describe_tables(tables):
    """
    Ambil perintah CREATE TABLE asli dari database untuk tabel-tabel tertentu.

    Skema diambil langsung dari database (bukan ditulis manual di prompt),
    jadi kalau tabel berubah, prompt ke LLM ikut berubah otomatis.
    """
    conn = sqlite3.connect(DB_PATH)
    parts = []
    for table in tables:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if row:
            parts.append(row[0])
    conn.close()
    return "\n\n".join(parts)


def get_users():
    """Daftar user aktif untuk dropdown login di aplikasi."""
    columns, rows = run_query(
        "SELECT Username, FullName, Role, AssignedAgentID FROM System_Users WHERE IsActive = 1"
    )
    return [
        {"username": r[0], "fullname": r[1], "role": r[2], "agent_id": r[3]}
        for r in rows
    ]
