// Bounded stdin/stdout protocol. Holds the same native mutex for the full session.
// Default --draft-session has no permission to press Enter. Explicit future test
// sends use --test-send-session, with durable intent supplied by posting.Posts.
using System;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Automation;

public partial class LineWindowPrep {
    [StructLayout(LayoutKind.Sequential)] struct KEYINPUT {public ushort vk,scan;public uint flags,time;public UIntPtr extra;}
    [StructLayout(LayoutKind.Explicit)] struct SENDUNION {
        [FieldOffset(0)] public KEYINPUT keyboard;
        [FieldOffset(0)] public MOUSEINPUT mouse;
    }
    [StructLayout(LayoutKind.Sequential)] struct SENDINPUT {public uint type;public SENDUNION value;}
    [DllImport("user32.dll",EntryPoint="SendInput")] static extern uint SendKeys(uint count,SENDINPUT[] inputs,int size);
    class Request {public string action {get;set;} public string message {get;set;} public string business_date {get;set;} public string delivery_date {get;set;}}
    static string LF(string text){return (text??"").Replace("\r\n","\n");}
    static AutomationElement DraftEdit(IntPtr h){
        var edits=Elements(Root(h,Room),ControlType.Edit);Require(edits.Count==1,"room_input_not_unique");return edits[0];
    }
    static string Instance(IntPtr h){uint pid;GetWindowThreadProcessId(h,out pid);return pid+":"+h.ToInt64()+":"+Process.GetProcessById((int)pid).StartTime.ToUniversalTime().Ticks;}
    static void SetDraft(AutomationElement edit,string text){object p;Require(edit.TryGetCurrentPattern(ValuePattern.Pattern,out p),"draft_not_writable");((ValuePattern)p).SetValue(text);}
    static int DraftSession(bool allowSend){
        var json=new JavaScriptSerializer(){MaxJsonLength=20000};
        IntPtr hwnd=IntPtr.Zero;string bound=null,body=null,day=null;
        bool prepared=false,invoked=false;var age=new Stopwatch();
        Console.WriteLine(json.Serialize(new {status="session_ready",send_enabled=allowSend,room=Room}));
        Console.Out.Flush();
        string line;
        while((line=Console.ReadLine())!=null){
            try{
                Require(line.Length<=20000,"request_too_large");var r=json.Deserialize<Request>(line);
                Require(r!=null,"invalid_request");
                if(r.action=="preflight"){
                    Require(body==null,"session_already_bound");
                    Require(!String.IsNullOrEmpty(r.message)&&r.message.Length<=4000&&r.message.All(c=>!Char.IsControl(c)||c=='\n'||c=='\r'),"invalid_body");
                    DateTime parsed;Require(DateTime.TryParseExact(r.business_date,"yyyy-MM-dd",CultureInfo.InvariantCulture,DateTimeStyles.None,out parsed),"invalid_date");
                    if(Production){
                        Require(LineDeliveryPolicy.ValidDates(r.business_date,r.delivery_date,DateTimeOffset.UtcNow),"stale_date");
                        Require(LineDeliveryPolicy.InWindow(DateTimeOffset.UtcNow),"outside_delivery_window");
                    }else Require(DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(9)).ToString("yyyy-MM-dd")==r.business_date,"stale_date");
                    hwnd=Find(Room);Require(hwnd!=IntPtr.Zero,"test_room_not_open");Guard(hwnd,Room,true);EmptyDraft(hwnd);
                    bound=Instance(hwnd);body=LF(r.message);day=Production?r.delivery_date:r.business_date;age.Start();
                }else{
                    Require(body!=null&&Instance(hwnd)==bound,"window_instance_changed");
                    Guard(hwnd,Room,true);
                    if(r.action=="prepare"){
                        if(Production)Require(LineDeliveryPolicy.InWindow(DateTimeOffset.UtcNow),"outside_delivery_window");
                        Require(!prepared&&!invoked&&age.Elapsed.TotalSeconds<=30,"prepare_not_allowed");EmptyDraft(hwnd);
                        var edit=DraftEdit(hwnd);edit.SetFocus();
                        Require(Automation.Compare(AutomationElement.FocusedElement,edit),"draft_focus_not_confirmed");
                        Guard(hwnd,Room,true);Require(Automation.Compare(DraftEdit(hwnd),edit),"draft_element_changed");
                        // Mark ownership before input; an interrupted SetValue is uncertain.
                        prepared=true;SetDraft(edit,body);Require(LF(Value(DraftEdit(hwnd)))==body,"prepared_body_mismatch");
                    }else if(r.action=="verify"||r.action=="invoke"){
                        Require(prepared&&!invoked&&age.Elapsed.TotalSeconds<=30,"prepared_input_expired");
                        Require(DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(9)).ToString("yyyy-MM-dd")==day,"stale_date");
                        var edit=DraftEdit(hwnd);Require(LF(Value(edit))==body,"prepared_body_mismatch");
                        Require(Automation.Compare(AutomationElement.FocusedElement,edit),"draft_focus_changed");
                        if(r.action=="invoke"){
                            Require(allowSend,"send_disabled_in_draft_session");Guard(hwnd,Room,true);
                            Require(LineDeliveryPolicy.InWindow(DateTimeOffset.UtcNow),"outside_delivery_window");
                            if(Production)Require(LineDeliveryPolicy.InWindow(DateTimeOffset.UtcNow),"outside_delivery_window");
                            // One attempt per process. posting.Posts persists intent BEFORE
                            // calling this; a lost response always becomes needs_review.
                            invoked=true;
                            var input=new[]{new SENDINPUT{type=1,value=new SENDUNION{keyboard=new KEYINPUT{vk=13}}},
                                new SENDINPUT{type=1,value=new SENDUNION{keyboard=new KEYINPUT{vk=13,flags=2}}}};
                            uint accepted=SendKeys(2,input,Marshal.SizeOf(typeof(SENDINPUT)));
                            // If only key-down was accepted, release the key without
                            // generating another key-down or another send attempt.
                            if(accepted==1)SendKeys(1,new[]{input[1]},Marshal.SizeOf(typeof(SENDINPUT)));
                            Require(accepted==2,"send_input_outcome_unknown");
                        }
                    }else if(r.action=="clear"){
                        Require(prepared&&!invoked,"clear_not_allowed");var edit=DraftEdit(hwnd);
                        Require(LF(Value(edit))==body,"changed_draft_preserved");SetDraft(edit,"");
                        Require(String.IsNullOrEmpty(Value(edit)),"clear_unconfirmed");prepared=false;
                    }else if(r.action!="observe")throw new InvalidOperationException("unsupported_action");
                }
                Console.WriteLine(json.Serialize(new {status="ok",action=r.action,room=Room,window_instance=bound,
                    input_empty=String.IsNullOrEmpty(Value(DraftEdit(hwnd))),draft_exact=body!=null&&LF(Value(DraftEdit(hwnd)))==body,
                    send_invocations=invoked?1:0,send_enabled=allowSend,observed_at=DateTimeOffset.UtcNow.ToString("o")}));
            }catch(Exception error){
                Console.WriteLine(json.Serialize(new {status="stopped",stage=stage,reason=error is InvalidOperationException?error.Message:error.GetType().Name,
                    send_invocations=invoked?1:0,automatic_retry=false}));
                Console.Out.Flush();return 2;
            }
            Console.Out.Flush();
        }
        return 0;
    }
}
