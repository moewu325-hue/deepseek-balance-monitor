#pragma once
#include <cmath>
#include <cstdint>

namespace ds {
struct WhaleFrame {
    double x = 24, y = 34, tail = 0, splash = 0, splash_x = 48;
    bool facing_right = true, jumping = false;
};
inline WhaleFrame WhaleAt(int64_t elapsed_ms) {
    const int64_t phase = (elapsed_ms < 0 ? 0 : elapsed_ms) % 16000;
    const double swim = static_cast<double>(phase % 8000) / 4000;
    WhaleFrame frame;
    frame.facing_right = swim < 1;
    frame.x = 24 + 24 * (frame.facing_right ? swim : 2 - swim);
    frame.tail = 2 * std::sin(phase * 6.283185307179586 / 800);
    frame.y += 0.6 * std::sin(phase * 6.283185307179586 / 1600);
    frame.jumping = phase >= 12000 && phase < 13500;
    if (frame.jumping) {
        const double progress = (phase - 12000) / 1500.0;
        frame.y = 34 - 68 * progress * (1 - progress);
    }
    if (phase >= 12000 && phase < 12240) frame.splash = 1 - (phase - 12000) / 240.0;
    if (phase >= 13500 && phase < 13800) {
        frame.splash = 1 - (phase - 13500) / 300.0;
        frame.splash_x = 39;
    }
    return frame;
}
struct PeakStatus { uint32_t color; const wchar_t* label; };
inline PeakStatus PeakIndicator(bool peak) {
    return peak ? PeakStatus{0x007165F8, L"高峰"} : PeakStatus{0x0098D64C, L"非高峰"};
}
}
