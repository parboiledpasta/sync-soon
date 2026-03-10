"""Debug script to verify SCORE fix - checks sql_expression after translation."""
import json
import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("SNOWFLAKE_ACCOUNT_ENV", "x")
os.environ.setdefault("SNOWFLAKE_USER_ENV", "x")
os.environ.setdefault("SNOWFLAKE_PASSWORD_ENV", "x")

logging.basicConfig(level=logging.WARNING)

from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter

d = json.load(open("output/debug/raw_fabric_model.json", "r", encoding="utf-8"))
conv = TMSLToOSIConverter()
osi = conv.to_osi({"tmsl": d, "dataset_id": "test"})
sml_conv = OSIToSMLConverter()
sml = sml_conv.from_osi(osi)

out_path = os.path.join(os.path.dirname(__file__), "debug_score_output.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("=" * 80 + "\n")
    f.write("CRITICAL CHECK: SalesFact metrics with sql_expression containing SCORE\n")
    f.write("=" * 80 + "\n")
    found_issue = False
    for m in sml.metrics:
        if m.dataset == "SalesFact" and m.sql_expression and "SCORE" in m.sql_expression.upper():
            f.write(f"  BUG: {m.unique_name} | sql_expr={m.sql_expression}\n")
            found_issue = True
    if not found_issue:
        f.write("  PASS: No SalesFact metrics reference SCORE in sql_expression\n")

    f.write("\n" + "=" * 80 + "\n")
    f.write("Sentiment metric on SalesFact details:\n")
    f.write("=" * 80 + "\n")
    for m in sml.metrics:
        if m.unique_name == "Sentiment" and m.dataset == "SalesFact":
            f.write(f"  unique_name: {m.unique_name}\n")
            f.write(f"  dataset: {m.dataset}\n")
            f.write(f"  expression (DAX): {m.expression}\n")
            f.write(f"  sql_expression: {m.sql_expression}\n")
            f.write(f"  sync_enabled: {m.sync_enabled}\n")
            f.write(f"  sync_failure_reason: {m.sync_failure_reason}\n")
            f.write(f"  complexity_tier: {m.complexity_tier}\n")

    f.write("\n" + "=" * 80 + "\n")
    f.write("All SalesFact metrics with sql_expression:\n")
    f.write("=" * 80 + "\n")
    for m in sml.metrics:
        if m.dataset == "SalesFact" and m.sql_expression:
            f.write(f"  {m.unique_name} | sql_expr={m.sql_expression}\n")

print(f"Output written to {out_path}")
