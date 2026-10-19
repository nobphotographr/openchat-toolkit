"""Compile reviewed C# sources; never download code or invoke LINE."""
from pathlib import Path
import os
import subprocess

def main():
    root=Path(__file__).resolve().parent
    framework=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319'
    refs=['System.Web.Extensions.dll','System.Drawing.dll','System.Windows.Forms.dll']+[str(framework/'WPF'/n) for n in ('UIAutomationClient.dll','UIAutomationTypes.dll','WindowsBase.dll')]
    builds=[('native/LineHistory.exe',['native/LineHistoryNavigation.cs','native/HistoryExport.cs','native/ScreenRead.cs']),
            ('posting-native/PostLabLine.exe',['posting-native/'+n for n in ('Navigation.cs','DraftSession.cs','MessageRead.cs','MediaSession.cs','LineDeliveryPolicy.cs')]),
            ('posting-native/ImageCodec.exe',['posting-native/ImageCodec.cs']),
            ('posting-native/ImageMatch.exe',['posting-native/ImageMatch.cs'])]
    for output,sources in builds:
        subprocess.run([str(framework/'csc.exe'),'/nologo','/target:exe','/out:'+str(root/output)]+['/r:'+r for r in refs]+[str(root/s) for s in sources],check=True)

if __name__=='__main__':main()
