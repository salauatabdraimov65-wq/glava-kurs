
# Windows: вывод в файл/конвейер идёт в кодировке cp1251, где нет «✅», «→», «⚠» — без этого print падает.
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
