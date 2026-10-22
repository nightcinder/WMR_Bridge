# Build and run the host unit tests (protocol encoder/reassembler + queue, and the input decoder).
# Needs any C99 compiler on PATH: gcc, clang, or `zig cc`. No ESP-IDF needed.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe  = Join-Path ([System.IO.Path]::GetTempPath()) "wmr_test_protocol.exe"

$cc = $null
foreach ($name in "gcc", "clang", "cc") {
    if (Get-Command $name -ErrorAction SilentlyContinue) { $cc = @($name); break }
}
if (-not $cc -and (Get-Command zig -ErrorAction SilentlyContinue)) { $cc = @("zig", "cc") }
if (-not $cc) { throw "No C compiler found (install gcc/clang/zig, or see README)." }

$sources = "$root\tests\test_protocol.c", "$root\src\wmr_protocol.c", "$root\src\wmr_queue.c", "$root\src\wmr_decoded.c", "$root\src\wmr_decode.c"
& $cc[0] $cc[1..($cc.Length)] -std=c99 -Wall -Wextra -Wconversion "-I$root\src" $sources -o $exe
if ($LASTEXITCODE -ne 0) { throw "compile failed" }
& $exe "$root\samples\controller_frames.txt"
$protocolStatus = $LASTEXITCODE

$exeDecode = Join-Path ([System.IO.Path]::GetTempPath()) "wmr_test_decode.exe"
$decodeSources = "$root\tests\test_decode.c", "$root\src\wmr_decode.c"
& $cc[0] $cc[1..($cc.Length)] -std=c99 -Wall -Wextra -Wconversion "-I$root\src" $decodeSources -lm -o $exeDecode
if ($LASTEXITCODE -ne 0) { throw "compile failed (decode)" }
& $exeDecode "$root\samples\controller_frames.txt"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$exeLink = Join-Path ([System.IO.Path]::GetTempPath()) "wmr_test_link.exe"
& $cc[0] $cc[1..($cc.Length)] -std=c99 -Wall -Wextra -Wconversion "-I$root\src" "$root\tests\test_link.c" "$root\src\wmr_link.c" -o $exeLink
if ($LASTEXITCODE -ne 0) { throw "compile failed (link)" }
& $exeLink
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$exeStream = Join-Path ([System.IO.Path]::GetTempPath()) "wmr_test_stream_mode.exe"
$streamSources = "$root\tests\test_stream_mode.c", "$root\src\wmr_stream_mode.c", "$root\src\wmr_decoded.c", "$root\src\wmr_decode.c"
& $cc[0] $cc[1..($cc.Length)] -std=c99 -Wall -Wextra -Wconversion "-I$root\src" $streamSources -o $exeStream
if ($LASTEXITCODE -ne 0) { throw "compile failed (stream mode)" }
& $exeStream "$root\samples\controller_frames.txt"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$exeBatch = Join-Path ([System.IO.Path]::GetTempPath()) "wmr_test_batch.exe"
$batchSources = "$root\tests\test_batch.c", "$root\src\wmr_batch.c", "$root\src\wmr_stream_mode.c", "$root\src\wmr_queue.c", "$root\src\wmr_decoded.c", "$root\src\wmr_decode.c"
& $cc[0] $cc[1..($cc.Length)] -std=c99 -Wall -Wextra -Wconversion "-I$root\src" $batchSources -o $exeBatch
if ($LASTEXITCODE -ne 0) { throw "compile failed (batch)" }
& $exeBatch
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$exeDisc = Join-Path ([System.IO.Path]::GetTempPath()) "wmr_test_discovery.exe"
& $cc[0] $cc[1..($cc.Length)] -std=c99 -Wall -Wextra -Wconversion "-I$root\src" "$root\tests\test_discovery.c" "$root\src\wmr_discovery.c" -o $exeDisc
if ($LASTEXITCODE -ne 0) { throw "compile failed (discovery)" }
& $exeDisc
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$exeDiag = Join-Path ([System.IO.Path]::GetTempPath()) "wmr_test_diag.exe"
& $cc[0] $cc[1..($cc.Length)] -std=c99 -Wall -Wextra -Wconversion "-I$root\src" "$root\tests\test_diag.c" "$root\src\wmr_devloss.c" "$root\src\wmr_diag.c" -o $exeDiag
if ($LASTEXITCODE -ne 0) { throw "compile failed (diag)" }
& $exeDiag
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
exit $protocolStatus
