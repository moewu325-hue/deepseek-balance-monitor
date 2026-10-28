#include "windows.hpp"
#include "animation.hpp"
#include "icon_sprite.hpp"
#include "ocean_scene.hpp"
#include <shellapi.h>
#include <algorithm>
#include <cmath>
#include <utility>

namespace ds {
namespace {
constexpr UINT Done = WM_APP + 1;
constexpr UINT SmokeFrame = WM_APP + 2;
constexpr int Refresh=101, Panel=102, Bubble=103, Taskbar=104, Console=105, Key=106, Startup=107, Exit=108;
constexpr int SizeFirst=120;
constexpr int BubbleSizes[]={72,90,108,132,160};
const wchar_t* SizeLabels[]={L"小",L"标准",L"大",L"超大",L"特大"};
const wchar_t* ClassName=L"DeepSeekNativeMonitor";
const wchar_t* KeyClass=L"DeepSeekNativeKey";
struct App {
    HINSTANCE instance=nullptr;
    HWND hwnd=nullptr, dialog=nullptr, edit=nullptr, remember=nullptr;
    HICON icon=nullptr;
    IconSprite whale;
    HANDLE worker=nullptr;
    Config config;
    History history;
    Snapshot snapshot;
    std::wstring root, status=L"等待刷新", refreshed=L"尚未刷新", candidate;
    std::vector<Credential> credentials;
    FetchResult result;
    std::wstring resultKey;
    bool smoke=false, busy=false, closing=false, persist=false;
    bool configWritable=true, historyWritable=true, dragging=false, moved=false;
    POINT dragPoint{}, dragOrigin{};
    int scale=96, smokeMs=1200;
    bool peak=false;
    ULONGLONG animationStarted=0, lastAnchor=0;
    int64_t smokeElapsed=-1;
    int64_t decrease=0;
};
int Px(const App& app,int value) { return MulDiv(value,app.scale,96); }
int DrawScale(const App& app) { return app.config.display_mode=="bubble"?MulDiv(app.scale,app.config.bubble_size,72):app.scale; }
int DrawPx(const App& app,int value) { return MulDiv(value,DrawScale(app),96); }
void Redraw(App& app) { InvalidateRect(app.hwnd,nullptr,FALSE); }
void AnimationTimer(App& app) {
    KillTimer(app.hwnd,2);
    if (app.config.display_mode=="bubble") SetTimer(app.hwnd,2,33,nullptr);
}
void UpdatePeak(App& app) {
    const bool peak=app.smoke && app.smokeElapsed>=0?app.peak:IsPeakHour();
    if (peak!=app.peak) { app.peak=peak; Redraw(app); }
}
void SaveSettings(App& app) {
    if (!app.smoke && app.configWritable && !SaveConfig(app.root,app.config)) {
        app.status=L"配置保存失败"; Redraw(app);
    }
}
void Position(App& app,bool restore=false) {
    const bool panel=app.config.display_mode=="panel", bubble=app.config.display_mode=="bubble";
    const int width=Px(app,panel?340:bubble?app.config.bubble_size:184), height=Px(app,panel?238:bubble?app.config.bubble_size:46);
    RECT work{}, current{};
    SystemParametersInfoW(SPI_GETWORKAREA,0,&work,0);
    GetWindowRect(app.hwnd,&current);
    int x=restore?app.config.window_x:current.left, y=restore?app.config.window_y:current.top;
    if (x < -100000 || y < -100000) { x=work.right-width-Px(app,24); y=work.bottom-height-Px(app,24); }
    if (app.config.display_mode=="taskbar") {
        RECT codex{};
        if (FindCodexRect(codex)) { x=codex.right-width; y=codex.top-Px(app,80)-height; }
    }
    MONITORINFO monitor{}; monitor.cbSize=sizeof(monitor);
    const POINT point{x,y};
    if (GetMonitorInfoW(MonitorFromPoint(point,MONITOR_DEFAULTTONEAREST),&monitor)) work=monitor.rcWork;
    x=std::max(static_cast<int>(work.left),std::min(x,static_cast<int>(work.right)-width));
    y=std::max(static_cast<int>(work.top),std::min(y,static_cast<int>(work.bottom)-height));
    SetWindowPos(app.hwnd,nullptr,x,y,width,height,SWP_NOZORDER|SWP_NOACTIVATE);
    HRGN region=CreateRoundRectRgn(0,0,width+1,height+1,Px(app,bubble?app.config.bubble_size:18),Px(app,bubble?app.config.bubble_size:18));
    if (!SetWindowRgn(app.hwnd,region,TRUE)) DeleteObject(region);
}
void Mode(App& app,const char* mode) {
    app.config.display_mode=mode;
    app.config.collapsed=app.config.display_mode=="bubble";
    Position(app); AnimationTimer(app); SaveSettings(app); Redraw(app);
}
bool SetBubbleSize(App& app,int command) {
    if (command<SizeFirst || command>=SizeFirst+5) return false;
    app.config.bubble_size=BubbleSizes[command-SizeFirst];
    if (app.config.display_mode=="bubble") Position(app);
    SaveSettings(app); Redraw(app); return true;
}
DWORD WINAPI FetchThread(void* context) {
    auto& app=*static_cast<App*>(context);
    app.result={}; app.resultKey.clear();
    for (const auto& credential:app.credentials) {
        app.result=FetchBalance(credential.key);
        if (app.result.http_status!=401) { app.resultKey=credential.key; break; }
    }
    PostMessageW(app.hwnd,Done,0,0);
    return 0;
}
void StartFetch(App& app,const std::wstring& candidate=L"",bool persist=false,bool input=false) {
    if (app.smoke || app.busy || app.closing) return;
    app.credentials=candidate.empty()?ReadCredentials():std::vector<Credential>{{candidate,L"input"}};
    if (!candidate.empty() && !input) {
        for (auto& credential:ReadCredentials())
            if (credential.key!=candidate) app.credentials.push_back(std::move(credential));
    }
    if (app.credentials.empty()) { app.status=L"请从右键菜单输入 API Key"; Redraw(app); return; }
    app.persist=persist; app.busy=true; app.status=L"正在安全刷新…";
    app.worker=CreateThread(nullptr,0,FetchThread,&app,0,nullptr);
    if (!app.worker) { app.busy=false; app.status=L"无法创建刷新线程"; }
    Redraw(app);
}
void FinishFetch(App& app) {
    if (app.worker) { WaitForSingleObject(app.worker,INFINITE); CloseHandle(app.worker); app.worker=nullptr; }
    app.busy=false;
    const bool verified=app.result.http_status==200 || app.result.http_status==402;
    if (verified && app.persist && !SaveCredential(app.resultKey)) app.status=L"凭据有效，但凭据管理器保存失败";
    else if (app.result.ok) app.status=app.result.snapshot.available?L"余额已更新":L"账户余额不可用";
    else if (app.result.http_status==401) app.status=L"凭据已失效，请重新输入 API Key";
    else if (app.result.http_status==402) app.status=L"凭据有效，账户余额不足";
    else app.status=L"刷新失败，保留上次余额";
    if (verified && !app.resultKey.empty()) app.candidate=app.resultKey;
    if (app.result.ok) {
        app.snapshot=std::move(app.result.snapshot);
        app.refreshed=Wide(LocalTimestamp());
        const auto* balance=SelectCurrency(app.snapshot,app.config.currency);
        if (balance) {
            app.decrease=RecordBalance(app.history,*balance,LocalTimestamp());
            if (app.historyWritable && !SaveHistory(app.root,app.history)) app.status+=L" · 历史保存失败";
        }
    }
    for (auto& credential:app.credentials) if (!credential.key.empty()) SecureZeroMemory(&credential.key[0],credential.key.size()*sizeof(wchar_t));
    app.credentials.clear();
    if (!app.resultKey.empty()) SecureZeroMemory(&app.resultKey[0],app.resultKey.size()*sizeof(wchar_t));
    app.resultKey.clear(); app.persist=false;
    if (app.closing) DestroyWindow(app.hwnd); else Redraw(app);
}
void Text(HDC dc,const App& app,const std::wstring& value,int x,int y,int width,int height,int size,COLORREF color,bool bold=false,UINT alignment=DT_LEFT) {
    HFONT font=CreateFontW(-DrawPx(app,size),0,0,0,bold?FW_SEMIBOLD:FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Microsoft YaHei UI");
    auto previous=SelectObject(dc,font); SetTextColor(dc,color);
    RECT rect{DrawPx(app,x),DrawPx(app,y),DrawPx(app,x+width),DrawPx(app,y+height)};
    DrawTextW(dc,value.c_str(),-1,&rect,alignment|DT_SINGLELINE|DT_VCENTER|DT_END_ELLIPSIS|DT_NOPREFIX);
    SelectObject(dc,previous); DeleteObject(font);
}
void Fill(HDC dc,const App& app,int x,int y,int w,int h,COLORREF color,int radius=0) {
    HBRUSH brush=CreateSolidBrush(color);
    auto oldBrush=SelectObject(dc,brush), oldPen=SelectObject(dc,GetStockObject(NULL_PEN));
    if (radius) RoundRect(dc,DrawPx(app,x),DrawPx(app,y),DrawPx(app,x+w),DrawPx(app,y+h),DrawPx(app,radius),DrawPx(app,radius));
    else Rectangle(dc,DrawPx(app,x),DrawPx(app,y),DrawPx(app,x+w),DrawPx(app,y+h));
    SelectObject(dc,oldBrush); SelectObject(dc,oldPen); DeleteObject(brush);
}
void PeakLamp(HDC dc,const App& app,int x,int y,int radius,int textWidth,int size) {
    const auto indicator=PeakIndicator(app.peak);
    Fill(dc,app,x-radius,y-radius,radius*2+1,radius*2+1,indicator.color,radius*2+1);
    Text(dc,app,indicator.label,x+radius+5,y-8,textWidth,16,size,indicator.color);
}
void PaintWhale(HDC dc,App& app,int64_t elapsed) {
    elapsed=std::max<int64_t>(0,elapsed)%16000;
    const auto frame=WhaleAt(elapsed);
    const int scale=DrawScale(app);
    PaintOceanBackground(dc,scale,elapsed);
    const int saved=SaveDC(dc);
    IntersectClipRect(dc,DrawPx(app,6),DrawPx(app,7),DrawPx(app,66),DrawPx(app,43));
    app.whale.Draw(dc,static_cast<int>(std::lround(frame.x*scale/96)),
        static_cast<int>(std::lround(frame.y*scale/96)),scale,frame.rotation,frame.facing_right);
    RestoreDC(dc,saved);
    PaintOceanSurface(dc,scale,elapsed);
    if (frame.splash>0) {
        const int spread=static_cast<int>((1-frame.splash)*8),height=static_cast<int>(frame.splash*7);
        for (int side:{-1,1}) Fill(dc,app,static_cast<int>(frame.splash_x)+side*(4+spread),30-height,2,3,RGB(141,190,255),2);
    }
}
void Paint(App& app,HDC supplied=nullptr) {
    PAINTSTRUCT paint{}; HDC target=supplied?supplied:BeginPaint(app.hwnd,&paint);
    RECT rect{}; GetClientRect(app.hwnd,&rect);
    HDC dc=CreateCompatibleDC(target);
    HBITMAP bitmap=CreateCompatibleBitmap(target,rect.right,rect.bottom);
    auto previous=SelectObject(dc,bitmap); SetBkMode(dc,TRANSPARENT);
    HBRUSH background=CreateSolidBrush(RGB(15,23,42)); FillRect(dc,&rect,background); DeleteObject(background);
    const COLORREF white=RGB(232,239,253),muted=RGB(139,154,180),blue=RGB(91,149,255);
    const Balance* balance=SelectCurrency(app.snapshot,app.config.currency);
    const std::wstring total=balance?DisplayMoney(*balance):L"--";
    int64_t warning=0;
    ParseMoney(app.config.low_balance_warning,warning);
    const COLORREF amountColor=balance && balance->total<warning?RGB(255,179,99):white;
    if (app.config.display_mode=="panel") {
        if (app.icon) DrawIconEx(dc,Px(app,18),Px(app,14),app.icon,Px(app,24),Px(app,24),0,nullptr,DI_NORMAL);
        Text(dc,app,L"DeepSeek 余额",50,12,230,28,14,white,true);
        PeakLamp(dc,app,206,26,3,62,9);
        Text(dc,app,L"−",280,10,24,28,20,muted,false,DT_CENTER);
        Text(dc,app,L"×",308,10,24,28,20,muted,false,DT_CENTER);
        Text(dc,app,L"可用总额",20,50,180,20,11,muted);
        Text(dc,app,total,18,72,300,48,32,amountColor,true);
        const std::wstring grant=balance?Wide(AmountText(balance->granted)):L"--", topup=balance?Wide(AmountText(balance->topped_up)):L"--";
        Text(dc,app,L"赠送  "+grant+L"    充值  "+topup,20,124,300,22,12,muted);
        Text(dc,app,L"今日估算减少  "+Wide(AmountText(app.decrease)),20,150,300,22,12,blue);
        Fill(dc,app,20,180,300,1,RGB(39,52,75));
        Text(dc,app,app.status,20,187,216,20,10,muted);
        Text(dc,app,app.refreshed,20,209,230,17,9,muted);
        Fill(dc,app,252,193,68,30,RGB(35,71,135),10);
        Text(dc,app,app.busy?L"刷新中":L"刷新",252,193,68,30,11,white,true,DT_CENTER);
    } else if (app.config.display_mode=="bubble") {
        const int64_t elapsed=app.smokeElapsed>=0?app.smokeElapsed:
            static_cast<int64_t>((GetTickCount64()-app.animationStarted)%16000);
        PaintWhale(dc,app,elapsed);
        Text(dc,app,balance?Wide(AmountText(balance->total)):L"--",3,44,66,17,10,amountColor,true,DT_CENTER);
        PeakLamp(dc,app,22,64,2,27,7);
    } else {
        if (app.icon) DrawIconEx(dc,Px(app,11),Px(app,11),app.icon,Px(app,24),Px(app,24),0,nullptr,DI_NORMAL);
        Text(dc,app,total,44,3,130,25,15,amountColor,true);
        PeakLamp(dc,app,48,34,3,50,8);
        Text(dc,app,app.busy?L"刷新中…":app.status,112,26,62,16,8,muted);
    }
    BitBlt(target,0,0,rect.right,rect.bottom,dc,0,0,SRCCOPY);
    SelectObject(dc,previous); DeleteObject(bitmap); DeleteDC(dc);
    if (!supplied) EndPaint(app.hwnd,&paint);
}
void RequestClose(App& app) {
    if (app.smoke || MessageBoxW(app.hwnd,L"确定退出 DeepSeek 余额监控？",L"退出",MB_YESNO|MB_ICONQUESTION)==IDYES) {
        app.closing=true;
        if (app.dialog) DestroyWindow(app.dialog);
        if (app.busy) ShowWindow(app.hwnd,SW_HIDE); else DestroyWindow(app.hwnd);
    }
}
LRESULT CALLBACK KeyProc(HWND hwnd,UINT message,WPARAM wparam,LPARAM lparam) {
    auto* app=reinterpret_cast<App*>(GetWindowLongPtrW(hwnd,GWLP_USERDATA));
    if (message==WM_NCCREATE) {
        app=static_cast<App*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams);
        SetWindowLongPtrW(hwnd,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(app));
    }
    if (!app) return DefWindowProcW(hwnd,message,wparam,lparam);
    if (message==WM_COMMAND) {
        if (LOWORD(wparam)==IDOK) {
            wchar_t buffer[513]{}; GetWindowTextW(app->edit,buffer,513);
            std::wstring key(buffer); SecureZeroMemory(buffer,sizeof(buffer));
            if (!ValidKey(key)) {
                MessageBoxW(hwnd,L"请输入有效的 API Key（仅接受可见 ASCII 字符）。",L"API Key",MB_OK|MB_ICONWARNING);
                return 0;
            }
            const bool persist=SendMessageW(app->remember,BM_GETCHECK,0,0)==BST_CHECKED;
            StartFetch(*app,key,persist,true);
            if (!key.empty()) SecureZeroMemory(&key[0],key.size()*sizeof(wchar_t));
            DestroyWindow(hwnd); return 0;
        }
        if (LOWORD(wparam)==IDCANCEL) { DestroyWindow(hwnd); return 0; }
    }
    if (message==WM_CLOSE) { DestroyWindow(hwnd); return 0; }
    if (message==WM_DESTROY) {
        app->dialog=nullptr; EnableWindow(app->hwnd,TRUE); SetForegroundWindow(app->hwnd); return 0;
    }
    return DefWindowProcW(hwnd,message,wparam,lparam);
}
void PromptKey(App& app) {
    if (app.busy || app.smoke) return;
    if (app.dialog) { SetForegroundWindow(app.dialog); return; }
    RECT rect{}; GetWindowRect(app.hwnd,&rect);
    app.dialog=CreateWindowExW(WS_EX_TOOLWINDOW,KeyClass,L"输入 / 更换 API Key",WS_CAPTION|WS_SYSMENU|WS_POPUP,
        rect.left,rect.top,Px(app,370),Px(app,195),app.hwnd,nullptr,app.instance,&app);
    if (!app.dialog) return;
    auto control=[&](const wchar_t* cls,const wchar_t* text,DWORD style,int x,int y,int w,int h,int id) {
        HWND child=CreateWindowExW(std::wstring(cls)==L"EDIT"?WS_EX_CLIENTEDGE:0,cls,text,WS_CHILD|WS_VISIBLE|style,
            Px(app,x),Px(app,y),Px(app,w),Px(app,h),app.dialog,reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),app.instance,nullptr);
        SendMessageW(child,WM_SETFONT,reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)),TRUE);
        return child;
    };
    control(L"STATIC",L"密钥仅发送至 DeepSeek；验证后才可保存。",0,16,12,335,20,0);
    app.edit=control(L"EDIT",L"",ES_PASSWORD|ES_AUTOHSCROLL|WS_TABSTOP,16,40,330,26,10);
    SendMessageW(app.edit,EM_SETLIMITTEXT,512,0);
    app.remember=control(L"BUTTON",L"验证后保存到 Windows 凭据管理器",BS_AUTOCHECKBOX|WS_TABSTOP,16,78,330,23,11);
    control(L"BUTTON",L"验证并使用",BS_DEFPUSHBUTTON|WS_TABSTOP,164,115,105,27,IDOK);
    control(L"BUTTON",L"取消",BS_PUSHBUTTON|WS_TABSTOP,278,115,68,27,IDCANCEL);
    EnableWindow(app.hwnd,FALSE); ShowWindow(app.dialog,SW_SHOW); SetFocus(app.edit);
}
void Menu(App& app,int x,int y) {
    if (app.smoke) return;
    HMENU menu=CreatePopupMenu();
    AppendMenuW(menu,MF_STRING|(app.busy?MF_GRAYED:0),Refresh,L"刷新余额");
    AppendMenuW(menu,MF_SEPARATOR,0,nullptr);
    AppendMenuW(menu,MF_STRING|(app.config.display_mode=="panel"?MF_CHECKED:0),Panel,L"完整面板");
    AppendMenuW(menu,MF_STRING|(app.config.display_mode=="bubble"?MF_CHECKED:0),Bubble,L"鲸鱼气泡");
    AppendMenuW(menu,MF_STRING|(app.config.display_mode=="taskbar"?MF_CHECKED:0),Taskbar,L"任务栏横条");
    HMENU sizes=CreatePopupMenu();
    if (sizes) {
        for (int index=0;index<5;++index)
            AppendMenuW(sizes,MF_STRING|(app.config.bubble_size==BubbleSizes[index]?MF_CHECKED:0),SizeFirst+index,SizeLabels[index]);
        if (!AppendMenuW(menu,MF_POPUP,reinterpret_cast<UINT_PTR>(sizes),L"气泡大小")) DestroyMenu(sizes);
    }
    AppendMenuW(menu,MF_SEPARATOR,0,nullptr);
    AppendMenuW(menu,MF_STRING,Console,L"打开 DeepSeek 控制台");
    AppendMenuW(menu,MF_STRING|(app.busy?MF_GRAYED:0),Key,L"输入 / 更换 API Key…");
    AppendMenuW(menu,MF_STRING|(AutostartEnabled()?MF_CHECKED:0),Startup,L"开机自动启动");
    AppendMenuW(menu,MF_SEPARATOR,0,nullptr);
    AppendMenuW(menu,MF_STRING,Exit,L"退出…");
    SetForegroundWindow(app.hwnd);
    const int choice=TrackPopupMenu(menu,TPM_RETURNCMD|TPM_RIGHTBUTTON,x,y,0,app.hwnd,nullptr);
    DestroyMenu(menu);
    if (SetBubbleSize(app,choice)) return;
    switch (choice) {
    case Refresh: StartFetch(app,app.candidate); break;
    case Panel: Mode(app,"panel"); break;
    case Bubble: Mode(app,"bubble"); break;
    case Taskbar: Mode(app,"taskbar"); break;
    case Console: ShellExecuteW(app.hwnd,L"open",L"https://platform.deepseek.com/usage",nullptr,nullptr,SW_SHOWNORMAL); break;
    case Key: PromptKey(app); break;
    case Startup:
        if (SetAutostart(!AutostartEnabled())) { app.config.auto_start=AutostartEnabled(); SaveSettings(app); }
        else MessageBoxW(app.hwnd,L"自动启动设置失败。",L"DeepSeek",MB_OK|MB_ICONWARNING);
        break;
    case Exit: RequestClose(app); break;
    }
}
LRESULT CALLBACK WindowProc(HWND hwnd,UINT message,WPARAM wparam,LPARAM lparam) {
    auto* app=reinterpret_cast<App*>(GetWindowLongPtrW(hwnd,GWLP_USERDATA));
    if (message==WM_NCCREATE) {
        app=static_cast<App*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams); app->hwnd=hwnd;
        SetWindowLongPtrW(hwnd,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(app));
    }
    if (!app) return DefWindowProcW(hwnd,message,wparam,lparam);
    switch (message) {
    case WM_PAINT: Paint(*app); return 0;
    case WM_PRINTCLIENT: Paint(*app,reinterpret_cast<HDC>(wparam)); return 0;
    case WM_ERASEBKGND: return 1;
    case WM_COMMAND:
        if (!lparam && HIWORD(wparam)==0 && SetBubbleSize(*app,LOWORD(wparam))) return 0;
        break;
    case SmokeFrame:
        if (app->smoke && wparam<=1 && lparam>=0 && lparam<=60000) {
            app->smokeElapsed=lparam; app->peak=wparam!=0; Redraw(*app); return 1;
        }
        return 0;
    case Done: FinishFetch(*app); return 0;
    case WM_CONTEXTMENU: { POINT point{}; GetCursorPos(&point); Menu(*app,point.x,point.y); return 0; }
    case WM_LBUTTONDBLCLK: if (app->config.display_mode!="panel") Mode(*app,"panel"); return 0;
    case WM_LBUTTONDOWN: {
        POINT cursor{}; GetCursorPos(&cursor); RECT rect{}; GetWindowRect(hwnd,&rect);
        app->dragPoint=cursor; app->dragOrigin={rect.left,rect.top}; app->moved=false; app->dragging=true;
        SetCapture(hwnd); return 0;
    }
    case WM_MOUSEMOVE:
        if (app->dragging) {
            POINT point{}; GetCursorPos(&point);
            const int dx=point.x-app->dragPoint.x,dy=point.y-app->dragPoint.y;
            if (std::abs(dx)>3 || std::abs(dy)>3) app->moved=true;
            if (app->moved) SetWindowPos(hwnd,nullptr,app->dragOrigin.x+dx,app->dragOrigin.y+dy,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE);
        }
        return 0;
    case WM_CAPTURECHANGED: app->dragging=false; return 0;
    case WM_LBUTTONUP: {
        if (!app->dragging) return 0;
        ReleaseCapture(); app->dragging=false;
        if (app->moved) {
            RECT rect{}; GetWindowRect(hwnd,&rect); app->config.window_x=rect.left; app->config.window_y=rect.top; SaveSettings(*app);
        } else if (app->config.display_mode=="panel") {
            const int x=MulDiv(static_cast<short>(LOWORD(lparam)),96,app->scale), y=MulDiv(static_cast<short>(HIWORD(lparam)),96,app->scale);
            if (y<42 && x>=306) RequestClose(*app);
            else if (y<42 && x>=278) Mode(*app,"bubble");
            else if (y>=190 && x>=250) StartFetch(*app,app->candidate);
        }
        return 0;
    }
    case WM_TIMER:
        if (wparam==1) StartFetch(*app,app->candidate);
        else if (wparam==2) {
            if (app->config.display_mode=="bubble") Redraw(*app);
        } else if (wparam==3) { KillTimer(hwnd,3); DestroyWindow(hwnd); }
        else if (wparam==4) {
            UpdatePeak(*app);
            const ULONGLONG now=GetTickCount64();
            if (app->config.display_mode=="taskbar" && !app->dragging && now-app->lastAnchor>=2000) {
                Position(*app); app->lastAnchor=now;
            }
        }
        return 0;
    case WM_TIMECHANGE: UpdatePeak(*app); return 0;
    case WM_DISPLAYCHANGE: Position(*app); return 0;
    case WM_DPICHANGED: app->scale=HIWORD(wparam); Position(*app); return 0;
    case WM_CLOSE: RequestClose(*app); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(hwnd,message,wparam,lparam);
}
}
int RunApp(HINSTANCE instance,const std::wstring& options) {
    App app; app.instance=instance; app.smoke=options.find(L"--smoke-test=")==0;
    if (app.smoke) {
        const size_t space=options.find(L' ');
        app.config.display_mode=Utf8(options.substr(13,space==std::wstring::npos?space:space-13));
        if (app.config.display_mode!="panel" && app.config.display_mode!="bubble" && app.config.display_mode!="taskbar") return 2;
        if (space!=std::wstring::npos) {
            const std::wstring duration=options.substr(space+1);
            if (duration.find(L"--smoke-ms=")!=0) return 2;
            const std::wstring digits=duration.substr(11);
            if (digits.empty() || digits.size()>5 || digits.find_first_not_of(L"0123456789")!=std::wstring::npos) return 2;
            app.smokeMs=0;
            for (wchar_t digit:digits) app.smokeMs=app.smokeMs*10+(digit-L'0');
            if (app.smokeMs<100 || app.smokeMs>10000) return 2;
        }
        app.snapshot={true,{{"CNY",123450000,20000000,103450000}}}; app.decrease=1250000;
        app.status=L"离线演示 · 余额已更新"; app.refreshed=L"2026-10-21 12:00:00";
    } else {
        app.root=AppDataRoot(); app.configWritable=LoadConfig(app.root,app.config); app.historyWritable=LoadHistory(app.root,app.history);
        if (app.config.collapsed && app.config.display_mode=="panel") app.config.display_mode="bubble";
        if (!app.configWritable) app.status=L"配置未载入；原文件保留";
    }
    HDC screen=GetDC(nullptr); app.scale=GetDeviceCaps(screen,LOGPIXELSX); ReleaseDC(nullptr,screen);
    app.icon=LoadIconW(instance,MAKEINTRESOURCEW(1));
    HICON source=static_cast<HICON>(LoadImageW(instance,MAKEINTRESOURCEW(1),IMAGE_ICON,225,225,0));
    const bool iconLoaded=app.whale.Load(source);
    if (source) DestroyIcon(source);
    if (!iconLoaded) return 1;
    WNDCLASSW cls{}; cls.style=CS_DBLCLKS; cls.lpfnWndProc=WindowProc; cls.hInstance=instance;
    cls.hCursor=LoadCursorW(nullptr,IDC_ARROW); cls.hIcon=app.icon; cls.lpszClassName=ClassName;
    if (!RegisterClassW(&cls)) return 1;
    cls.lpfnWndProc=KeyProc; cls.lpszClassName=KeyClass; RegisterClassW(&cls);
    HWND hwnd=CreateWindowExW(WS_EX_TOOLWINDOW|WS_EX_LAYERED,ClassName,L"DeepSeek Balance Monitor",WS_POPUP,0,0,1,1,nullptr,nullptr,instance,&app);
    if (!hwnd) return 1;
    SetLayeredWindowAttributes(hwnd,0,static_cast<BYTE>(std::max(0.3,std::min(1.0,app.config.opacity))*255),LWA_ALPHA);
    app.animationStarted=app.lastAnchor=GetTickCount64(); app.peak=IsPeakHour();
    Position(app,true); ShowWindow(hwnd,SW_SHOWNOACTIVATE); UpdateWindow(hwnd);
    AnimationTimer(app); SetTimer(hwnd,4,1000,nullptr);
    if (app.smoke) SetTimer(hwnd,3,app.smokeMs,nullptr);
    else { SetTimer(hwnd,1,static_cast<UINT>(app.config.refresh_interval)*1000,nullptr); StartFetch(app); }
    MSG message{};
    while (GetMessageW(&message,nullptr,0,0)>0) {
        if (!app.dialog || !IsDialogMessageW(app.dialog,&message)) { TranslateMessage(&message); DispatchMessageW(&message); }
    }
    if (app.worker) { WaitForSingleObject(app.worker,INFINITE); CloseHandle(app.worker); }
    if (!app.candidate.empty()) SecureZeroMemory(&app.candidate[0],app.candidate.size()*sizeof(wchar_t));
    return 0;
}
}
