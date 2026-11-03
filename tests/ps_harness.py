"""Load PowerShell function definitions only; never execute a production entry point."""
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")


def run_ps(test, root, sources, body, timeout=30):
    if not PWSH:
        test.skipTest("PowerShell 7 required")
    loader = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
foreach ($source in ($env:TEST_PS_SOURCES | ConvertFrom-Json)) {
    if (-not (Test-Path -LiteralPath $source)) { continue }
    $ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
    foreach ($fn in $ast.FindAll({ param($n)
        $n -is [Management.Automation.Language.FunctionDefinitionAst]
    }, $false)) { . ([scriptblock]::Create($fn.Extent.Text)) }
}
$scriptDir = $env:TEST_PS_ROOT
$root = $scriptDir
function Log($message) { }
'''
    script = Path(root) / "harness.ps1"
    script.write_text(loader + body, encoding="utf-8")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        env=dict(os.environ, TEST_PS_ROOT=str(root),
                 TEST_PS_SOURCES=json.dumps([str(ROOT / s) for s in sources])),
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    test.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    return json.loads(result.stdout.strip())
