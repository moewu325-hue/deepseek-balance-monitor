#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include "core.hpp"

namespace ds {
struct FetchResult {
    bool ok = false;
    unsigned long http_status = 0;
    Snapshot snapshot;
    std::wstring error;
};
struct Credential { std::wstring key, source; };
std::vector<Credential> ReadCredentials();
bool ValidKey(const std::wstring& key);
bool SaveCredential(const std::wstring& key);
FetchResult FetchBalance(const std::wstring& key);
std::wstring AppDataRoot();
bool ReadText(const std::wstring& path, std::string& output, size_t limit = 4 * 1024 * 1024);
bool AtomicWrite(const std::wstring& path, const std::string& content);
bool LoadConfig(const std::wstring& root, Config& config);
bool SaveConfig(const std::wstring& root, const Config& config);
bool LoadHistory(const std::wstring& root, History& history);
bool SaveHistory(const std::wstring& root, const History& history);
bool SetAutostart(bool enabled);
bool AutostartEnabled();
bool FindCodexRect(RECT& rect);
std::string LocalTimestamp();
bool IsPeakHour();
int RunApp(HINSTANCE instance, const std::wstring& options);
}
