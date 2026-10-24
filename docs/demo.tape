# Records docs/demo.gif. Run from the repo root: vhs docs/demo.tape
Output docs/demo.gif
Set FontSize 14
Set Width 1200
Set Height 700
Set TypingSpeed 40ms

Hide
Type "rm -f datamend.duckdb && uv sync -q && clear" Enter
Show

Type "uv run datamend seed" Enter
Sleep 1s
Type "uv run datamend inject stale raw_orders && uv run datamend inject stale raw_payments" Enter
Sleep 1s
Type "uv run datamend inject gap raw_customers" Enter
Sleep 1s
Type "uv run datamend run" Enter
Sleep 3s
Type "uv run datamend audit | head -4" Enter
Sleep 4s
