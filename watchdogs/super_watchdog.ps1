# Super Watchdog - Intelligent crawler management with adaptive strategies

param(
    [int]$MaxRestarts = 50,
    [int]$HealthCheckIntervalSeconds = 30,
    [string]$Mode = "enhanced",  # "basic" or "enhanced"
    [switch]$ContinuousDiscovery = $true,
    [int]$DiscoveryIntervalMinutes = 30,
    [int]$UrlGrowthThreshold = 200,
    [int]$MinUrlsToCrawl = 200
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$CrawlerScript = Join-Path $ProjectRoot "crawlers\async_crawler.py"
$EnhancedDiscoveryScript = Join-Path $ProjectRoot "discovery\enhanced_url_discovery.py"
$BasicDiscoveryScript = Join-Path $ProjectRoot "discovery\url_discovery.py"
$LogFile = Join-Path $ScriptDir "super_watchdog.log"

$ConfigFile = Join-Path $ProjectRoot "config\crawler_config.yaml"
$CrawlDataPath = $env:CRAWL_OUTPUT_DIR

if (-not $CrawlDataPath) {
    if (Test-Path $ConfigFile) {
        $outputLine = Get-Content $ConfigFile | Where-Object { $_ -match '^\s*output_dir\s*:' } | Select-Object -First 1
        if ($outputLine) {
            $rawValue = ($outputLine -replace '^\s*output_dir\s*:\s*', '').Trim().Trim('"')
            if ([System.IO.Path]::IsPathRooted($rawValue)) {
                $CrawlDataPath = $rawValue
            } else {
                $CrawlDataPath = Join-Path $ProjectRoot $rawValue
            }
        }
    }
}

if (-not $CrawlDataPath) {
    $CrawlDataPath = Join-Path $ProjectRoot "crawl_data"
}

$DiscoveredUrlsBasic = Join-Path $CrawlDataPath "discovered_urls.json"
$DiscoveredUrlsEnhanced = Join-Path $CrawlDataPath "discovered_urls_enhanced.json"

function Write-Log {
    param([string]$Message, [string]$Color = "Cyan")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $Message"
    Write-Host $logMessage -ForegroundColor $Color
    Add-Content -Path $LogFile -Value $logMessage
}

function Get-CrawlerProcess {
    Get-Process python -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -eq $VenvPython -and
        (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)" | Select-Object -ExpandProperty CommandLine) -like "*async_crawler.py*"
    }
}

function Get-DiscoveryProcess {
    Get-Process python -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -eq $VenvPython -and
        (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)" | Select-Object -ExpandProperty CommandLine) -like "*url_discovery*.py*"
    }
}

function Get-CrawlStats {
    $stats = @{
        TotalFiles = 0
        TopicProcessed = 0
        TopicCount = $null
        LastUpdate = $null
        UrlsDiscovered = 0
        SuccessRate = 0
    }

    $topicDirs = Get-ChildItem "$CrawlDataPath\raw" -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'topic_*' }
    if ($topicDirs) {
        $stats.TotalFiles = ($topicDirs | ForEach-Object { (Get-ChildItem $_.FullName -File).Count } | Measure-Object -Sum).Sum
        $stats.TopicProcessed = $topicDirs.Count
        $stats.LastUpdate = ($topicDirs | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
    }

    $urlFile = if (Test-Path $DiscoveredUrlsEnhanced) { $DiscoveredUrlsEnhanced } else { $DiscoveredUrlsBasic }
    if (Test-Path $urlFile) {
        $json = Get-Content $urlFile -Raw | ConvertFrom-Json
        $stats.UrlsDiscovered = ($json.PSObject.Properties | ForEach-Object { $_.Value.urls.Count } | Measure-Object -Sum).Sum
        $stats.TopicCount = $json.PSObject.Properties.Count
        if ($stats.UrlsDiscovered -gt 0) {
            $stats.SuccessRate = [Math]::Round($stats.TotalFiles / $stats.UrlsDiscovered * 100, 1)
        }
    }

    return $stats
}

