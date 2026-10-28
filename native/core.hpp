#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace ds {
constexpr int64_t MoneyScale = 1000000;
struct Balance {
    std::string currency;
    int64_t total = 0, granted = 0, topped_up = 0;
};
struct Snapshot { bool available = false; std::vector<Balance> balances; };
struct Config {
    int refresh_interval = 60;
    int bubble_size = 108;
    int window_x = -100001, window_y = -100001;
    double opacity = 0.9;
    std::string low_balance_warning = "10.00", currency = "CNY", snap_edge;
    bool collapsed = false, auto_start = false;
    std::string display_mode = "panel";
};
struct Baseline { std::string date; int64_t total = 0; };
struct Record { std::string time, currency; int64_t total = 0; };
struct History { std::map<std::string, Baseline> baselines; std::vector<Record> records; };
bool ParseMoney(const std::string& text, int64_t& amount);
std::string AmountText(int64_t amount, int decimals = 2);
std::wstring DisplayMoney(const Balance& balance);
bool ParseSnapshot(const std::string& text, Snapshot& output);
const Balance* SelectCurrency(const Snapshot& snapshot, const std::string& preferred);
bool ParseConfig(const std::string& text, Config& output);
std::string SerializeConfig(const Config& config);
bool ParseHistory(const std::string& text, History& output);
std::string SerializeHistory(const History& history);
int64_t RecordBalance(History& history, const Balance& balance, const std::string& timestamp);
std::wstring Wide(const std::string& utf8);
std::string Utf8(const std::wstring& wide);
}
