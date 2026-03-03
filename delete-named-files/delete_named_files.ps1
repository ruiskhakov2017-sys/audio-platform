# Recursive delete: clean_text.txt, Tekstovyj dokument.txt (any encoding)
param([string]$RootFolder = $PSScriptRoot)
$root = (Resolve-Path -LiteralPath $RootFolder -ErrorAction Stop).Path
# "Tekstovyj dokument.txt" in Cyrillic, built from char codes so this file can be ASCII
$nameRu = [char]0x0422 + [char]0x0435 + [char]0x043a + [char]0x0441 + [char]0x0442 + [char]0x043e + [char]0x0432 + [char]0x044b + [char]0x0439 + ' ' + [char]0x0434 + [char]0x043e + [char]0x043a + [char]0x0443 + [char]0x043c + [char]0x0435 + [char]0x043d + [char]0x0442 + '.txt'
$names = @('clean_text.txt', $nameRu)
Get-ChildItem -Path $root -Recurse -File | Where-Object { $_.Name -in $names } | ForEach-Object {
    Write-Host "Del: $($_.FullName)"
    Remove-Item $_.FullName -Force
}
Write-Host "Done."
