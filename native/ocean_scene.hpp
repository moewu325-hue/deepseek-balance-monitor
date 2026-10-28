#pragma once
#include "windows.hpp"
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace ds {
inline void PaintOceanBackground(HDC dc, int scale, int64_t elapsed) {
    const auto px = [scale](int value) { return MulDiv(value, scale, 96); };
    const auto shape = [&](int x, int y, int width, int height, COLORREF color, bool round = false) {
        HBRUSH brush = CreateSolidBrush(color);
        auto oldBrush = SelectObject(dc, brush), oldPen = SelectObject(dc, GetStockObject(NULL_PEN));
        if (round) Ellipse(dc, px(x), px(y), px(x + width), px(y + height));
        else Rectangle(dc, px(x), px(y), px(x + width), px(y + height));
        SelectObject(dc, oldBrush); SelectObject(dc, oldPen); DeleteObject(brush);
    };
    const auto gradient = [&](int top, int bottom, COLORREF first, COLORREF last) {
        TRIVERTEX vertices[2]{};
        vertices[0] = {0, px(top), static_cast<COLOR16>(GetRValue(first) << 8),
            static_cast<COLOR16>(GetGValue(first) << 8), static_cast<COLOR16>(GetBValue(first) << 8), 0};
        vertices[1] = {px(72), px(bottom), static_cast<COLOR16>(GetRValue(last) << 8),
            static_cast<COLOR16>(GetGValue(last) << 8), static_cast<COLOR16>(GetBValue(last) << 8), 0};
        GRADIENT_RECT rectangle{0, 1};
        GradientFill(dc, vertices, 2, &rectangle, 1, GRADIENT_FILL_RECT_V);
    };
    const int saved = SaveDC(dc);
    IntersectClipRect(dc, 0, 0, px(72), px(43));
    gradient(0, 31, RGB(13, 28, 53), RGB(29, 65, 85));
    gradient(31, 43, RGB(20, 57, 77), RGB(15, 34, 54));
    // 月晕和远景保持低对比，让原图标仍是主体。
    shape(47, 6, 15, 15, RGB(35, 66, 84), true);
    shape(49, 8, 11, 11, RGB(71, 102, 112), true);
    shape(50, 9, 9, 9, RGB(225, 239, 219), true);
    for (const POINT star : {POINT{21, 8}, POINT{35, 5}, POINT{42, 12}, POINT{30, 16}})
        shape(star.x, star.y, 1, 1, RGB(143, 180, 194), true);
    shape(16, 17, 13, 2, RGB(43, 76, 96), true);
    shape(21, 15, 8, 3, RGB(43, 76, 96), true);
    HBRUSH mountain = CreateSolidBrush(RGB(20, 47, 68));
    auto oldBrush = SelectObject(dc, mountain), oldPen = SelectObject(dc, GetStockObject(NULL_PEN));
    POINT left[] = {{px(0),px(31)},{px(12),px(24)},{px(22),px(29)},{px(31),px(31)}};
    POINT right[] = {{px(43),px(31)},{px(58),px(25)},{px(72),px(31)}};
    Polygon(dc, left, 4); Polygon(dc, right, 3);
    SelectObject(dc, oldBrush); SelectObject(dc, oldPen); DeleteObject(mountain);
    // 水下小气泡缓慢上升，周期与主动画整除以免循环闪跳。
    HPEN pen = CreatePen(PS_SOLID, std::max(1, px(1)), RGB(55, 102, 124));
    oldPen = SelectObject(dc, pen); oldBrush = SelectObject(dc, GetStockObject(NULL_BRUSH));
    for (int index = 0; index < 3; ++index) {
        const double progress = ((elapsed + index * 1300) % 4000) / 4000.0;
        const int x = 10 + index * 23 + static_cast<int>(std::lround(std::sin(progress * 6.283185307179586)));
        const int y = static_cast<int>(std::lround(43 - progress * 9));
        Ellipse(dc, px(x), px(y), px(x + 2), px(y + 2));
    }
    SelectObject(dc, oldBrush); SelectObject(dc, oldPen); DeleteObject(pen);
    RestoreDC(dc, saved);
}
inline void PaintOceanSurface(HDC dc, int scale, int64_t elapsed) {
    const auto px = [scale](int value) { return MulDiv(value, scale, 96); };
    const double phase = elapsed * 6.283185307179586 / 4000;
    HPEN pen = CreatePen(PS_SOLID, std::max(1, px(1)), RGB(72, 132, 154));
    auto previous = SelectObject(dc, pen);
    for (int x = 0; x <= 72; ++x) {
        const int y = px(31) + static_cast<int>(std::lround(std::sin(x / 7.0 + phase) * scale / 160.0));
        if (x == 0) MoveToEx(dc, 0, y, nullptr); else LineTo(dc, px(x), y);
    }
    SelectObject(dc, previous); DeleteObject(pen);
    pen = CreatePen(PS_SOLID, std::max(1, px(1)), RGB(50, 94, 115));
    previous = SelectObject(dc, pen);
    for (int index = 0; index < 4; ++index) {
        const int x = 5 + index * 17 + static_cast<int>(std::lround(std::sin(phase + index) * 2));
        const int y = 36 + index % 2 * 4;
        MoveToEx(dc, px(x), px(y), nullptr); LineTo(dc, px(x + 5), px(y));
    }
    SelectObject(dc, previous); DeleteObject(pen);
    pen = CreatePen(PS_SOLID, std::max(1, px(1)), RGB(108, 164, 167));
    previous = SelectObject(dc, pen);
    for (int index = 0; index < 3; ++index) {
        const int drift = static_cast<int>(std::lround(std::sin(phase + index)));
        MoveToEx(dc, px(51 - index + drift), px(33 + index * 3), nullptr);
        LineTo(dc, px(57 + index + drift), px(33 + index * 3));
    }
    SelectObject(dc, previous); DeleteObject(pen);
}
}