function Start-URLDiscovery {
    param([string]$DiscoveryMode = "enhanced")

    $script = if ($DiscoveryMode -eq "enhanced") { $EnhancedDiscoveryScript } else { $BasicDiscoveryScript }
    Write-Log "Starting $DiscoveryMode URL discovery in background..." "Yellow"

    $discoveryStdout = Join-Path $ScriptDir "discovery_stdout.log"
    $discoveryStderr = Join-Path $ScriptDir "discovery_stderr.log"

    $process = Start-Process -FilePath $VenvPython `
                             -ArgumentList $script `
                             -WorkingDirectory $ProjectRoot `
                             -PassThru `
                             -NoNewWindow `
                             -RedirectStandardOutput $discoveryStdout `
                             -RedirectStandardError $discoveryStderr

    if ($process) {
        Write-Log "$DiscoveryMode discovery started in background (PID: $($process.Id))" "Green"
        Write-Log "Waiting for completion (checking every 30s)..." "Yellow"

        $maxWaitMinutes = 90
        $checkIntervalSeconds = 30
        $checksElapsed = 0

        while (-not $process.HasExited) {
            Start-Sleep -Seconds $checkIntervalSeconds
            $checksElapsed++

            if (($checksElapsed % 6) -eq 0) {
                $minutesElapsed = [Math]::Round($checksElapsed * $checkIntervalSeconds / 60, 1)
                Write-Log "  Discovery still running... ($minutesElapsed minutes elapsed)" "Gray"
            }

            if (($checksElapsed * $checkIntervalSeconds / 60) -gt $maxWaitMinutes) {
                Write-Log "Discovery exceeded $maxWaitMinutes minutes - may be stalled" "Yellow"
                return $false
            }
        }

        $process.WaitForExit()

        if ($process.ExitCode -eq 0) {
            Write-Log "$DiscoveryMode discovery completed successfully" "Green"
            return $true
        } else {
            Write-Log "$DiscoveryMode discovery failed with exit code: $($process.ExitCode)" "Red"
            Write-Log "Check logs: $discoveryStderr" "Yellow"
            return $false
        }
    }

    Write-Log "Failed to start $DiscoveryMode discovery" "Red"
    return $false
}

function Start-URLDiscoveryAsync {
    param([string]$DiscoveryMode = "enhanced")

    $script = if ($DiscoveryMode -eq "enhanced") { $EnhancedDiscoveryScript } else { $BasicDiscoveryScript }
    Write-Log "Starting $DiscoveryMode URL discovery (async)..." "Yellow"

    $discoveryStdout = Join-Path $ScriptDir "discovery_stdout.log"
    $discoveryStderr = Join-Path $ScriptDir "discovery_stderr.log"

    $process = Start-Process -FilePath $VenvPython `
                             -ArgumentList $script `
                             -WorkingDirectory $ProjectRoot `
                             -PassThru `
                             -NoNewWindow `
                             -RedirectStandardOutput $discoveryStdout `
                             -RedirectStandardError $discoveryStderr

    if ($process) {
        Write-Log "$DiscoveryMode discovery started (PID: $($process.Id))" "Green"
        return $process
    }

    Write-Log "Failed to start $DiscoveryMode discovery" "Red"
    return $null
}

function Start-Crawler {
    Write-Log "Starting async crawler..." "Yellow"

    $process = Start-Process -FilePath $VenvPython `
                             -ArgumentList $CrawlerScript `
                             -WorkingDirectory $ProjectRoot `
                             -PassThru `
                             -NoNewWindow `
                             -RedirectStandardOutput (Join-Path $ScriptDir "crawler_stdout.log") `
                             -RedirectStandardError (Join-Path $ScriptDir "crawler_stderr.log")

    Start-Sleep -Seconds 3

    if ($process -and !$process.HasExited) {
        Write-Log "Crawler started (PID: $($process.Id))" "Green"
        return $process
    }

    Write-Log "Failed to start crawler" "Red"
    return $null
}

