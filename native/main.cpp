#include "windows.hpp"
#include <shellapi.h>
#include <cstdio>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR command, int) {
    const std::wstring options = command ? command : L"";
    if (options == L"--version") {
        AttachConsole(ATTACH_PARENT_PROCESS);
        std::puts("DeepSeek Balance Monitor native 2.2.0");
        return 0;
    }
    if (options == L"--check") {
        AttachConsole(ATTACH_PARENT_PROCESS);
        const auto credentials = ds::ReadCredentials();
        for (const auto& credential : credentials) {
            const auto result = ds::FetchBalance(credential.key);
            if (result.http_status == 401) continue;
            const bool verified = result.http_status == 200 || result.http_status == 402;
            std::printf("credential=%s http=%lu balance=%s\n", verified ? "verified" : "unverified",
                        result.http_status, result.ok ? "available" : "unavailable");
            return verified ? 0 : 2;
        }
        std::puts(credentials.empty() ? "credential=unavailable" : "credential=unverified http=401");
        return credentials.empty() ? 1 : 2;
    }
    const bool smoke = options.find(L"--smoke-test=") == 0;
    if (!options.empty() && !smoke) return 2;
    HANDLE mutex = nullptr;
    if (!smoke) {
        mutex = CreateMutexW(nullptr, FALSE, L"Local\\MoeWu.DeepSeekBalanceMonitor");
        if (!mutex) return 1;
        if (GetLastError() == ERROR_ALREADY_EXISTS) {
            CloseHandle(mutex);
            return 0;
        }
    }
    if (!SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)) SetProcessDPIAware();
    const int result = ds::RunApp(instance, options);
    if (mutex) CloseHandle(mutex);
    return result;
}
