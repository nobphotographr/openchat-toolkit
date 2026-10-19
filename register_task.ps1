param([ValidateSet('collection','posting')][string]$Kind='collection',[switch]$StatusOnly,[switch]$Remove)
$ErrorActionPreference='Stop'

$taskRoot=[IO.Path]::GetFullPath($PSScriptRoot)
$taskSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$hash=[Security.Cryptography.SHA256]::Create()
try { $taskId=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRoot.ToLowerInvariant()+'|'+$taskSid)))).Replace('-','').Substring(0,12) }
finally { $hash.Dispose() }
$taskName='OpenChatToolkit-'+$Kind+'-'+$taskId
$marker='OpenChatToolkit '+$Kind+'; '+$taskRoot
$existing=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $marker) { throw 'Unrelated existing task; preserving it.' }
if ($StatusOnly) { if($existing){$existing | Select-Object TaskName,State,Description; Get-ScheduledTaskInfo -TaskName $taskName}else{Write-Host 'Not registered'};exit }
if ($Remove) { if($existing){Unregister-ScheduledTask -TaskName $taskName -Confirm:$false};exit }
$taskPython=Join-Path $taskRoot '.venv\Scripts\pythonw.exe'
$helper=if($Kind -eq 'posting'){'posting-native\PostLabLine.exe'}else{'native\LineHistory.exe'}
if (!(Test-Path -LiteralPath $taskPython) -or !(Test-Path -LiteralPath (Join-Path $taskRoot $helper))) { throw 'Run setup.ps1 first.' }
$workerScript=if($Kind -eq 'posting'){'posting_worker.py'}else{'worker.py'}
$action=New-ScheduledTaskAction -Execute $taskPython -Argument ('"'+(Join-Path $taskRoot $workerScript)+'"') -WorkingDirectory $taskRoot
$tick=New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1)
$tick.Repetition.StopAtDurationEnd=$false
$login=New-ScheduledTaskTrigger -AtLogOn -User $taskSid
$principal=New-ScheduledTaskPrincipal -UserId $taskSid -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$definition=New-ScheduledTask -Action $action -Trigger @($tick,$login) -Principal $principal -Settings $settings -Description $marker
if($existing){Register-ScheduledTask -TaskName $taskName -InputObject $definition -Force | Out-Null}else{Register-ScheduledTask -TaskName $taskName -InputObject $definition | Out-Null}
Write-Host 'Registered. Workers remain inactive until configured and enabled in the app.'
