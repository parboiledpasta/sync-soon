import re
expr = '[MonthID]&"01"'
print("Expression:", repr(expr))
has_dax = bool(re.search(r'[()[\]+\-*/=<>!&|,\n]', expr))
print("Has DAX chars:", has_dax)
print("_is_physical:", not has_dax)
