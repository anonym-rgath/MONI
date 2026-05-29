import os
import sys

# backend/ in den Importpfad legen, damit `import server` funktioniert,
# egal von wo pytest gestartet wird.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
