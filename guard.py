"""
LANGKAH 2 - SATPAM QUERY (Guard)

Semua SQL buatan LLM WAJIB lewat file ini sebelum menyentuh database.

Kenapa pakai AST, bukan regex?
  Regex cuma melihat teks. Contoh: `WHERE CustomerName = 'DROP'` akan dianggap
  berbahaya, sedangkan `DrOp/**/TABLE` bisa lolos. sqlglot mengubah SQL menjadi
  pohon (AST = Abstract Syntax Tree), jadi kita tahu PERSIS jenis perintahnya
  (SELECT/DROP/...) dan tabel apa saja yang dipakai.

Yang dicek di sini:
  1. Hanya boleh 1 statement           -> mencegah "SELECT ...; DROP TABLE ..."
  2. Harus SELECT                      -> tidak boleh INSERT/UPDATE/DELETE/DROP/...
  3. Tabel harus sesuai hak akses role -> mis. System_Users tidak boleh dibaca siapa pun
  4. RBAC: query Sales ditulis ulang supaya hanya melihat kontrak miliknya sendiri
"""
import sqlglot
from sqlglot import exp

REAL_TABLES = {
    "units", "sales_contracts", "payment_schedules",
    "payments_received", "late_fee_penalties", "system_users",
    "sqlite_master", "sqlite_schema",
}

# Tabel yang boleh dibaca tiap role. Role yang tidak ada di sini = tidak boleh apa-apa.
# System_Users sengaja tidak diberikan ke siapa pun.
ROLE_TABLES = {
    "Sales": {"Units", "Sales_Contracts", "Payment_Schedules", "Late_Fee_Penalties"},
    "Finance": {"Units", "Sales_Contracts", "Payment_Schedules", "Payments_Received", "Late_Fee_Penalties"},
    "Auditor": {"Units", "Sales_Contracts", "Payment_Schedules", "Payments_Received", "Late_Fee_Penalties"},
}

# Untuk role Sales, tabel-tabel ini diganti dengan versi yang sudah difilter.
# Contoh: "FROM Sales_Contracts" menjadi
#         "FROM (SELECT * FROM Sales_Contracts WHERE SalesAgentID = 101) AS Sales_Contracts"
SALES_FILTERS = {
    "sales_contracts":
        "SELECT * FROM Sales_Contracts WHERE SalesAgentID = {agent_id}",
    "payment_schedules":
        "SELECT * FROM Payment_Schedules WHERE ContractID IN "
        "(SELECT ContractID FROM Sales_Contracts WHERE SalesAgentID = {agent_id})",
    "late_fee_penalties":
        "SELECT * FROM Late_Fee_Penalties WHERE ContractID IN "
        "(SELECT ContractID FROM Sales_Contracts WHERE SalesAgentID = {agent_id})",
}

# Jenis node AST yang tidak boleh muncul di mana pun di dalam query.
# (pakai getattr karena nama class bisa sedikit beda antar versi sqlglot)
FORBIDDEN_NODES = tuple(
    getattr(exp, name)
    for name in ["Insert", "Update", "Delete", "Drop", "Create", "Alter", "AlterTable",
                 "TruncateTable", "Merge", "Command", "Pragma", "Attach", "Detach"]
    if hasattr(exp, name)
)


class SecurityError(Exception):
    """Query ditolak karena keamanan/hak akses. JANGAN di-retry ke LLM."""


class InvalidSQLError(Exception):
    """SQL tidak bisa dibaca (salah sintaks). Boleh dikirim balik ke LLM untuk diperbaiki."""


def validate_and_secure(sql, user):
    """
    Cek SQL, lalu kembalikan SQL final yang aman untuk dijalankan.
    Melempar SecurityError atau InvalidSQLError kalau gagal.
    """
    # 1. Parse SQL menjadi AST
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise InvalidSQLError(f"SQL tidak valid: {e}")

    if len(statements) == 0:
        raise InvalidSQLError("SQL kosong.")
    if len(statements) > 1:
        raise SecurityError(
            f"Terdeteksi {len(statements)} perintah SQL sekaligus. Hanya 1 query yang diizinkan "
            "(indikasi SQL injection)."
        )
    tree = statements[0]

    # 2. Harus SELECT (termasuk UNION / INTERSECT / EXCEPT), dan tidak ada perintah mutasi di dalamnya
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SecurityError(f"Hanya query SELECT yang diizinkan, bukan {tree.key.upper()}.")
    bad = tree.find(*FORBIDDEN_NODES)
    if bad is not None:
        raise SecurityError(f"Perintah {bad.key.upper()} tidak diizinkan (database read-only).")

    # 3. Cek tabel yang dipakai sesuai hak akses role
    role = user["role"]
    allowed = {t.lower() for t in ROLE_TABLES.get(role, set())}

    cte_names = set()
    for cte in tree.find_all(exp.CTE):
        name = cte.alias.lower()
        if name in REAL_TABLES:
            raise SecurityError(f"Nama CTE '{cte.alias}' tidak boleh sama dengan nama tabel asli.")
        cte_names.add(name)

    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise SecurityError(f"Nama tabel dengan prefix schema ('{table.sql()}') tidak diizinkan.")
        if name in cte_names:
            continue
        if name not in allowed:
            raise SecurityError(f"Role {role} tidak punya akses ke tabel '{table.name or table.sql()}'.")

    # 4. RBAC: tulis ulang query Sales supaya hanya melihat datanya sendiri
    if role == "Sales":
        if user.get("agent_id") is None:
            raise SecurityError("Akun Sales ini tidak terhubung ke AgentID mana pun.")
        tree = apply_sales_filter(tree, int(user["agent_id"]))

    return tree.sql(dialect="sqlite")


def apply_sales_filter(tree, agent_id):
    """Ganti setiap tabel sensitif dengan subquery yang sudah difilter berdasarkan AgentID."""

    def replace(node):
        if isinstance(node, exp.Table) and node.name.lower() in SALES_FILTERS:
            filtered = sqlglot.parse_one(
                SALES_FILTERS[node.name.lower()].format(agent_id=agent_id), read="sqlite"
            )
            # Alias dipertahankan, jadi "sc.CustomerName" atau "Sales_Contracts.CustomerName" tetap jalan
            return exp.Subquery(
                this=filtered,
                alias=exp.TableAlias(this=exp.to_identifier(node.alias_or_name)),
            )
        return node

    return tree.transform(replace)
