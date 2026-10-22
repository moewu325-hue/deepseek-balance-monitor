#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace ds {
struct WhaleFrame {
    double x = 24, y = 34, vx = 0, vy = 0, rotation = 0, splash = 0, splash_x = 44;
    bool facing_right = true, jumping = false;
};
inline WhaleFrame WhaleAt(int64_t elapsed_ms) {
    const int64_t phase = (elapsed_ms < 0 ? 0 : elapsed_ms) % 16000;
    WhaleFrame frame;
    constexpr double pi = 3.141592653589793;
    if (phase < 11200) {
        const double travel = phase * 3.0 / 11200;
        const int leg = static_cast<int>(travel);
        const double progress = travel - leg, direction = leg % 2 ? -1 : 1;
        frame.x = 36 - direction * 12 * std::cos(pi * progress);
        frame.vx = direction * 12 * pi / (11.2 / 3) * std::sin(pi * progress);
        frame.facing_right = direction > 0;
    } else if (phase < 12000) {
        // 水下转向蓄力，起跳处的位置和速度与抛物线衔接。
        const double progress = (phase - 11200) / 800.0;
        frame.x = 48 - 4 * progress * progress * progress;
        frame.y = 34 + 28 * progress * progress * (1 - progress);
        frame.vx = -15 * progress * progress;
        frame.vy = 35 * (2 * progress - 3 * progress * progress);
        frame.facing_right = false;
    } else if (phase < 13600) {
        const double seconds = (phase - 12000) / 1000.0;
        frame.x = 44 - 15 * seconds;
        frame.y = 34 - 35 * seconds + 21.875 * seconds * seconds;
        frame.vx = -15;
        frame.vy = -35 + 43.75 * seconds;
        frame.facing_right = false;
        frame.jumping = true;
    } else if (phase < 14400) {
        // 入水后减速转向，避免落点跳回普通游动轨迹。
        const double progress = (phase - 13600) / 800.0;
        frame.x = 20 - 12 * progress + 36 * progress * progress - 20 * progress * progress * progress;
        frame.y = 34 + 28 * progress * (1 - progress) * (1 - progress);
        frame.vx = (-12 + 72 * progress - 60 * progress * progress) / 0.8;
        frame.vy = 35 * (1 - 4 * progress + 3 * progress * progress);
        frame.facing_right = frame.vx >= 0;
    }
    if (phase >= 11200 && phase < 14400) {
        const double pitch = std::atan2(frame.vy, std::abs(frame.vx));
        frame.rotation = frame.facing_right ? pitch : -pitch;
        if (!frame.jumping) frame.rotation *= std::min(1.0, std::hypot(frame.vx, frame.vy) / 8);
    } else {
        frame.y += 0.3 * std::sin(phase * 2 * pi / 1600);
        frame.rotation = 0.04 * std::sin(phase * 2 * pi / 1600);
    }
    if (phase >= 12000 && phase < 12240) frame.splash = 1 - (phase - 12000) / 240.0;
    if (phase >= 13600 && phase < 13900) {
        frame.splash = 1 - (phase - 13600) / 300.0;
        frame.splash_x = 20;
    }
    return frame;
}
struct PeakStatus { uint32_t color; const wchar_t* label; };
inline PeakStatus PeakIndicator(bool peak) {
    return peak ? PeakStatus{0x007165F8, L"高峰"} : PeakStatus{0x0098D64C, L"非高峰"};
}
}
