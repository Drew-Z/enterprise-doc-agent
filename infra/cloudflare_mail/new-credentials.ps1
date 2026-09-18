#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$Path,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'This helper requires Windows DACL support.' }
$effectiveCodexHome = [Environment]::GetEnvironmentVariable('CODEX_HOME')
if ([string]::IsNullOrWhiteSpace($effectiveCodexHome)) {
    $effectiveCodexHome = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex'
}
if ([string]::IsNullOrWhiteSpace($Path)) {
    $Path = Join-Path $effectiveCodexHome 'secrets\docagent-private-mail\credentials.json'
}
$credentialPath = [IO.Path]::GetFullPath($Path)
$credentialRoot = [IO.Path]::GetDirectoryName($credentialPath)
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$allowedSids = @($userSid.Value, $systemSid.Value)

function Test-PrivateAcl([string]$Target) {
    $item = Get-Item -LiteralPath $Target
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Credential links or junctions are not allowed.'
    }
    $acl = Get-Acl -LiteralPath $Target
    if (-not $acl.AreAccessRulesProtected) { throw 'Credential DACL is not protected.' }
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    $allows = @($rules | Where-Object { $_.AccessControlType -eq 'Allow' })
    if ($allows.Count -ne 2) { throw 'Credential DACL has unexpected access rules.' }
    foreach ($rule in $allows) {
        if ($rule.IdentityReference.Value -notin $allowedSids -or
            $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
            $rule.IsInherited) {
            throw 'Credential DACL grants unexpected access.'
        }
    }
    foreach ($sid in $allowedSids) {
        if (@($allows | Where-Object { $_.IdentityReference.Value -eq $sid }).Count -ne 1) {
            throw 'Credential DACL is missing an expected principal.'
        }
    }
}

if ($CheckOnly) {
    Test-PrivateAcl $credentialRoot
    Test-PrivateAcl $credentialPath
} else {
    if (Test-Path -LiteralPath $credentialPath) { throw 'Credential file exists; refusing to replace it.' }
    if (Test-Path -LiteralPath $credentialRoot) {
        Test-PrivateAcl $credentialRoot
    } else {
        [IO.Directory]::CreateDirectory($credentialRoot) | Out-Null
        $directoryAcl = [Security.AccessControl.DirectorySecurity]::new()
        $directoryAcl.SetOwner($userSid)
        $directoryAcl.SetAccessRuleProtection($true, $false)
        foreach ($sid in @($userSid, $systemSid)) {
            $directoryAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
                $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'
            ))
        }
        Set-Acl -LiteralPath $credentialRoot -AclObject $directoryAcl
        Test-PrivateAcl $credentialRoot
    }
    # The empty file inherits a private directory ACL; protect its own DACL before writing secrets.
    [IO.File]::Open($credentialPath, [IO.FileMode]::CreateNew).Dispose()
    $fileAcl = [Security.AccessControl.FileSecurity]::new()
    $fileAcl.SetOwner($userSid)
    $fileAcl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($userSid, $systemSid)) {
        $fileAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow'))
    }
    Set-Acl -LiteralPath $credentialPath -AclObject $fileAcl
    Test-PrivateAcl $credentialPath
    $generated = 1..3 | ForEach-Object {
        [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    }
    $privateValues = [ordered]@{
        PASSWORDS = ConvertTo-Json -InputObject @($generated[0]) -Compress
        ADMIN_PASSWORDS = ConvertTo-Json -InputObject @($generated[1]) -Compress
        JWT_SECRET = $generated[2]
    }
    [IO.File]::WriteAllText($credentialPath, ($privateValues | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Test-PrivateAcl $credentialPath
    $generated = $null
    $privateValues = $null
}
[ordered]@{
    path = $credentialPath
    file_dacl_protected = $true
    directory_dacl_protected = $true
    allowed_principals = @('current_user', 'SYSTEM')
    check_only = [bool]$CheckOnly
} | ConvertTo-Json
