"""
Tes keamanan & rumus bisnis TANPA LLM (tidak butuh API key).

Jalankan:  python test_guard.py
"""
from database import init_db, run_query
from guard import InvalidSQLError, SecurityError, validate_and_secure

BAMBANG = {"username": "sales_bambang", "role": "Sales", "agent_id": 101}
HENDRA = {"username": "finance_hendra", "role": "Finance", "agent_id": None}


def run_as(user, sql):
    return run_query(validate_and_secure(sql, user))[1]


def must_block(user, sql, error=SecurityError):
    try:
        validate_and_secure(sql, user)
    except error:
        return
    raise AssertionError(f"Seharusnya diblokir: {sql}")


# --- Keamanan ---------------------------------------------------------------
def test_sql_injection_piggyback():
    must_block(HENDRA, "SELECT CustomerName FROM Sales_Contracts; DROP TABLE Units; --")


def test_mutasi_ditolak():
    for sql in ["DROP TABLE Units", "DELETE FROM Units", "UPDATE Units SET Status = 'Sold'",
                "INSERT INTO Units (UnitID) VALUES (99)"]:
        must_block(HENDRA, sql)


def test_system_users_tidak_boleh_dibaca():
    must_block(HENDRA, "SELECT * FROM System_Users")
    must_block(HENDRA, "SELECT name FROM sqlite_master")
    must_block(HENDRA, "SELECT * FROM main.Units")


def test_sales_tidak_boleh_baca_kas_masuk():
    must_block(BAMBANG, "SELECT * FROM Payments_Received")


def test_cte_tidak_boleh_menyamar_jadi_tabel():
    must_block(BAMBANG, "WITH Sales_Contracts AS (SELECT 1 AS SalesAgentID) SELECT * FROM Units")


def test_sql_rusak_bisa_diretry():
    must_block(HENDRA, "SELEC * FORM Units WHERE", error=InvalidSQLError)


# --- RBAC -------------------------------------------------------------------
def test_sales_hanya_lihat_kontrak_sendiri():
    rows = run_as(BAMBANG, "SELECT DISTINCT SalesAgentID FROM Sales_Contracts")
    assert rows == [(101,)], rows


def test_sales_tidak_bisa_lihat_customer_sales_lain():
    rows = run_as(BAMBANG, "SELECT * FROM Sales_Contracts sc WHERE sc.CustomerName = 'Siti Rahma'")
    assert rows == [], rows
    rows = run_as(BAMBANG, """
        SELECT ps.* FROM Payment_Schedules ps
        JOIN Sales_Contracts c ON c.ContractID = ps.ContractID
        WHERE c.CustomerName = 'Siti Rahma'""")
    assert rows == [], rows


def test_finance_lihat_semua():
    rows = run_as(HENDRA, "SELECT COUNT(*) FROM Sales_Contracts")
    assert rows == [(6,)], rows


# --- Jawaban sampel soal (untuk memastikan rumus & data benar) --------------
def test_sampel_1_unit_vacant():
    rows = run_as(HENDRA, "SELECT UnitNumber, Floor FROM Units WHERE Status='Vacant' AND Tower='Tower A' AND UnitType='2BR'")
    assert rows == [("A-0502", 5)], rows


def test_sampel_3_outstanding_budi():
    rows = run_as(HENDRA, """
        SELECT
          (SELECT SUM(BillingAmount) FROM Payment_Schedules WHERE ContractID = c.ContractID)
        - (SELECT COALESCE(SUM(PaidAmount), 0) FROM Payments_Received WHERE ContractID = c.ContractID)
        FROM Sales_Contracts c WHERE c.CustomerName = 'Budi Santoso'""")
    assert rows == [(65_000_000,)], rows


def test_sampel_4_denda_installment():
    rows = run_as(HENDRA, """
        SELECT SUM(p.PenaltyAmount - p.WaiverAmount - p.PaidPenalty)
        FROM Late_Fee_Penalties p JOIN Sales_Contracts c ON c.ContractID = p.ContractID
        WHERE c.PaymentScheme = 'Installment 24x'""")
    assert rows == [(4_200_000,)], rows


if __name__ == "__main__":
    init_db()
    tests = [(name, fn) for name, fn in globals().items() if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  OK    {name}")
        except Exception as e:
            failed += 1
            print(f"  GAGAL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tes lolos")
