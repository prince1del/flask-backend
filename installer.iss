[Setup]
AppName=Centralized DB System
AppVersion=0.1.0
DefaultDirName={pf}\Centralized DB System
DefaultGroupName=Centralized DB System
OutputBaseFilename=CentralizedDBSystemInstaller
Compression=lzma
SolidCompression=yes

[Files]
Source: "dist\Centralized DB System\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:";

[Icons]
Name: "{group}\Centralized DB System"; Filename: "{app}\Centralized DB System.exe"
Name: "{commondesktop}\Centralized DB System"; Filename: "{app}\Centralized DB System.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Centralized DB System.exe"; Description: "Launch Centralized DB System"; Flags: nowait postinstall skipifsilent
