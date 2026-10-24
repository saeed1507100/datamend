"""Load the payment provider's daily export into raw_payments."""


def run(con, now, mode):
    con.execute(
        "CREATE TABLE IF NOT EXISTS raw_payments "
        "(id INTEGER, amount DECIMAL(10, 2), _loaded_at TIMESTAMP)"
    )
    # The export always holds the full day: payment ids 0..199.
    con.execute(
        "INSERT INTO raw_payments SELECT range, 10 + range % 90, ? FROM range(200)",
        [now],
    )
