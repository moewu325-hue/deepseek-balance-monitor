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
    CHECK(ParseConfig("{}", config) && SerializeConfig(config).find("\"bubble_size\": 108") != std::string::npos);
    CHECK(ParseConfig(R"({"bubble_size":132})", config) && SerializeConfig(config).find("\"bubble_size\": 132") != std::string::npos);
    CHECK(!ParseConfig(R"({"bubble_size":71})", config));
    CHECK(!ParseConfig(R"({"bubble_size":161})", config));
    CHECK(!ParseConfig(R"({"bubble_size":90.5})", config));
    CHECK(!ParseConfig(R"({"bubble_size":true})", config));
    CHECK(!ParseConfig(R"({"bubble_size":999999999999999999})", config));
    CHECK(ParseConfig(R"({"bubble_size":72})", config) && config.bubble_size == 72);
    CHECK(ParseConfig(R"({"bubble_size":160})", config) && config.bubble_size == 160);
    Config resized;
    CHECK(ParseConfig(SerializeConfig(config), resized) && resized.bubble_size == 160);
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
    CHECK(turn.x > 47 && !turn.facing_right);
    CHECK(left.x < turn.x && !left.facing_right);
    CHECK(WhaleAt(8000).x > start.x && WhaleAt(8000).facing_right);
    CHECK(WhaleAt(100).rotation != WhaleAt(500).rotation);
    CHECK(!WhaleAt(11999).jumping);
    CHECK(WhaleAt(12000).jumping && WhaleAt(12000).splash > 0);
    CHECK(WhaleAt(12400).y < WhaleAt(12000).y);
    CHECK(WhaleAt(12800).y == 20);
    CHECK(WhaleAt(13200).y > WhaleAt(12800).y);
    CHECK(!WhaleAt(13600).jumping && WhaleAt(13600).splash > 0);
    CHECK(WhaleAt(13900).splash == 0);
    CHECK(WhaleAt(16000).x == start.x && WhaleAt(16000).y == start.y);
    CHECK(WhaleAt(-1).x == start.x && WhaleAt(-1).y == start.y);
    const auto huge = WhaleAt(std::numeric_limits<int64_t>::max());
    CHECK(std::isfinite(huge.x) && std::isfinite(huge.y) && huge.x >= 18 && huge.x <= 48);
    bool bounded = true;
    for (int64_t elapsed = 0; elapsed <= 32000; elapsed += 7) {
        const auto frame = WhaleAt(elapsed);
        bounded = bounded && frame.x >= 18 && frame.x <= 48 && frame.y >= 20 && frame.y < 39
            && std::isfinite(frame.rotation) && std::abs(frame.rotation) <= 1.5708;
    }
    CHECK(bounded);
    CHECK(WhaleAt(12400).x == WhaleAt(12400 + 16000).x);
    CHECK(WhaleAt(12400).y == WhaleAt(12400 + 16000).y);
    CHECK(WhaleAt(12000).rotation > 0.5);
    CHECK(std::abs(WhaleAt(12800).rotation) < 0.001);
    CHECK(WhaleAt(13599).rotation < -0.5);
    for (int64_t elapsed : {12000, 12200, 12800, 13400, 13599}) {
        const auto frame = WhaleAt(elapsed);
        const double direction = frame.facing_right ? 1 : -1;
        const double speed = std::hypot(frame.vx, frame.vy);
        CHECK(std::abs(direction * std::cos(frame.rotation) - frame.vx / speed) < 0.001);
        CHECK(std::abs(direction * std::sin(frame.rotation) - frame.vy / speed) < 0.001);
        CHECK(frame.vx == -15);
    }
    for (int64_t boundary : {12000, 13600}) {
        const auto before = WhaleAt(boundary - 1), at = WhaleAt(boundary);
        CHECK(std::abs(before.x - at.x) < 0.05 && std::abs(before.y - at.y) < 0.05);
        CHECK(std::abs(before.vx - at.vx) < 0.1 && std::abs(before.vy - at.vy) < 0.2);
        CHECK(std::abs(before.rotation - at.rotation) < 0.02);
    }
    CHECK(PeakIndicator(true).color == 0x007165F8);
    CHECK(PeakIndicator(false).color == 0x0098D64C);
    CHECK(std::wstring(PeakIndicator(true).label) == L"高峰");
    CHECK(std::wstring(PeakIndicator(false).label) == L"非高峰");
    // 弧线跳跃必须持续前进，不得只沿竖直方向起落。
    CHECK(std::abs(WhaleAt(12000).x - 44) < 0.001);
    CHECK(std::abs(WhaleAt(12800).x - 32) < 0.001);
    CHECK(std::abs(WhaleAt(12800).y - 20) < 0.001);
    CHECK(std::abs(WhaleAt(13600).x - 20) < 0.001);
    CHECK(std::abs(WhaleAt(13600).y - 34) < 0.001);
    const double gravity = (WhaleAt(12900).y - 2*WhaleAt(12800).y + WhaleAt(12700).y)/0.01;
    CHECK(std::abs(gravity - 43.75) < 0.001);
    CHECK(std::abs(WhaleAt(11201).rotation - WhaleAt(11199).rotation) < 0.1);
    CHECK(std::abs(WhaleAt(14401).rotation - WhaleAt(14399).rotation) < 0.1);
    std::printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
