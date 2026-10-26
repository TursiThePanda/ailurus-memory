; Inno Setup script for the Windows installer.
;
; Deliberately small. The executable is self-contained, so the installer only has to
; put it somewhere, offer a Start-menu entry, and offer to start the tray at login.
; It does NOT create the database or ask for a passphrase -- `ai-memory setup` does
; that, interactively, on first run. An installer that tried would have to hold the
; passphrase, which is the one thing this project refuses to do.

#define AppName "Ailurus Memory"
; Up to 1.1.1 the name was "AI Memory" and there was no AppId, so Inno Setup used the
; NAME as the identity. The identity must not change with the name, or Windows lists a
; second program and an upgrade leaves the old one behind: AppId keeps the old value.
#define OldName "AI Memory"
; Kept in step with version.py by test_packaging -- an installer announcing the
; wrong version is how a user ends up unable to say what they are running.
#define AppVersion "1.2.2"
#define AppExe "ai-memory.exe"

[Setup]
; This script lives in packaging/; paths below are relative to the checkout.
SourceDir=..
AppId={#OldName}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\AI Memory
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=dist
OutputBaseFilename=ai-memory-setup
Compression=lzma2
SolidCompression=yes
; per-user install: no admin prompt, and the data it manages is per-user anyway
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "tray"
Name: "{group}\{#AppName} setup (terminal)"; Filename: "{app}\{#AppExe}"; Parameters: "setup"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "tray"; Tasks: startup

; The Start-menu folder and autostart shortcut of 1.1.x carried the old name; remove
; them so an upgrade does not leave two of each.
[InstallDelete]
Type: filesandordirs; Name: "{userprograms}\{#OldName}"
Type: files; Name: "{userstartup}\{#OldName}.lnk"

[Tasks]
Name: "startup"; Description: "Start the tray when I log in"; GroupDescription: "Autostart:"

; First run must be the wizard, not the tray: with no database the tray can only tell
; the user to go and run setup, so send them straight there.
[Run]
Filename: "{app}\{#AppExe}"; Parameters: "tray"; Description: "Start {#AppName} (sets it up on first run)"; Flags: postinstall shellexec nowait
