#include "windows.hpp"
#include <winhttp.h>
#include <wincred.h>
#include <shlobj.h>
#include <algorithm>
#include <cstdio>

namespace ds {
struct Internet {
    HINTERNET handle=nullptr;
    explicit Internet(HINTERNET h):handle(h){}
    ~Internet(){ if(handle) WinHttpCloseHandle(handle); }
    operator HINTERNET() const { return handle; }
};
static std::wstring Environment(const wchar_t* name) {
    DWORD size=GetEnvironmentVariableW(name,nullptr,0); if(!size || size>32768) return {};
    std::wstring result(size,L'\0'); DWORD count=GetEnvironmentVariableW(name,result.data(),size);
    if(!count || count>=size) return {}; result.resize(count); return result;
}
static std::wstring Trim(std::wstring text) {
    size_t first=text.find_first_not_of(L" \r\n\t"), last=text.find_last_not_of(L" \r\n\t");
    return first==std::wstring::npos ? std::wstring() : text.substr(first,last-first+1);
}
bool ValidKey(const std::wstring& key) {
    if(key.empty() || key.size()>1280) return false;
    for(wchar_t c:key) if(c<33 || c>126) return false;
    return true;
}
std::vector<Credential> ReadCredentials() {
    std::vector<Credential> result; auto key=Trim(Environment(L"DEEPSEEK_API_KEY"));
    if(ValidKey(key)) result.push_back({key,L"环境变量"});
    PCREDENTIALW credential=nullptr;
    if(CredReadW(L"DeepSeekBalanceMonitor.ApiKey",CRED_TYPE_GENERIC,0,&credential)) {
        if(credential->CredentialBlob && credential->CredentialBlobSize>0 && credential->CredentialBlobSize<=2560 && credential->CredentialBlobSize%sizeof(wchar_t)==0) {
            std::wstring stored(reinterpret_cast<const wchar_t*>(credential->CredentialBlob),credential->CredentialBlobSize/sizeof(wchar_t));
            stored=Trim(stored);
            if(ValidKey(stored) && (result.empty() || result.front().key!=stored)) result.push_back({stored,L"Windows 凭据管理器"});
            if(!stored.empty()) SecureZeroMemory(stored.data(),stored.size()*sizeof(wchar_t));
        }
        if(credential->CredentialBlob) SecureZeroMemory(credential->CredentialBlob,credential->CredentialBlobSize);
        CredFree(credential);
    }
    if(!key.empty()) SecureZeroMemory(key.data(),key.size()*sizeof(wchar_t));
    return result;
}
bool SaveCredential(const std::wstring& key) {
    if(!ValidKey(key)) return false;
    CREDENTIALW credential{}; credential.Type=CRED_TYPE_GENERIC;
    credential.TargetName=const_cast<wchar_t*>(L"DeepSeekBalanceMonitor.ApiKey");
    credential.UserName=const_cast<wchar_t*>(L"DeepSeekBalanceMonitor");
    credential.CredentialBlobSize=static_cast<DWORD>(key.size()*sizeof(wchar_t));
    credential.CredentialBlob=reinterpret_cast<LPBYTE>(const_cast<wchar_t*>(key.data()));
    credential.Persist=CRED_PERSIST_LOCAL_MACHINE;
    return CredWriteW(&credential,0)!=FALSE;
}
FetchResult FetchBalance(const std::wstring& key) {
    FetchResult result;
    if(!ValidKey(key)) { result.error=L"API Key 格式无效"; return result; }
    Internet session(WinHttpOpen(L"DeepSeekBalanceMonitor/2.0",WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,WINHTTP_NO_PROXY_NAME,WINHTTP_NO_PROXY_BYPASS,0));
    if(!session.handle || !WinHttpSetTimeouts(session,5000,5000,6000,6000)) { result.error=L"无法初始化安全连接"; return result; }
    Internet connection(WinHttpConnect(session,L"api.deepseek.com",INTERNET_DEFAULT_HTTPS_PORT,0));
    if(!connection.handle) { result.error=L"无法连接余额服务"; return result; }
    Internet request(WinHttpOpenRequest(connection,L"GET",L"/user/balance",nullptr,WINHTTP_NO_REFERER,WINHTTP_DEFAULT_ACCEPT_TYPES,WINHTTP_FLAG_SECURE));
    DWORD disable=WINHTTP_DISABLE_REDIRECTS|WINHTTP_DISABLE_COOKIES|WINHTTP_DISABLE_AUTHENTICATION;
    if(!request.handle || !WinHttpSetOption(request,WINHTTP_OPTION_DISABLE_FEATURE,&disable,sizeof(disable))) { result.error=L"无法设置安全请求"; return result; }
    std::wstring headers=L"Authorization: Bearer "+key+L"\r\nAccept: application/json\r\n";
    BOOL added=WinHttpAddRequestHeaders(request,headers.c_str(),static_cast<DWORD>(headers.size()),WINHTTP_ADDREQ_FLAG_ADD|WINHTTP_ADDREQ_FLAG_REPLACE);
    SecureZeroMemory(headers.data(),headers.size()*sizeof(wchar_t));
    ULONGLONG started=GetTickCount64();
    if(!added || !WinHttpSendRequest(request,WINHTTP_NO_ADDITIONAL_HEADERS,0,WINHTTP_NO_REQUEST_DATA,0,0,0) || !WinHttpReceiveResponse(request,nullptr)) { result.error=L"网络连接失败，请稍后重试"; return result; }
    DWORD bytes=sizeof(result.http_status);
    if(!WinHttpQueryHeaders(request,WINHTTP_QUERY_STATUS_CODE|WINHTTP_QUERY_FLAG_NUMBER,WINHTTP_HEADER_NAME_BY_INDEX,&result.http_status,&bytes,WINHTTP_NO_HEADER_INDEX)) { result.error=L"无法读取 HTTP 状态"; return result; }
    if(result.http_status!=200) {
        result.error=result.http_status==401 ? L"API Key 已失效，请重新输入" : L"余额服务返回 HTTP "+std::to_wstring(result.http_status);
        return result;
    }
    std::string body; char buffer[4096];
    while(true) {
        DWORD count=0;
        if(GetTickCount64()-started>20000 || !WinHttpReadData(request,buffer,sizeof(buffer),&count)) { result.error=L"读取余额超时或连接中断"; return result; }
        if(!count) break;
        if(body.size()+count>65536) { result.error=L"余额响应超过大小限制"; return result; }
        body.append(buffer,count);
    }
    if(!ParseSnapshot(body,result.snapshot)) { result.error=L"余额响应格式无效"; return result; }
    result.ok=true; return result;
}
std::wstring AppDataRoot() {
    wchar_t path[MAX_PATH]; if(FAILED(SHGetFolderPathW(nullptr,CSIDL_LOCAL_APPDATA,nullptr,SHGFP_TYPE_CURRENT,path))) return {};
    return std::wstring(path)+L"\\DeepSeekBalanceMonitor";
}
bool ReadText(const std::wstring& path,std::string& output,size_t limit) {
    HANDLE file=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file==INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{}; bool ok=GetFileSizeEx(file,&size) && size.QuadPart>=0 && static_cast<uint64_t>(size.QuadPart)<=limit;
    std::string content; if(ok) { content.resize(static_cast<size_t>(size.QuadPart)); DWORD count=0; ok=ReadFile(file,content.data(),static_cast<DWORD>(content.size()),&count,nullptr) && count==content.size(); }
    CloseHandle(file); if(!ok) return false;
    if(content.size()>=3 && content.compare(0,3,"\xef\xbb\xbf")==0) content.erase(0,3);
    output=std::move(content); return true;
}
bool AtomicWrite(const std::wstring& path,const std::string& content) {
    // 同卷临时文件落盘后替换，崩溃时旧文件仍完整。
    static LONG serial=0;
    std::wstring temporary=path+L"."+std::to_wstring(GetCurrentProcessId())+L"."+std::to_wstring(InterlockedIncrement(&serial))+L".tmp";
    HANDLE file=CreateFileW(temporary.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_TEMPORARY|FILE_FLAG_WRITE_THROUGH,nullptr);
    if(file==INVALID_HANDLE_VALUE) return false;
    DWORD written=0; bool ok=content.size()<=MAXDWORD && WriteFile(file,content.data(),static_cast<DWORD>(content.size()),&written,nullptr) && written==content.size() && FlushFileBuffers(file);
    if(!CloseHandle(file)) ok=false;
    if(ok) ok=MoveFileExW(temporary.c_str(),path.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)!=FALSE;
    if(!ok) DeleteFileW(temporary.c_str());
    if(ok) {
        std::string verified;
        std::string expected=content;
        if(expected.size()>=3 && expected.compare(0,3,"\xef\xbb\xbf")==0) expected.erase(0,3);
        ok=ReadText(path,verified,content.size()+3) && verified==expected;
    }
    return ok;
}
static bool Missing(const std::wstring& path) {
    DWORD attributes=GetFileAttributesW(path.c_str());
    return attributes==INVALID_FILE_ATTRIBUTES && (GetLastError()==ERROR_FILE_NOT_FOUND || GetLastError()==ERROR_PATH_NOT_FOUND);
}
static bool EnsureRoot(const std::wstring& root) {
    if(root.empty()) return false;
    int code=SHCreateDirectoryExW(nullptr,root.c_str(),nullptr);
    return code==ERROR_SUCCESS || code==ERROR_ALREADY_EXISTS || code==ERROR_FILE_EXISTS;
}
bool LoadConfig(const std::wstring& root,Config& config) {
    if(root.empty()) return false;
    auto path=root+L"\\config.json"; if(Missing(path)) { config=Config{}; return true; }
    std::string text; return ReadText(path,text,65536) && ParseConfig(text,config);
}
bool SaveConfig(const std::wstring& root,const Config& config) { return EnsureRoot(root) && AtomicWrite(root+L"\\config.json",SerializeConfig(config)); }
bool LoadHistory(const std::wstring& root,History& history) {
    if(root.empty()) return false;
    auto path=root+L"\\usage_log.json"; if(Missing(path)) { history=History{}; return true; }
    std::string text; return ReadText(path,text) && ParseHistory(text,history);
}
bool SaveHistory(const std::wstring& root,const History& history) { return EnsureRoot(root) && AtomicWrite(root+L"\\usage_log.json",SerializeHistory(history)); }
static std::wstring StartupPath() {
    wchar_t folder[MAX_PATH]; if(FAILED(SHGetFolderPathW(nullptr,CSIDL_STARTUP,nullptr,SHGFP_TYPE_CURRENT,folder))) return {};
    return std::wstring(folder)+L"\\DeepSeekBalanceMonitor.vbs";
}
bool AutostartEnabled() { auto path=StartupPath(); return !path.empty() && GetFileAttributesW(path.c_str())!=INVALID_FILE_ATTRIBUTES; }
bool SetAutostart(bool enabled) {
    auto path=StartupPath(); if(path.empty()) return false;
    if(!enabled) return DeleteFileW(path.c_str()) || GetLastError()==ERROR_FILE_NOT_FOUND;
    wchar_t executable[32768]; DWORD length=GetModuleFileNameW(nullptr,executable,32768); if(!length || length>=32768) return false;
    std::wstring location(executable,length),escaped;
    for(wchar_t c:location) { if(c==L'\r'||c==L'\n'||c==L'"') return false; escaped+=c; }
    std::wstring script=L"\ufeffSet shell = CreateObject(\"WScript.Shell\")\r\nshell.Run \"\"\""+escaped+L"\"\"\", 0, False\r\n";
    // VBS 使用 UTF-16，与旧版启动目录项一致。
    std::string bytes(reinterpret_cast<const char*>(script.data()),script.size()*sizeof(wchar_t));
    return AtomicWrite(path,bytes);
}
bool FindCodexRect(RECT& rect) {
    struct Search { RECT rect{}; bool found=false; } search;
    EnumWindows([](HWND window,LPARAM context)->BOOL {
        if(!IsWindowVisible(window)) return TRUE;
        DWORD pid=0; GetWindowThreadProcessId(window,&pid);
        HANDLE process=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid); if(!process) return TRUE;
        wchar_t executable[32768]; DWORD size=32768;
        bool match=QueryFullProcessImageNameW(process,0,executable,&size) && std::wstring(executable,size).find(L"CodexUsageBar")!=std::wstring::npos;
        CloseHandle(process);
        auto* search=reinterpret_cast<Search*>(context);
        if(match && GetWindowRect(window,&search->rect) && search->rect.right>search->rect.left) { search->found=true; return FALSE; }
        return TRUE;
    },reinterpret_cast<LPARAM>(&search));
    if(search.found) rect=search.rect; return search.found;
}
std::string LocalTimestamp() {
    SYSTEMTIME time; GetLocalTime(&time); TIME_ZONE_INFORMATION zone{}; DWORD state=GetTimeZoneInformation(&zone);
    int offset=-(zone.Bias+(state==TIME_ZONE_ID_DAYLIGHT?zone.DaylightBias:zone.StandardBias));
    char text[48]; std::snprintf(text,sizeof(text),"%04u-%02u-%02uT%02u:%02u:%02u%c%02d:%02d",time.wYear,time.wMonth,time.wDay,time.wHour,time.wMinute,time.wSecond,offset<0?'-':'+',std::abs(offset)/60,std::abs(offset)%60);
    return text;
}
bool IsPeakHour() {
    FILETIME now; GetSystemTimeAsFileTime(&now); ULARGE_INTEGER value{}; value.LowPart=now.dwLowDateTime; value.HighPart=now.dwHighDateTime;
    value.QuadPart+=8ULL*60*60*10000000; now.dwLowDateTime=value.LowPart; now.dwHighDateTime=value.HighPart; SYSTEMTIME shanghai{}; FileTimeToSystemTime(&now,&shanghai);
    if(shanghai.wDayOfWeek==0 || shanghai.wDayOfWeek==6) return false;
    return (shanghai.wHour>=9 && shanghai.wHour<12) || (shanghai.wHour>=14 && shanghai.wHour<18);
}
}
