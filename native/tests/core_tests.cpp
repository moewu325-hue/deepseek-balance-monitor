#include "../core.hpp"
#include "../animation.hpp"
#include <cmath>
#include <cstdio>
#include <limits>
#include <string>
static int failures = 0, checks = 0;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; std::printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
int main() {
    using namespace ds;
    int64_t money = 0;
    CHECK(ParseMoney("110.000001", money) && money == 110000001);
    CHECK(ParseMoney("-0.01", money) && money == -10000);
    CHECK(ParseMoney("+1", money) && money == MoneyScale);
    for (const char* bad : {"", "nan", "1e3", "1.", ".1", " 1", "0.0000001", "1000000000001", "--2"}) CHECK(!ParseMoney(bad, money));
    CHECK(AmountText(1234567) == "1.23");
    CHECK(AmountText(-1235000) == "-1.24");
    CHECK(AmountText(1, 6) == "0.000001");
    Snapshot snapshot;
    const std::string entry = R"({"currency":"CNY","total_balance":"110.00","granted_balance":"10.00","topped_up_balance":"100.00"})";
    CHECK(ParseSnapshot("{\"is_available\":true,\"balance_infos\":[" + entry + "]}", snapshot));
    CHECK(SelectCurrency(snapshot, "CNY") && SelectCurrency(snapshot, "USD"));
    CHECK(!ParseSnapshot("{\"is_available\":true,\"balance_infos\":[" + entry + "," + entry + "]}", snapshot));
    CHECK(!ParseSnapshot(R"({"is_available":true,"balance_infos":[]})", snapshot));
    CHECK(!ParseSnapshot(R"({"is_available":"yes","balance_infos":[]})", snapshot));
    CHECK(!ParseSnapshot("[", snapshot));
    CHECK(!ParseSnapshot(std::string(70000, ' '), snapshot));
    Snapshot multi{true, {{"CNY",1,0,1},{"USD",2,0,2}}};
    CHECK(SelectCurrency(multi, "EUR") == nullptr);
    CHECK(SelectCurrency(multi, "USD") && SelectCurrency(multi, "USD")->total == 2);
    Config config;
    CHECK(ParseConfig(R"({"currency":"USD","display_mode":"taskbar","refresh_interval":30,"api_key":"ignored"})", config));
    CHECK(config.currency == "USD" && config.display_mode == "taskbar" && config.refresh_interval == 30);
    CHECK(SerializeConfig(config).find("api_key") == std::string::npos);
    CHECK(!ParseConfig(R"({"refresh_interval":true})", config));
    CHECK(!ParseConfig(R"({"refresh_interval":9})", config));
    CHECK(!ParseConfig(R"({"display_mode":"bad"})", config));
    CHECK(!ParseConfig(R"({"window_x":100001})", config));
    CHECK(ParseConfig(R"({"collapsed":true})", config) && config.display_mode == "bubble");
    CHECK(!ParseConfig(std::string(40, '[') + std::string(40, ']'), config));
    History history;
    CHECK(ParseHistory(R"({"schema_version":2,"baselines":{"CNY":{"date":"2026-10-21","total_balance":"10.00"}},"records":[]})", history));
    CHECK(RecordBalance(history, {"CNY",9000000,0,9000000}, "2026-10-21T12:00:00+08:00") == 1000000);
    CHECK(history.records.size() == 1);
    RecordBalance(history, {"CNY",9000000,0,9000000}, "2026-10-21T12:00:01+08:00");
    CHECK(history.records.size() == 1);
    CHECK(RecordBalance(history, {"USD",5000000,0,5000000}, "2026-10-21T12:00:02+08:00") == 0);
    CHECK(history.baselines.size() == 2);
    CHECK(RecordBalance(history, {"CNY",11000000,0,11000000}, "2026-10-21T12:01:00+08:00") == 0);
    CHECK(RecordBalance(history, {"CNY",8000000,0,8000000}, "2026-10-22T01:00:00+08:00") == 0);
    History copy;
    CHECK(ParseHistory(SerializeHistory(history), copy) && copy.records.size() == history.records.size());
    CHECK(!ParseHistory(R"({"schema_version":1})", copy));
    CHECK(!ParseHistory(R"({"schema_version":2,"baselines":{},"records":[{"time":"bad","currency":"CNY","total_balance":"1"}]})", copy));
    CHECK(!ParseHistory(R"({"schema_version":2,"baselines":{},"records":[{"time":"2026-10-21T12:00:00---","currency":"CNY","total_balance":"1"}]})", copy));
    CHECK(Wide(Utf8(L"中文 ¥")) == L"中文 ¥");
    const auto start = WhaleAt(0), right = WhaleAt(2000), turn = WhaleAt(4000), left = WhaleAt(6000);
    CHECK(start.x == 24 && start.facing_right);
    CHECK(right.x > start.x && right.facing_right);
    CHECK(turn.x == 48 && !turn.facing_right);
    CHECK(left.x < turn.x && !left.facing_right);
    CHECK(WhaleAt(8000).x == start.x && WhaleAt(8000).facing_right);
    CHECK(WhaleAt(100).tail != WhaleAt(500).tail);
    CHECK(!WhaleAt(11999).jumping);
    CHECK(WhaleAt(12000).jumping && WhaleAt(12000).splash > 0);
    CHECK(WhaleAt(12375).y < WhaleAt(12000).y);
    CHECK(WhaleAt(12750).y == 17);
    CHECK(WhaleAt(13125).y > WhaleAt(12750).y);
    CHECK(!WhaleAt(13500).jumping && WhaleAt(13500).splash > 0);
    CHECK(WhaleAt(13800).splash == 0);
    CHECK(WhaleAt(16000).x == start.x && WhaleAt(16000).y == start.y);
    CHECK(WhaleAt(-1).x == start.x && WhaleAt(-1).y == start.y);
    const auto huge = WhaleAt(std::numeric_limits<int64_t>::max());
    CHECK(std::isfinite(huge.x) && std::isfinite(huge.y) && huge.x >= 24 && huge.x <= 48);
    bool bounded = true;
    for (int64_t elapsed = 0; elapsed <= 32000; elapsed += 7) {
        const auto frame = WhaleAt(elapsed);
        bounded = bounded && frame.x >= 24 && frame.x <= 48 && frame.y >= 17 && frame.y < 35;
    }
    CHECK(bounded);
    CHECK(WhaleAt(12375).x == WhaleAt(12375 + 16000).x);
    CHECK(WhaleAt(12375).y == WhaleAt(12375 + 16000).y);
    CHECK(PeakIndicator(true).color == 0x007165F8);
    CHECK(PeakIndicator(false).color == 0x0098D64C);
    CHECK(std::wstring(PeakIndicator(true).label) == L"高峰");
    CHECK(std::wstring(PeakIndicator(false).label) == L"非高峰");
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