function Invoke-IntelligentRestart {
    param([hashtable]$Stats)

    Write-Log "=== INTELLIGENT RESTART ANALYSIS ===" "Cyan"
    Write-Log "Current stats:" "Yellow"
    Write-Log "  Files downloaded: $($Stats.TotalFiles)" "Gray"
    Write-Log "  Topics: $($Stats.TopicProcessed) / $($Stats.TopicCount)" "Gray"
    Write-Log "  URLs discovered: $($Stats.UrlsDiscovered)" "Gray"
    Write-Log "  Success rate: $($Stats.SuccessRate)%" "Gray"

    $needsEnhancedSearch = $false

    if ($Stats.UrlsDiscovered -lt 500) {
        Write-Log "Low URL count detected (<500) - triggering enhanced discovery" "Yellow"
        $needsEnhancedSearch = $true
    }

    if ($Stats.TopicProcessed -lt 5) {
        Write-Log "Low topic coverage - triggering enhanced discovery" "Yellow"
        $needsEnhancedSearch = $true
    }

    if ($Stats.SuccessRate -lt 50) {
        Write-Log "Low success rate (<50%) - triggering enhanced discovery" "Yellow"
        $needsEnhancedSearch = $true
    }

    if ($needsEnhancedSearch) {
        Write-Log "Running enhanced URL discovery to find better sources..." "Green"
        if (Start-URLDiscovery -DiscoveryMode "enhanced") {
            $newStats = Get-CrawlStats
            Write-Log "Enhanced discovery complete! New URL count: $($newStats.UrlsDiscovered)" "Green"
        }
    }

    Write-Log "Restarting crawler with updated URL list..." "Green"
    return Start-Crawler
}

Write-Log "=========================================" "Green"
Write-Log "SUPER WATCHDOG STARTED" "Green"
Write-Log "=========================================" "Green"
Write-Log "Mode: $Mode" "Cyan"
Write-Log "Max restarts: $MaxRestarts" "Cyan"
Write-Log "Health check interval: $HealthCheckIntervalSeconds seconds" "Cyan"
Write-Log "Continuous discovery: $ContinuousDiscovery" "Cyan"
Write-Log "Discovery interval: $DiscoveryIntervalMinutes minutes" "Cyan"
Write-Log "URL growth threshold: $UrlGrowthThreshold" "Cyan"
Write-Log "Min URLs to crawl: $MinUrlsToCrawl" "Cyan"
Write-Log "Output dir: $CrawlDataPath" "Cyan"
Write-Log ""

if (-not (Test-Path $DiscoveredUrlsBasic) -and -not (Test-Path $DiscoveredUrlsEnhanced)) {
    Write-Log "No discovered URLs found - running initial discovery..." "Yellow"
    if ($Mode -eq "enhanced") {
        Start-URLDiscovery -DiscoveryMode "enhanced"
    } else {
        Start-URLDiscovery -DiscoveryMode "basic"
    }
}

$initialStats = Get-CrawlStats
Write-Log "Initial Statistics:" "Yellow"
Write-Log "  Total files: $($initialStats.TotalFiles)" "Gray"
Write-Log "  Topics: $($initialStats.TopicProcessed) / $($initialStats.TopicCount)" "Gray"
Write-Log "  URLs discovered: $($initialStats.UrlsDiscovered)" "Gray"
Write-Log ""

$restartCount = 0
$lastFileCount = $initialStats.TotalFiles
$stagnantChecks = 0
$lastDiscoveryCheck = [DateTime]::Now
$lastDiscoveryUrlCount = $initialStats.UrlsDiscovered
$discoveryProcess = $null

