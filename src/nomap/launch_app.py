from __future__ import annotations
import os, sys, inspect
import nomap.ui_app as ui_app

def main():
    app_path = inspect.getsourcefile(ui_app)
    if not app_path:
        print("Could not resolve nomap.ui_app path", file=sys.stderr)
        sys.exit(1)
    # Replace current process with: streamlit run <path>
    os.execvp("streamlit", ["streamlit", "run", app_path])
