#pragma once

// Qt 6.8–6.10 calls the ARM __yield intrinsic without declaring it. New Apple
// Clang versions require its ACLE declaration before any Qt headers are read.
#if defined(__APPLE__) && defined(__aarch64__)
#include <arm_acle.h>
#endif
