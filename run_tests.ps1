Get-ChildItem -Path ".\tests\*.py" | ForEach-Object {
  Write-Host "Running tests in $($_.FullName)"
  try {
      python -m unittest $_.FullName
      if ($LASTEXITCODE -ne 0) {
          throw "Tests failed in $($_.FullName)"
      }
  } catch {
      Write-Error $_
      exit 1
  }
}
