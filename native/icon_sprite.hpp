#pragma once
#include "windows.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

namespace ds {
class IconSprite {
    std::vector<uint32_t> source_;
    int width_ = 0, height_ = 0, side_ = 0;
    HDC dc_ = nullptr;
    HBITMAP bitmap_ = nullptr;
    HGDIOBJ previous_ = nullptr;
    uint32_t* pixels_ = nullptr;

    void ResetSurface() {
        GdiFlush();
        if (dc_ && previous_) SelectObject(dc_, previous_);
        if (bitmap_) DeleteObject(bitmap_);
        if (dc_) DeleteDC(dc_);
        dc_ = nullptr; bitmap_ = nullptr; previous_ = nullptr; pixels_ = nullptr; side_ = 0;
    }
    bool Surface(int side) {
        if (side == side_ && pixels_) return true;
        ResetSurface();
        if (side < 1 || side > 768) return false;
        BITMAPINFO info{};
        info.bmiHeader = {sizeof(BITMAPINFOHEADER), side, -side, 1, 32, BI_RGB, 0, 0, 0, 0, 0};
        dc_ = CreateCompatibleDC(nullptr);
        if (!dc_) return false;
        bitmap_ = CreateDIBSection(dc_, &info, DIB_RGB_COLORS, reinterpret_cast<void**>(&pixels_), nullptr, 0);
        if (!bitmap_ || !pixels_) { ResetSurface(); return false; }
        previous_ = SelectObject(dc_, bitmap_);
        if (!previous_ || previous_ == HGDI_ERROR) { previous_ = nullptr; ResetSurface(); return false; }
        side_ = side;
        return true;
    }
    uint32_t Pixel(int x, int y) const {
        return x >= 0 && x < width_ && y >= 0 && y < height_ ? source_[y * width_ + x] : 0;
    }
public:
    IconSprite() = default;
    IconSprite(const IconSprite&) = delete;
    IconSprite& operator=(const IconSprite&) = delete;
    ~IconSprite() { ResetSurface(); }

    bool Load(HICON icon) {
        ICONINFO iconInfo{}; BITMAP dimensions{};
        if (!icon || !GetIconInfo(icon, &iconInfo)) return false;
        const bool sized = iconInfo.hbmColor && GetObjectW(iconInfo.hbmColor, sizeof(dimensions), &dimensions);
        if (iconInfo.hbmColor) DeleteObject(iconInfo.hbmColor);
        if (iconInfo.hbmMask) DeleteObject(iconInfo.hbmMask);
        const int width = dimensions.bmWidth, height = dimensions.bmHeight;
        if (!sized || width < 1 || height < 1 || width > 1024 || height > 1024) return false;
        BITMAPINFO info{};
        info.bmiHeader = {sizeof(BITMAPINFOHEADER), width, -height, 1, 32, BI_RGB, 0, 0, 0, 0, 0};
        HDC dc = CreateCompatibleDC(nullptr);
        uint32_t* bits = nullptr;
        HBITMAP bitmap = dc ? CreateDIBSection(dc, &info, DIB_RGB_COLORS, reinterpret_cast<void**>(&bits), nullptr, 0) : nullptr;
        HGDIOBJ previous = bitmap ? SelectObject(dc, bitmap) : nullptr;
        bool loaded = false;
        if (previous && previous != HGDI_ERROR && bits) {
            std::memset(bits, 0, width * height * sizeof(uint32_t));
            loaded = DrawIconEx(dc, 0, 0, icon, width, height, 0, nullptr, DI_NORMAL);
            GdiFlush();
            if (loaded) { source_.assign(bits, bits + width * height); width_ = width; height_ = height; }
            SelectObject(dc, previous);
        }
        if (bitmap) DeleteObject(bitmap);
        if (dc) DeleteDC(dc);
        return loaded;
    }
    bool Draw(HDC target, int centerX, int centerY, int scale, double rotation, bool facingRight) {
        const int side = MulDiv(48, scale, 96);
        if (source_.empty() || !Surface(side)) return false;
        // 源图头朝左；镜像在采样中完成，AlphaBlend始终使用正宽高。
        GdiFlush();
        const double cosine = std::cos(rotation), sine = std::sin(rotation);
        const double ratio = width_ / (32.0 * scale / 96);
        for (int y = 0; y < side; ++y) for (int x = 0; x < side; ++x) {
            const double dx = x + 0.5 - side / 2.0, dy = y + 0.5 - side / 2.0;
            const double u = (facingRight ? -1 : 1) * (cosine * dx + sine * dy) * ratio + width_ / 2.0 - 0.5;
            const double v = (-sine * dx + cosine * dy) * ratio + height_ / 2.0 - 0.5;
            const int left = static_cast<int>(std::floor(u)), top = static_cast<int>(std::floor(v));
            const double fx = u - left, fy = v - top;
            const uint32_t neighbors[] = {Pixel(left, top), Pixel(left + 1, top), Pixel(left, top + 1), Pixel(left + 1, top + 1)};
            const double weights[] = {(1-fx)*(1-fy), fx*(1-fy), (1-fx)*fy, fx*fy};
            uint32_t result = 0;
            for (int shift = 0; shift < 32; shift += 8) {
                double channel = 0;
                for (int n = 0; n < 4; ++n) channel += ((neighbors[n] >> shift) & 255) * weights[n];
                result |= static_cast<uint32_t>(std::clamp(static_cast<int>(std::lround(channel)), 0, 255)) << shift;
            }
            pixels_[y * side + x] = result;
        }
        const BLENDFUNCTION blend{AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
        return AlphaBlend(target, centerX - side / 2, centerY - side / 2, side, side, dc_, 0, 0, side, side, blend);
    }
};
}
