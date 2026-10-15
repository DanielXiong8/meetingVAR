param(
    [string]$Project = '',
    [ValidateSet('cuda', 'cpu')][string]$Device = 'cpu',
    [int]$Duration = 0,
    [switch]$NoScreen,
    [switch]$Devices,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv-live\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:MEETINGVAR_MODELS = Join-Path $RepoRoot 'models'
$env:MODELSCOPE_CACHE = Join-Path $RepoRoot 'models\.cache'
$OutputEncoding = New-Object System.Text.UTF8Encoding
[Console]::OutputEncoding = $OutputEncoding
Set-Location -LiteralPath $RepoRoot

if ($Help) {
    Write-Host 'meetingVAR local trial (Python 3.10, local ASR models)'
    Write-Host '.\Start-MeetingVAR.cmd                         Interactive menu'
    Write-Host '.\Start-MeetingVAR.cmd -Devices                List audio outputs'
    Write-Host '.\Start-MeetingVAR.cmd -Project "D:\project"    Start a meeting'
    Write-Host 'Optional: -Device cpu -Duration 120 -NoScreen'
    exit 0
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Missing .venv-live. See docs/LOCAL_TRIAL.md for installation.'
}

function Invoke-MeetingVAR([string[]]$CliArguments) {
    & $script:Python -m meetingvar @CliArguments
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "meetingVAR exited with code $LASTEXITCODE. See the error above."
    }
}

function Start-LocalMeeting([string]$ProjectPath, [int]$Seconds) {
    if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
        throw "Project folder does not exist: $ProjectPath"
    }
    $parameters = @('go', $ProjectPath, '--device', $script:Device, '--duration', "$Seconds")
    if ($script:NoScreen) { $parameters += '--no-screen' }
    Write-Host ''
    Write-Host '请通过 Windows 默认扬声器或耳机播放会议音频。'
    Write-Host '回车 = 手动保存 VAR 片段；Ctrl+C = 结束并生成纪要。'
    Write-Host '当前采集系统播放声音，不额外采集本机麦克风。'
    Invoke-MeetingVAR $parameters
}

function Open-LatestReport {
    $reports = Join-Path $script:RepoRoot 'data\reports'
    $latest = Get-ChildItem -LiteralPath $reports -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        Write-Host "最新报告目录：$($latest.FullName)"
        Start-Process -FilePath explorer.exe -ArgumentList ('"' + $latest.FullName + '"')
    } else {
        Write-Host '还没有报告。请先完成一次会议实测，并至少触发或手动保存一个片段。'
    }
}

if ($Devices) {
    & $Python -m meetingvar devices
    exit $LASTEXITCODE
}
if ($Project) {
    Start-LocalMeeting $Project $Duration
    exit $LASTEXITCODE
}

while ($true) {
    Write-Host ''
    Write-Host '=== meetingVAR 本机实测（CPU）==='
    Write-Host '1  查看系统播放设备'
    Write-Host '2  使用演示资料，开始 120 秒实测'
    Write-Host '3  使用自己的项目资料，开始会议'
    Write-Host '4  打开最新报告目录'
    Write-Host '5  用现有 WAV 测真实识别'
    Write-Host '6  打开中文实测指南'
    Write-Host 'Q  退出'
    $choice = Read-Host '请选择'
    try {
        switch ($choice.Trim().ToUpperInvariant()) {
            '1' { Invoke-MeetingVAR @('devices') }
            '2' {
                $trial = Join-Path $RepoRoot 'data\trial_project'
                New-Item -ItemType Directory -Path $trial -Force | Out-Null
                $notes = Join-Path $trial 'project.md'
                if (-not (Test-Path -LiteralPath $notes)) {
                    Copy-Item -LiteralPath (Join-Path $RepoRoot 'configs\trial_project.md') -Destination $notes
                }
                Start-LocalMeeting $trial 120
            }
            '3' {
                $folder = (Read-Host '项目资料文件夹的完整路径').Trim().Trim('"')
                Start-LocalMeeting $folder $Duration
            }
            '4' { Open-LatestReport }
            '5' {
                $wav = (Read-Host 'WAV 文件的完整路径').Trim().Trim('"')
                if (-not (Test-Path -LiteralPath $wav -PathType Leaf)) {
                    throw "WAV file does not exist: $wav"
                }
                Invoke-MeetingVAR @('replay', '--wav', $wav, '--device', $Device,
                    '--terms', (Join-Path $RepoRoot 'configs\terms_demo.txt'))
                Write-Host 'Use the printed session ID: meetingvar digest --session <id>'
            }
            '6' {
                Start-Process -FilePath notepad.exe -ArgumentList ('"' + (Join-Path $RepoRoot 'docs\LOCAL_TRIAL.md') + '"')
            }
            'Q' { exit 0 }
            default { Write-Host '请输入 1 到 6，或 Q。' }
        }
    } catch {
        Write-Warning $_.Exception.Message
    }
}
