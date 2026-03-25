# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

# Collect all dependencies (including hidden imports and binaries) from builtins modules
core_builtins_deps = collect_all('drydock.core.tools.builtins')
acp_builtins_deps = collect_all('drydock.acp.tools.builtins')

# Extract hidden imports and binaries, filtering to ensure only strings are in hiddenimports
hidden_imports = []
for item in core_builtins_deps[2] + acp_builtins_deps[2]:
    if isinstance(item, str):
        hidden_imports.append(item)

binaries = core_builtins_deps[1] + acp_builtins_deps[1]

a = Analysis(
    ['drydock/acp/entrypoint.py'],
    pathex=[],
    binaries=binaries,
    datas=[
        ('drydock/core/prompts/*.md', 'drydock/core/prompts'),
        ('drydock/core/tools/builtins/prompts/*.md', 'drydock/core/tools/builtins/prompts'),
        ('drydock/setup/*', 'drydock/setup'),
        ('drydock/core/tools/builtins/*.py', 'drydock/core/tools/builtins'),
        ('drydock/acp/tools/builtins/*.py', 'drydock/acp/tools/builtins'),
        ('drydock/skills/*/SKILL.md', 'drydock/skills'),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='drydock-acp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
