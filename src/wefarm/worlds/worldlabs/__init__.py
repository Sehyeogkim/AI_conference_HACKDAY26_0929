"""World Labs (Marble) World API client with a hard spending guard, plus tools to measure generated scenes.

Only this package talks to the World Labs API. Every paid call goes through the budget guard and the
persistent spending ledger; the API key is wrapped so it never appears in output, logs, or errors.
"""
