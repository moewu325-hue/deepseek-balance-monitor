#include "core.hpp"
#include "third_party/json.hpp"
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <algorithm>
#include <cstdio>
#include <limits>
#include <set>

namespace ds {
using Json = nlohmann::json;
static bool ParseJson(const std::string& text, Json& value, size_t limit = 4 * 1024 * 1024) {
    if (text.size() > limit) return false;
    // 先限制嵌套，再交给标准 JSON 解析器验证编码与语法。
    int depth = 0; bool quoted = false, escape = false;
    for (unsigned char c : text) {
        if (quoted) { if (escape) escape = false; else if (c == '\\') escape = true; else if (c == '"') quoted = false; }
        else if (c == '"') quoted = true;
        else if (c == '{' || c == '[') { if (++depth > 32) return false; }
        else if (c == '}' || c == ']') { if (--depth < 0) return false; }
    }
    value = Json::parse(text, nullptr, false);
    return !value.is_discarded() && value.is_object();
}
static bool Currency(const std::string& text, std::string& output) {
    if (text.empty() || text.size() > 12) return false;
    output.clear();
    for (unsigned char c : text) {
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))) return false;
        output += static_cast<char>(c >= 'a' && c <= 'z' ? c - 32 : c);
    }
    return true;
}
bool ParseMoney(const std::string& text, int64_t& amount) {
    if (text.empty() || text.size() > 32) return false;
    size_t i = 0; bool negative = false;
    if (text[i] == '-' || text[i] == '+') { negative = text[i] == '-'; if (++i == text.size()) return false; }
    int64_t whole = 0, fraction = 0, scale = MoneyScale; size_t digits = 0;
    while (i < text.size() && text[i] >= '0' && text[i] <= '9') {
        whole = whole * 10 + text[i++] - '0';
        if (whole > 1000000000000LL) return false;
        ++digits;
    }
    if (!digits) return false;
    if (i < text.size() && text[i] == '.') {
        size_t start = ++i;
        while (i < text.size() && text[i] >= '0' && text[i] <= '9') {
            if (scale == 1) return false;
            scale /= 10; fraction += (text[i++] - '0') * scale;
        }
        if (i == start) return false;
    }
    if (i != text.size() || (whole == 1000000000000LL && fraction)) return false;
    amount = (whole * MoneyScale + fraction) * (negative ? -1 : 1);
    return true;
}
std::string AmountText(int64_t amount, int decimals) {
    decimals = std::clamp(decimals, 0, 6);
    uint64_t absolute = amount < 0 ? static_cast<uint64_t>(-(amount + 1)) + 1 : static_cast<uint64_t>(amount);
    uint64_t divisor = 1; for (int i = decimals; i < 6; ++i) divisor *= 10;
    uint64_t rounded = absolute / divisor + (divisor > 1 && absolute % divisor >= divisor / 2 ? 1 : 0);
    uint64_t fraction_scale = 1; for (int i = 0; i < decimals; ++i) fraction_scale *= 10;
    std::string result = (amount < 0 && rounded ? "-" : "") + std::to_string(rounded / fraction_scale);
    if (decimals) { std::string fraction = std::to_string(rounded % fraction_scale); result += "." + std::string(decimals - fraction.size(), '0') + fraction; }
    return result;
}
std::wstring Wide(const std::string& text) {
    if (text.empty() || text.size() > 4 * 1024 * 1024) return {};
    int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (!size) return {};
    std::wstring result(size, L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), result.data(), size);
    return result;
}
std::string Utf8(const std::wstring& text) {
    if (text.empty() || text.size() > 2 * 1024 * 1024) return {};
    int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    if (!size) return {};
    std::string result(size, '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), result.data(), size, nullptr, nullptr);
    return result;
}
std::wstring DisplayMoney(const Balance& balance) {
    return (balance.currency == "CNY" ? L"¥" : balance.currency == "USD" ? L"$" : Wide(balance.currency) + L" ") + Wide(AmountText(balance.total));
}
static bool StringField(const Json& object, const char* key, std::string& value) {
    auto it = object.find(key); if (it == object.end() || !it->is_string()) return false;
    value = it->get<std::string>(); return true;
}
static bool MoneyField(const Json& object, const char* key, int64_t& amount) {
    std::string text; return StringField(object, key, text) && ParseMoney(text, amount);
}
bool ParseSnapshot(const std::string& text, Snapshot& output) {
    Json value; if (!ParseJson(text, value, 65536)) return false;
    auto available = value.find("is_available"), infos = value.find("balance_infos");
    if (available == value.end() || !available->is_boolean() || infos == value.end() || !infos->is_array() || infos->empty() || infos->size() > 32) return false;
    Snapshot parsed; parsed.available = available->get<bool>(); std::set<std::string> seen;
    for (const auto& row : *infos) {
        Balance balance; std::string currency;
        if (!row.is_object() || !StringField(row, "currency", currency) || !Currency(currency, balance.currency) || !seen.insert(balance.currency).second ||
            !MoneyField(row, "total_balance", balance.total) || !MoneyField(row, "granted_balance", balance.granted) || !MoneyField(row, "topped_up_balance", balance.topped_up)) return false;
        parsed.balances.push_back(balance);
    }
    output = std::move(parsed); return true;
}
const Balance* SelectCurrency(const Snapshot& snapshot, const std::string& preferred) {
    std::string currency; if (!Currency(preferred, currency)) return nullptr;
    for (const auto& balance : snapshot.balances) if (balance.currency == currency) return &balance;
    return snapshot.balances.size() == 1 ? &snapshot.balances.front() : nullptr;
}
static bool IntOption(const Json& value, const char* key, int minimum, int maximum, int& destination, bool nullable = false) {
    auto it = value.find(key); if (it == value.end()) return true;
    if (nullable && it->is_null()) return true;
    if (!it->is_number_integer() || *it < minimum || *it > maximum) return false;
    destination = it->get<int>(); return true;
}
static bool BoolOption(const Json& value, const char* key, bool& destination) {
    auto it = value.find(key); if (it == value.end()) return true;
    if (!it->is_boolean()) return false;
    destination = it->get<bool>(); return true;
}
bool ParseConfig(const std::string& text, Config& output) {
    Json value; if (!ParseJson(text, value, 65536)) return false;
    Config config;
    if (!IntOption(value,"refresh_interval",10,86400,config.refresh_interval) ||
        !IntOption(value,"window_x",-100000,100000,config.window_x,true) || !IntOption(value,"window_y",-100000,100000,config.window_y,true) ||
        !BoolOption(value,"collapsed",config.collapsed) || !BoolOption(value,"auto_start",config.auto_start)) return false;
    auto opacity = value.find("opacity");
    if (opacity != value.end()) { if (!opacity->is_number() || *opacity < 0.2 || *opacity > 1.0) return false; config.opacity = opacity->get<double>(); }
    if (value.contains("currency")) { std::string currency; if (!StringField(value,"currency",currency) || !Currency(currency,config.currency)) return false; }
    if (value.contains("low_balance_warning")) { int64_t warning; if (!StringField(value,"low_balance_warning",config.low_balance_warning) || !ParseMoney(config.low_balance_warning,warning) || warning < 0) return false; }
    if (value.contains("snap_edge") && !value["snap_edge"].is_null()) { if (!StringField(value,"snap_edge",config.snap_edge) || config.snap_edge.size() > 32) return false; }
    if (value.contains("display_mode")) { if (!StringField(value,"display_mode",config.display_mode) || (config.display_mode != "panel" && config.display_mode != "bubble" && config.display_mode != "taskbar")) return false; }
    else if (config.collapsed) config.display_mode = "bubble";
    config.collapsed = config.display_mode == "bubble";
    output = config; return true;
}
std::string SerializeConfig(const Config& config) {
    Json value = {{"refresh_interval",config.refresh_interval},{"opacity",config.opacity},{"low_balance_warning",config.low_balance_warning},{"currency",config.currency},
        {"collapsed",config.display_mode == "bubble"},{"auto_start",config.auto_start},{"display_mode",config.display_mode}};
    value["window_x"] = config.window_x == -100001 ? Json(nullptr) : Json(config.window_x);
    value["window_y"] = config.window_y == -100001 ? Json(nullptr) : Json(config.window_y);
    value["snap_edge"] = config.snap_edge.empty() ? Json(nullptr) : Json(config.snap_edge);
    return value.dump(2) + "\n";
}
static bool Date(const std::string& text) {
    if (text.size() != 10 || text[4] != '-' || text[7] != '-') return false;
    for (size_t i = 0; i < text.size(); ++i) if (i != 4 && i != 7 && (text[i] < '0' || text[i] > '9')) return false;
    int y = 0,m = 0,d = 0; if (std::sscanf(text.c_str(),"%4d-%2d-%2d",&y,&m,&d) != 3 || y < 1 || m < 1 || m > 12) return false;
    const int days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    int maximum = days[m-1] + (m == 2 && (y % 400 == 0 || (y % 4 == 0 && y % 100 != 0)) ? 1 : 0);
    return d >= 1 && d <= maximum;
}
static bool Timestamp(const std::string& text) {
    if (text.size() < 19 || text.size() > 40 || !Date(text.substr(0,10)) || text[10] != 'T' || text[13] != ':' || text[16] != ':') return false;
    for (size_t i = 11; i < 19; ++i) if (i != 13 && i != 16 && (text[i] < '0' || text[i] > '9')) return false;
    int h=0,m=0,s=0; if (std::sscanf(text.c_str()+11,"%2d:%2d:%2d",&h,&m,&s) != 3 || h>23 || m>59 || s>59) return false;
    size_t i=19;
    if (i<text.size() && text[i]=='.') {
        size_t first=++i;
        while(i<text.size() && text[i]>='0' && text[i]<='9') ++i;
        if(i==first || i-first>6) return false;
    }
    if(i==text.size()) return true;
    if(text[i]=='Z') return i+1==text.size();
    if((text[i]!='+' && text[i]!='-') || text.size()-i!=6 || text[i+3]!=':') return false;
    for(size_t j : {i+1,i+2,i+4,i+5}) if(text[j]<'0' || text[j]>'9') return false;
    return (text[i+1]-'0')*10+text[i+2]-'0'<=23 && (text[i+4]-'0')*10+text[i+5]-'0'<=59;
}
bool ParseHistory(const std::string& text, History& output) {
    Json value; if (!ParseJson(text, value)) return false;
    if (!value.contains("schema_version") || !value["schema_version"].is_number_integer() || value["schema_version"] != 2 ||
        !value.contains("baselines") || !value["baselines"].is_object() || value["baselines"].size() > 32 ||
        !value.contains("records") || !value["records"].is_array() || value["records"].size() > 10000) return false;
    History history;
    for (auto it = value["baselines"].begin(); it != value["baselines"].end(); ++it) {
        std::string currency; Baseline baseline;
        if (!Currency(it.key(),currency) || history.baselines.count(currency) || !it.value().is_object() || !StringField(it.value(),"date",baseline.date) ||
            !Date(baseline.date) || !MoneyField(it.value(),"total_balance",baseline.total)) return false;
        history.baselines[currency] = baseline;
    }
    for (const auto& row : value["records"]) {
        Record record; std::string currency;
        if (!row.is_object() || !StringField(row,"time",record.time) || !Timestamp(record.time) || !StringField(row,"currency",currency) ||
            !Currency(currency,record.currency) || !MoneyField(row,"total_balance",record.total)) return false;
        history.records.push_back(record);
    }
    output = std::move(history); return true;
}
std::string SerializeHistory(const History& history) {
    Json value = {{"schema_version",2},{"baselines",Json::object()},{"records",Json::array()}};
    for (const auto& pair : history.baselines) value["baselines"][pair.first] = {{"date",pair.second.date},{"total_balance",AmountText(pair.second.total,6)}};
    for (const auto& record : history.records) value["records"].push_back({{"time",record.time},{"currency",record.currency},{"total_balance",AmountText(record.total,6)}});
    return value.dump(2) + "\n";
}
int64_t RecordBalance(History& history, const Balance& balance, const std::string& timestamp) {
    if (!Timestamp(timestamp)) return 0;
    std::string date = timestamp.substr(0,10);
    auto it = history.baselines.find(balance.currency);
    if (it == history.baselines.end() || it->second.date != date) history.baselines[balance.currency] = {date,balance.total};
    if (history.records.empty() || history.records.back().currency != balance.currency || history.records.back().total != balance.total) history.records.push_back({timestamp,balance.currency,balance.total});
    if (history.records.size() > 10000) history.records.erase(history.records.begin(),history.records.begin() + (history.records.size()-10000));
    return std::max<int64_t>(0,history.baselines[balance.currency].total - balance.total);
}
}