$crawlerProcess = Start-Crawler
if (-not $crawlerProcess) {
    Write-Log "WARNING: Initial crawler did not stay running - will rely on discovery loop" "Yellow"
    $crawlerProcess = $null
}

while ($true) {
    Start-Sleep -Seconds $HealthCheckIntervalSeconds

    $process = Get-CrawlerProcess
    $currentStats = Get-CrawlStats

    if ($ContinuousDiscovery) {
        $discoveryProcess = Get-DiscoveryProcess

        if (([DateTime]::Now - $lastDiscoveryCheck).TotalMinutes -ge $DiscoveryIntervalMinutes) {
            $lastDiscoveryCheck = [DateTime]::Now
            if (-not $discoveryProcess) {
                $discoveryProcess = Start-URLDiscoveryAsync -DiscoveryMode $Mode
            } else {
                Write-Log "Discovery already running (PID: $($discoveryProcess.Id))" "Gray"
            }
        }

        if (-not $discoveryProcess) {
            $newStats = Get-CrawlStats
            $urlDelta = $newStats.UrlsDiscovered - $lastDiscoveryUrlCount
            if ($urlDelta -ge $UrlGrowthThreshold) {
                Write-Log "URL growth detected (+$urlDelta) - refreshing crawler" "Yellow"
                $lastDiscoveryUrlCount = $newStats.UrlsDiscovered

                if ($process) {
                    Write-Log "Stopping crawler to reload URL list..." "Yellow"
                    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 3
                }

                $crawlerProcess = Start-Crawler
                $lastFileCount = $newStats.TotalFiles
                $stagnantChecks = 0
            }

            if (-not $process -and $newStats.UrlsDiscovered -ge $MinUrlsToCrawl) {
                Write-Log "Crawler not running and URL pool ready - starting crawler" "Green"
                $crawlerProcess = Start-Crawler
                $lastFileCount = $newStats.TotalFiles
                $stagnantChecks = 0
            }
        }
    }

    if (-not $process) {
        Write-Log "Crawler stopped - checking if complete or failed..." "Yellow"

        if ($restartCount -ge $MaxRestarts) {
            Write-Log "Maximum restart limit reached ($MaxRestarts)" "Red"
            Write-Log "Final stats: $($currentStats.TotalFiles) files from $($currentStats.TopicProcessed) topics" "Yellow"
            exit 0
        }

        $restartCount++
        Write-Log "Restart attempt $restartCount / $MaxRestarts" "Cyan"

        $crawlerProcess = Invoke-IntelligentRestart -Stats $currentStats
        $lastFileCount = $currentStats.TotalFiles
        $stagnantChecks = 0

    } else {
        if ($currentStats.TotalFiles -gt $lastFileCount) {
            Write-Log "[Health] Active: $($currentStats.TotalFiles) files (+$($currentStats.TotalFiles - $lastFileCount)) from $($currentStats.TopicProcessed) topics" "Green"
            $lastFileCount = $currentStats.TotalFiles
            $stagnantChecks = 0
        } else {
            $stagnantChecks++
            if ($stagnantChecks -ge 10) {
                Write-Log "WARNING: No progress in 5 minutes - crawler may be stalled" "Yellow"
            }
        }

        $script:lastDetailedLog = if ($script:lastDetailedLog) { $script:lastDetailedLog } else { [DateTime]::MinValue }
        if (([DateTime]::Now - $script:lastDetailedLog).TotalMinutes -ge 5) {
            Write-Log "=== STATUS REPORT ===" "Cyan"
            Write-Log "Crawler: RUNNING (PID: $($process.Id))" "Green"
            Write-Log "Files: $($currentStats.TotalFiles) | Topics: $($currentStats.TopicProcessed)/$($currentStats.TopicCount) | Success Rate: $($currentStats.SuccessRate)%" "Yellow"
            Write-Log "Restarts: $restartCount | Stagnant checks: $stagnantChecks" "Gray"
            $script:lastDetailedLog = [DateTime]::Now
        }
    }
}
