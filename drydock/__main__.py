"""Entry point for `python -m drydock`.

This file lets users run drydock without the `drydock` console script
shim being on PATH — useful on Windows where `pip install --user`
puts the script in `%APPDATA%\\Python\\PythonXY\\Scripts` which isn't
on PATH by default.

Usage:
    python -m drydock              # same as `drydock`
    python -m drydock --fix-windows-path
    python -m drydock --setup
    python -m drydock -p "your prompt here"
"""
from drydock.cli.entrypoint import main

if __name__ == "__main__":
    main()
