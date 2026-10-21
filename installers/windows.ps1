# Installs mpv-anilist-updater into mpv's scripts folder. Run it again to update.
$ZipURL = if ($env:ANILIST_UPDATER_ZIP) { $env:ANILIST_UPDATER_ZIP } else { "https://github.com/AzuredBlue/mpv-anilist-updater/archive/refs/heads/main.zip" }
$TokenURL = "https://anilist.co/api/v2/oauth/authorize?client_id=20740&response_type=token"
$UserFiles = "anilistToken.txt", "cache.json", "queue.json", "anilistUpdater.conf"

# Messages use Write-Host so they never end up in a function's return value.
function Get-ConfigDir {
	if ($env:MPV_CONFIG_DIR) { return $env:MPV_CONFIG_DIR }
	# Next to mpv.exe, or inside it, when mpv is used as a portable install
	$Portable = Join-Path "$PWD" "portable_config"
	if (Test-Path -LiteralPath $Portable) { return $Portable }
	if ((Split-Path "$PWD" -Leaf) -eq "portable_config") { return "$PWD" }
	return Join-Path $env:APPDATA "mpv"
}

function Remove-Path($Path) {
	if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force -Recurse }
}

# Native commands write progress to stderr, which "Stop" would turn into an error.
function Test-Python {
	$ErrorActionPreference = "Continue"
	if (!(Get-Command python -ErrorAction SilentlyContinue)) { return $false }
	python -c "import sys; sys.exit(sys.version_info < (3, 11))" 2> $null
	return $LASTEXITCODE -eq 0
}

function Update-GitClone($Target) {
	$ErrorActionPreference = "Continue"
	if (!(Get-Command git -ErrorAction SilentlyContinue)) {
		throw "git is needed to update a git clone."
	}
	$Branch = git -C $Target rev-parse --abbrev-ref HEAD
	if ($Branch -ne "main") {
		throw "The clone is on branch '$Branch'. Run 'git checkout main' in $Target and try again."
	}
	git -C $Target pull --ff-only | Out-Host
	if ($LASTEXITCODE -ne 0) {
		throw "git pull failed. Commit or discard your changes in $Target and try again."
	}
	# Hide the development files; `git sparse-checkout disable` brings them back
	git -C $Target sparse-checkout set --no-cone '/*' '!/tests/' '!/.github/' '!/installers/' '!/ruff.toml' | Out-Host
	if ($LASTEXITCODE -ne 0) {
		Write-Host "Warning: couldn't hide the development files (needs a recent git)."
	}
	$State.Result = "updated (git)"
}

function Install-FromZip($Target, $Backup) {
	$Tmp = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
	$Zip = Join-Path $Tmp "source.zip"
	$OldVersionMoved = $false
	try {
		New-Item -ItemType Directory -Force -Path $Tmp > $null
		Write-Host "Downloading..."
		if ($env:ANILIST_UPDATER_ZIP -and (Test-Path -LiteralPath $ZipURL)) {
			Copy-Item -LiteralPath $ZipURL -Destination $Zip
		}
		else {
			# Older Windows PowerShell defaults to TLS versions GitHub rejects
			[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
			try {
				Invoke-WebRequest -UseBasicParsing -OutFile $Zip -Uri $ZipURL
			}
			catch {
				throw "Couldn't download $ZipURL"
			}
		}
		Expand-Archive -LiteralPath $Zip -DestinationPath (Join-Path $Tmp "source") -Force
		$NewVersion = Get-ChildItem -LiteralPath (Join-Path $Tmp "source") -Directory | Select-Object -First 1
		if (!$NewVersion -or !(Test-Path -LiteralPath (Join-Path $NewVersion.FullName "main.lua"))) {
			throw "The download doesn't contain the script."
		}

		New-Item -ItemType Directory -Force -Path (Split-Path $Target -Parent) > $null
		$State.Result = "installed"
		if (Test-Path -LiteralPath $Target) {
			$State.Result = "updated"
			Remove-Path $Backup
			Move-Item -LiteralPath $Target -Destination $Backup -Force
			$OldVersionMoved = $true
		}
		Move-Item -LiteralPath $NewVersion.FullName -Destination $Target -Force
		foreach ($File in $UserFiles) {
			$Saved = Join-Path $Backup $File
			if (Test-Path -LiteralPath $Saved) {
				Copy-Item -LiteralPath $Saved -Destination (Join-Path $Target $File) -Force
			}
		}
		Remove-Path $Backup
		$OldVersionMoved = $false
	}
	catch {
		if ($OldVersionMoved) {
			Write-Host "Restoring the previous version..."
			Remove-Path $Target
			Move-Item -LiteralPath $Backup -Destination $Target -Force
		}
		throw
	}
	finally {
		Remove-Path $Tmp
	}
}

function Request-Token($Target) {
	$TokenFile = Join-Path $Target "anilistToken.txt"
	Write-Host ""
	Write-Host "The script needs an AniList token. Authorize the app here and copy the token:"
	Write-Host $TokenURL
	$Token = ""
	if ([Environment]::UserInteractive -and ![Console]::IsInputRedirected) {
		$Token = "$(Read-Host "Paste the token (Enter to skip)")".Trim()
	}
	if ($Token) {
		Set-Content -LiteralPath $TokenFile -Value $Token -NoNewline
	}
	else {
		Write-Host "Put the token in $TokenFile before using the script."
	}
}

# The preferences are set inside a function so they don't leak into the user's session under `irm | iex`.
# It returns instead of calling exit for the same reason: exit would close their terminal with the message.
function Install-MpvAnilistUpdater {
	$ErrorActionPreference = "Stop"
	$ProgressPreference = "SilentlyContinue"
	$State = @{}
	try {
		$ConfigDir = Get-ConfigDir
		$Target = Join-Path (Join-Path $ConfigDir "scripts") "mpv-anilist-updater"
		Write-Host "Installing into $Target"

		if (!(Test-Python)) {
			Write-Host "Warning: Python 3.11 or newer was not found as 'python'. The script needs it to run."
		}

		if (Test-Path -LiteralPath (Join-Path $Target ".git")) {
			Update-GitClone $Target
		}
		else {
			Install-FromZip $Target (Join-Path $ConfigDir ".anilist-updater-backup")
		}

		if (!(Test-Path -LiteralPath (Join-Path $Target "anilistToken.txt"))) {
			Request-Token $Target
		}
		Write-Host "mpv-anilist-updater has been $($State.Result)."
	}
	catch {
		Write-Host "Error: $($_.Exception.Message)"
		$global:LASTEXITCODE = 1
	}
}

Install-MpvAnilistUpdater
