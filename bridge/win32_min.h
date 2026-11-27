/* The few Win32 declarations the bridge needs, so it builds with clang and lld
   alone: no Windows SDK and no C runtime. 32-bit x86 only. */
#pragma once

typedef void *HANDLE;
typedef unsigned long DWORD;
typedef unsigned int UINT;
typedef unsigned short WORD;
typedef unsigned char BYTE;
typedef int BOOL;
typedef short SHORT;
typedef unsigned long long ULONGLONG;
#define WINAPI __stdcall
#define IMPORT __declspec(dllimport)

typedef struct { long x, y; } POINT;
typedef struct { long left, top, right, bottom; } RECT;
typedef struct { DWORD low, high; } FILETIME;
typedef struct {
    DWORD biSize; long biWidth, biHeight; WORD biPlanes, biBitCount;
    DWORD biCompression, biSizeImage; long biXPelsPerMeter, biYPelsPerMeter;
    DWORD biClrUsed, biClrImportant;
} BITMAPINFOHEADER;
typedef BOOL (WINAPI *WNDENUMPROC)(HANDLE, long);
typedef DWORD (WINAPI *THREADPROC)(void *);

/* kernel32 */
IMPORT HANDLE WINAPI CreateThread(void *, DWORD, THREADPROC, void *, DWORD, DWORD *);
IMPORT HANDLE WINAPI CreateNamedPipeA(const char *, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD, void *);
IMPORT BOOL WINAPI ConnectNamedPipe(HANDLE, void *);
IMPORT BOOL WINAPI DisconnectNamedPipe(HANDLE);
IMPORT BOOL WINAPI PeekNamedPipe(HANDLE, void *, DWORD, DWORD *, DWORD *, DWORD *);
IMPORT BOOL WINAPI ReadFile(HANDLE, void *, DWORD, DWORD *, void *);
IMPORT BOOL WINAPI WriteFile(HANDLE, const void *, DWORD, DWORD *, void *);
IMPORT BOOL WINAPI FlushFileBuffers(HANDLE);
IMPORT BOOL WINAPI CloseHandle(HANDLE);
IMPORT DWORD WINAPI GetLastError(void);
IMPORT DWORD WINAPI GetCurrentProcessId(void);
IMPORT HANDLE WINAPI GetModuleHandleA(const char *);
IMPORT void *WINAPI GetProcAddress(HANDLE, const char *);
IMPORT BOOL WINAPI VirtualProtect(void *, DWORD, DWORD, DWORD *);
IMPORT void *WINAPI VirtualAlloc(void *, DWORD, DWORD, DWORD);
IMPORT BOOL WINAPI VirtualFree(void *, DWORD, DWORD);
IMPORT BOOL WINAPI FlushInstructionCache(HANDLE, const void *, DWORD);
IMPORT HANDLE WINAPI GetCurrentProcess(void);
IMPORT DWORD WINAPI GetTickCount(void);
IMPORT void WINAPI Sleep(DWORD);
IMPORT HANDLE WINAPI OpenThread(DWORD, BOOL, DWORD);
IMPORT DWORD WINAPI SuspendThread(HANDLE);
IMPORT DWORD WINAPI ResumeThread(HANDLE);
IMPORT BOOL WINAPI GetThreadTimes(HANDLE, FILETIME *, FILETIME *, FILETIME *, FILETIME *);
IMPORT BOOL WINAPI DisableThreadLibraryCalls(HANDLE);

/* user32 */
__declspec(dllimport) int __cdecl wsprintfA(char *, const char *, ...);
IMPORT BOOL WINAPI EnumWindows(WNDENUMPROC, long);
IMPORT BOOL WINAPI EnumChildWindows(HANDLE, WNDENUMPROC, long);
IMPORT DWORD WINAPI GetWindowThreadProcessId(HANDLE, DWORD *);
IMPORT BOOL WINAPI IsWindow(HANDLE);
IMPORT BOOL WINAPI IsWindowVisible(HANDLE);
IMPORT BOOL WINAPI IsWindowEnabled(HANDLE);
IMPORT BOOL WINAPI IsIconic(HANDLE);
IMPORT int WINAPI GetClassNameA(HANDLE, char *, int);
IMPORT int WINAPI InternalGetWindowText(HANDLE, unsigned short *, int);
IMPORT BOOL WINAPI GetWindowRect(HANDLE, RECT *);
IMPORT BOOL WINAPI GetClientRect(HANDLE, RECT *);
IMPORT BOOL WINAPI ClientToScreen(HANDLE, POINT *);
IMPORT BOOL WINAPI ScreenToClient(HANDLE, POINT *);
IMPORT HANDLE WINAPI GetParent(HANDLE);
IMPORT HANDLE WINAPI ChildWindowFromPointEx(HANDLE, POINT, UINT);
IMPORT BOOL WINAPI PostMessageA(HANDLE, UINT, UINT, long);
IMPORT HANDLE WINAPI GetDC(HANDLE);
IMPORT int WINAPI ReleaseDC(HANDLE, HANDLE);

/* gdi32 */
IMPORT HANDLE WINAPI CreateCompatibleDC(HANDLE);
IMPORT HANDLE WINAPI CreateDIBSection(HANDLE, const BITMAPINFOHEADER *, UINT, void **, HANDLE, DWORD);
IMPORT HANDLE WINAPI SelectObject(HANDLE, HANDLE);
IMPORT BOOL WINAPI BitBlt(HANDLE, int, int, int, int, HANDLE, int, int, DWORD);
IMPORT BOOL WINAPI GdiFlush(void);
IMPORT BOOL WINAPI DeleteObject(HANDLE);
IMPORT BOOL WINAPI DeleteDC(HANDLE);
