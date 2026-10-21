#include "../windows.hpp"
#include <cstdio>
#include <string>
static int failures=0, checks=0;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; std::printf("FAIL line %d: %s\n",__LINE__,#x); } } while (0)
int wmain(int argc,wchar_t** argv) {
    using namespace ds;
    if (argc==3 && std::wstring(argv[1])==L"--write") return AtomicWrite(argv[2],std::string(128*1024*1024,'x')) ? 0 : 1;
    CHECK(ValidKey(L"test-key"));
    CHECK(!ValidKey(L"")); CHECK(!ValidKey(L"a\r\nInjected: yes")); CHECK(!ValidKey(std::wstring(1281,L'x')));
    wchar_t temporary[MAX_PATH], executable[32768]; GetTempPathW(MAX_PATH,temporary); GetModuleFileNameW(nullptr,executable,32768);
    std::wstring root=std::wstring(temporary)+L"DeepSeekNativeTests-"+std::to_wstring(GetCurrentProcessId()); CreateDirectoryW(root.c_str(),nullptr);
    std::wstring path=root+L"\\配置.json";
    CHECK(AtomicWrite(path,"before")); std::string content;
    CHECK(ReadText(path,content) && content=="before");
    CHECK(!ReadText(path,content,2));
    HANDLE locked=CreateFileW(path.c_str(),GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,0,nullptr);
    CHECK(!AtomicWrite(path,"blocked")); CloseHandle(locked);
    CHECK(ReadText(path,content) && content=="before");
    std::wstring command=L"\""+std::wstring(executable)+L"\" --write \""+path+L"\"";
    STARTUPINFOW startup{}; startup.cb=sizeof(startup); PROCESS_INFORMATION process{};
    CHECK(CreateProcessW(nullptr,command.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW,nullptr,nullptr,&startup,&process));
    bool interrupted=false;
    for (int i=0;i<5000 && process.hProcess && WaitForSingleObject(process.hProcess,0)==WAIT_TIMEOUT;++i) {
        WIN32_FIND_DATAW entry{}; HANDLE search=FindFirstFileW((path+L".*.tmp").c_str(),&entry);
        if (search!=INVALID_HANDLE_VALUE) { FindClose(search); TerminateProcess(process.hProcess,99); interrupted=true; break; }
        Sleep(1);
    }
    if (process.hProcess) { WaitForSingleObject(process.hProcess,5000); CloseHandle(process.hThread); CloseHandle(process.hProcess); }
    CHECK(interrupted);
    CHECK(ReadText(path,content) && content=="before");
    CHECK(AtomicWrite(path,"recovered")); CHECK(ReadText(path,content) && content=="recovered");
    CHECK(AtomicWrite(path,"\xef\xbb\xbf" "UTF8")); CHECK(ReadText(path,content) && content=="UTF8");
    Config config; CHECK(LoadConfig(root,config)); config.display_mode="taskbar";
    CHECK(SaveConfig(root,config)); Config copy; CHECK(LoadConfig(root,copy) && copy.display_mode=="taskbar");
    CHECK(AtomicWrite(root+L"\\config.json","{")); CHECK(!LoadConfig(root,copy));
    WIN32_FIND_DATAW entry{}; HANDLE search=FindFirstFileW((root+L"\\*").c_str(),&entry);
    if (search!=INVALID_HANDLE_VALUE) { do { if (!(entry.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)) DeleteFileW((root+L"\\"+entry.cFileName).c_str()); } while (FindNextFileW(search,&entry)); FindClose(search); }
    RemoveDirectoryW(root.c_str());
    unsigned long status=0;
    if(argc==2 && std::wstring(argv[1])==L"--live") {
        auto result=FetchBalance(L"synthetic-invalid-key"); status=result.http_status;
        CHECK(!result.ok && result.http_status==401);
    }
    std::printf("%d checks, %d failures; live HTTP %lu; crash injection %s\n",checks,failures,status,interrupted?"passed":"failed");
    return failures?1:0;
}
