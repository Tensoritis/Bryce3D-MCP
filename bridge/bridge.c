/*
 * bryce3d_bridge.dll - runs inside Bryce 3D and gives the MCP server:
 *
 *   - a virtual mouse and keyboard that only Bryce sees,
 *   - Bryce's "unsaved changes" flag,
 *   - activity counters that show whether Bryce is rendering,
 *   - the list of Bryce's windows and pictures of them.
 *
 * It talks over a local named pipe, one text line per request. Until a client
 * switches the virtual input on, every hooked call goes straight to Windows.
 * Only the exact Bryce 3D build it was written against is touched.
 */
#include "win32_min.h"

#define BRIDGE_VERSION 1
#define EXE_BASE 0x400000u
#define EXE_STAMP 0x347b8bf5u      /* Bryce 3D.exe, 25 November 1997 */
#define AXIOM_STAMP 0x347922d3u    /* Axiom.dll,    24 November 1997 */
#define DOCUMENT_POINTER 0x635018u /* -> the open document */
#define DOCUMENT_FLAGS 0x4d8u      /* bit 0: unsaved changes */
#define IDLE_RELEASE_MS 30000u

#define VK_COUNT 256
#define WM_KEYDOWN 0x100
#define WM_KEYUP 0x101
#define WM_CHAR 0x102
#define WM_MOUSEMOVE 0x200
#define WM_LBUTTONDOWN 0x201
#define WM_LBUTTONUP 0x202
#define WM_RBUTTONDOWN 0x204
#define WM_RBUTTONUP 0x205
#define SRCCOPY 0x00CC0020u
#define PAGE_EXECUTE_READWRITE 0x40

int _fltused = 0;

void *memset(void *to, int value, unsigned int count) {
    volatile BYTE *p = to;
    while (count--) *p++ = (BYTE)value;
    return to;
}
void *memcpy(void *to, const void *from, unsigned int count) {
    volatile BYTE *p = to; const BYTE *q = from;
    while (count--) *p++ = *q++;
    return to;
}

static int same(const char *a, const char *b) {
    while (*a && *a == *b) a++, b++;
    return *a == *b;
}
static unsigned int length(const char *s) {
    unsigned int n = 0;
    while (s[n]) n++;
    return n;
}

/* ------------------------------------------------------------ virtual input */
static volatile int g_virtual;            /* 1: Bryce sees our mouse and keys */
static volatile long g_x, g_y;            /* screen coordinates */
static volatile BYTE g_keys[VK_COUNT];    /* 1: held, including buttons */
static volatile DWORD g_last_request;

/* activity counters */
static volatile DWORD g_peeks, g_checks, g_blits, g_cursor_reads, g_key_reads;

typedef BOOL (WINAPI *GetCursorPos_t)(POINT *);
typedef BOOL (WINAPI *SetCursorPos_t)(int, int);
typedef SHORT (WINAPI *KeyState_t)(int);
typedef HANDLE (WINAPI *GetForegroundWindow_t)(void);
typedef HANDLE (WINAPI *WindowFromPoint_t)(long, long);
typedef BOOL (WINAPI *PeekMessageA_t)(void *, HANDLE, UINT, UINT, UINT);
typedef int (WINAPI *StretchDIBits_t)(HANDLE, int, int, int, int, int, int, int, int,
                                      const void *, const void *, UINT, DWORD);
typedef BOOL (WINAPI *BitBlt_t)(HANDLE, int, int, int, int, HANDLE, int, int, DWORD);
typedef char (__fastcall *Method_t)(void *self, void *unused);

static GetCursorPos_t real_GetCursorPos;
static SetCursorPos_t real_SetCursorPos;
static KeyState_t real_GetAsyncKeyState, real_GetKeyState;
static GetForegroundWindow_t real_GetForegroundWindow;
static WindowFromPoint_t real_WindowFromPoint;
static PeekMessageA_t real_PeekMessageA;
static StretchDIBits_t real_StretchDIBits;
static BitBlt_t real_BitBlt;
static Method_t real_IsFrontProcess, real_InterruptionInQueue;

/* -------------------------------------------------------------- our windows */
struct Search { DWORD pid; long x, y; HANDLE found; HANDLE first; };

static BOOL WINAPI find_at_point(HANDLE window, long parameter) {
    struct Search *search = (struct Search *)parameter;
    DWORD pid = 0;
    RECT r;
    GetWindowThreadProcessId(window, &pid);
    if (pid != search->pid || !IsWindowVisible(window) || IsIconic(window)) return 1;
    if (!GetWindowRect(window, &r)) return 1;
    if (!search->first && r.right - r.left > 200) search->first = window;
    if (search->x < r.left || search->x >= r.right || search->y < r.top || search->y >= r.bottom)
        return 1;
    if (!IsWindowEnabled(window)) return 1;
    search->found = window;
    return 0;                        /* EnumWindows goes front to back */
}

/* Bryce's own window under a screen point, whatever other programs cover it. */
static HANDLE window_at(long x, long y) {
    struct Search search = {GetCurrentProcessId(), x, y, 0, 0};
    HANDLE window;
    int depth;
    EnumWindows(find_at_point, (long)&search);
    window = search.found;
    for (depth = 0; window && depth < 16; depth++) {
        POINT p = {x, y};
        HANDLE child;
        ScreenToClient(window, &p);
        child = ChildWindowFromPointEx(window, p, 0x0001 | 0x0002); /* skip hidden, disabled */
        if (!child || child == window) break;
        window = child;
    }
    return window;
}

static HANDLE front_window(void) {
    struct Search search = {GetCurrentProcessId(), -32000, -32000, 0, 0};
    EnumWindows(find_at_point, (long)&search);
    return search.first;
}

/* -------------------------------------------------------------------- hooks */
static BOOL WINAPI hook_GetCursorPos(POINT *point) {
    g_cursor_reads++;
    if (!g_virtual) return real_GetCursorPos(point);
    point->x = g_x;
    point->y = g_y;
    return 1;
}

static BOOL WINAPI hook_SetCursorPos(int x, int y) {
    if (!g_virtual) return real_SetCursorPos(x, y);
    g_x = x;                          /* sliders warp the pointer: keep yours still */
    g_y = y;
    return 1;
}

static SHORT WINAPI hook_GetAsyncKeyState(int key) {
    g_key_reads++;
    if (!g_virtual) return real_GetAsyncKeyState(key);
    return g_keys[key & 0xff] ? (SHORT)0x8001 : 0;
}

static SHORT WINAPI hook_GetKeyState(int key) {
    g_key_reads++;
    if (!g_virtual) return real_GetKeyState(key);
    return g_keys[key & 0xff] ? (SHORT)0xff80 : 0;
}

static HANDLE WINAPI hook_GetForegroundWindow(void) {
    if (g_virtual) {
        HANDLE window = front_window();
        if (window) return window;
    }
    return real_GetForegroundWindow();
}

static HANDLE WINAPI hook_WindowFromPoint(long x, long y) {
    if (g_virtual) return window_at(x, y);
    return real_WindowFromPoint(x, y);
}

static BOOL WINAPI hook_PeekMessageA(void *message, HANDLE window, UINT low, UINT high, UINT how) {
    g_peeks++;
    return real_PeekMessageA(message, window, low, high, how);
}

static int WINAPI hook_StretchDIBits(HANDLE dc, int a, int b, int c, int d, int e, int f, int g,
                                     int h, const void *bits, const void *info, UINT use, DWORD op) {
    g_blits++;
    return real_StretchDIBits(dc, a, b, c, d, e, f, g, h, bits, info, use, op);
}

static BOOL WINAPI hook_BitBlt(HANDLE to, int x, int y, int w, int h, HANDLE from, int sx, int sy,
                               DWORD op) {
    g_blits++;
    return real_BitBlt(to, x, y, w, h, from, sx, sy, op);
}

/* CWinAppl::IsFrontProcess: while we drive Bryce it counts as the front program. */
static char __fastcall hook_IsFrontProcess(void *self, void *unused) {
    if (g_virtual) return 1;
    return real_IsFrontProcess(self, unused);
}

/* CWinAppl::InterruptionInQueue: long jobs such as rendering call this to see
   whether to stop, so its rate shows that Bryce is working. */
static char __fastcall hook_InterruptionInQueue(void *self, void *unused) {
    g_checks++;
    return real_InterruptionInQueue(self, unused);
}

/* ------------------------------------------------------------- installation */
static const char *g_problem = "not installed";
static int g_ready;
static int g_import_hooks;

static BYTE *nt_headers(BYTE *module) { return module + *(DWORD *)(module + 0x3c); }
static DWORD time_stamp(BYTE *module) { return *(DWORD *)(nt_headers(module) + 8); }

static int write_memory(void *at, const void *bytes, unsigned int count) {
    DWORD old = 0, ignored = 0;
    if (!VirtualProtect(at, count, PAGE_EXECUTE_READWRITE, &old)) return 0;
    memcpy(at, bytes, count);
    VirtualProtect(at, count, old, &ignored);
    FlushInstructionCache(GetCurrentProcess(), at, count);
    return 1;
}

/* Redirect one function in a module's import table; found by name. */
static int hook_import(BYTE *module, const char *name, void *hook, void **real) {
    DWORD directory = *(DWORD *)(nt_headers(module) + 24 + 96 + 8);
    DWORD *entry;
    if (!directory) return 0;
    for (entry = (DWORD *)(module + directory); entry[3]; entry += 5) {
        DWORD *names = (DWORD *)(module + (entry[0] ? entry[0] : entry[4]));
        void **slots = (void **)(module + entry[4]);
        int i;
        for (i = 0; names[i]; i++) {
            if (names[i] & 0x80000000u) continue;
            if (!same((const char *)(module + names[i] + 2), name)) continue;
            if (!*real) *real = slots[i];
            if (!write_memory(&slots[i], &hook, 4)) return 0;
            g_import_hooks++;
            return 1;
        }
    }
    return 0;
}

static int prologue_is(const BYTE *code, const BYTE *expected, int count) {
    int i;
    for (i = 0; i < count; i++)
        if (code[i] != expected[i]) return 0;
    return 1;
}

/* Redirect a function whose first `count` bytes are whole instructions that do
   not depend on where they run. `copy` runs those bytes and jumps back. */
static int hook_function(BYTE *target, int count, void *hook, BYTE *copy, Method_t *original) {
    BYTE patch[16];
    int i;
    memcpy(copy, target, count);
    copy[count] = 0xe9;
    *(long *)(copy + count + 1) = (long)(target + count) - (long)(copy + count + 5);
    FlushInstructionCache(GetCurrentProcess(), copy, 32);
    *original = (Method_t)copy;
    patch[0] = 0xe9;
    *(long *)(patch + 1) = (long)hook - (long)(target + 5);
    for (i = 5; i < count; i++) patch[i] = 0x90;
    return write_memory(target, patch, count);
}

static HANDLE front_window(void);

/* Both code patches, with Bryce's main thread paused so that it cannot be
   running through bytes that are being replaced. */
static int patch_code(BYTE *front, BYTE *check) {
    BYTE *copies = VirtualAlloc(0, 64, 0x3000, PAGE_EXECUTE_READWRITE);
    HANDLE window = front_window();
    HANDLE thread = 0;
    int done;
    if (!copies) return 0;
    if (window) thread = OpenThread(0x0002, 0, GetWindowThreadProcessId(window, 0));
    if (thread) SuspendThread(thread);          /* THREAD_SUSPEND_RESUME */
    done = hook_function(front, 6, hook_IsFrontProcess, copies, &real_IsFrontProcess) &&
           hook_function(check, 5, hook_InterruptionInQueue, copies + 32,
                         &real_InterruptionInQueue);
    if (thread) {
        ResumeThread(thread);
        CloseHandle(thread);
    }
    return done;
}

static void install(void) {
    static const BYTE front_start[] = {0x8a, 0x81, 0x94, 0x00, 0x00, 0x00};
    static const BYTE check_start[] = {0x8b, 0x01, 0x83, 0xec, 0x1c};
    BYTE *exe = GetModuleHandleA(0);
    BYTE *axiom = GetModuleHandleA("Axiom.dll");
    BYTE *front, *check;
    BYTE *modules[2];
    int i;

    if ((DWORD)exe != EXE_BASE || time_stamp(exe) != EXE_STAMP) {
        g_problem = "this is not the Bryce 3D build the bridge supports";
        return;
    }
    if (!axiom || time_stamp(axiom) != AXIOM_STAMP) {
        g_problem = "Axiom.dll is not the supported build";
        return;
    }
    front = GetProcAddress(axiom, "?IsFrontProcess@CWinAppl@@UBEEXZ");
    check = GetProcAddress(axiom, "?InterruptionInQueue@CWinAppl@@UBEEXZ");
    if (!front || !check || !prologue_is(front, front_start, 6) ||
        !prologue_is(check, check_start, 5)) {
        g_problem = "Axiom.dll code differs from the supported build (already hooked?)";
        return;
    }
    modules[0] = exe;
    modules[1] = axiom;
    for (i = 0; i < 2; i++) {
        BYTE *m = modules[i];
        hook_import(m, "GetCursorPos", hook_GetCursorPos, (void **)&real_GetCursorPos);
        hook_import(m, "SetCursorPos", hook_SetCursorPos, (void **)&real_SetCursorPos);
        hook_import(m, "GetAsyncKeyState", hook_GetAsyncKeyState, (void **)&real_GetAsyncKeyState);
        hook_import(m, "GetKeyState", hook_GetKeyState, (void **)&real_GetKeyState);
        hook_import(m, "GetForegroundWindow", hook_GetForegroundWindow,
                    (void **)&real_GetForegroundWindow);
        hook_import(m, "WindowFromPoint", hook_WindowFromPoint, (void **)&real_WindowFromPoint);
        hook_import(m, "PeekMessageA", hook_PeekMessageA, (void **)&real_PeekMessageA);
        hook_import(m, "StretchDIBits", hook_StretchDIBits, (void **)&real_StretchDIBits);
        hook_import(m, "BitBlt", hook_BitBlt, (void **)&real_BitBlt);
    }
    if (!real_GetCursorPos || !real_GetAsyncKeyState || !real_PeekMessageA) {
        g_problem = "Bryce's input imports were not found";
        return;
    }
    if (!patch_code(front, check)) {
        g_problem = "could not patch Axiom.dll";
        return;
    }
    g_ready = 1;
    g_problem = "";
}

/* ------------------------------------------------------------------ actions */
static long make_lparam(HANDLE window, long x, long y) {
    POINT p = {x, y};
    ScreenToClient(window, &p);
    return (long)(((DWORD)(p.y & 0xffff) << 16) | (DWORD)(p.x & 0xffff));
}

static UINT held_buttons(void) {
    return (g_keys[1] ? 0x0001 : 0) | (g_keys[2] ? 0x0002 : 0) |
           (g_keys[0x10] ? 0x0004 : 0) | (g_keys[0x11] ? 0x0008 : 0);
}

static HANDLE g_capture;     /* the window that got the button press keeps the drag */

static HANDLE move_pointer(long x, long y) {
    HANDLE window;
    g_x = x;
    g_y = y;
    window = g_capture && IsWindow(g_capture) ? g_capture : window_at(x, y);
    if (window) PostMessageA(window, WM_MOUSEMOVE, held_buttons(), make_lparam(window, x, y));
    return window;
}

static HANDLE press_button(int button, int down) {
    HANDLE window = g_capture && IsWindow(g_capture) ? g_capture : window_at(g_x, g_y);
    int key = button == 2 ? 2 : 1;
    UINT message = button == 2 ? (down ? WM_RBUTTONDOWN : WM_RBUTTONUP)
                               : (down ? WM_LBUTTONDOWN : WM_LBUTTONUP);
    g_keys[key] = (BYTE)down;
    g_capture = down ? window : 0;
    if (window) PostMessageA(window, message, held_buttons(), make_lparam(window, g_x, g_y));
    return window;
}

static HANDLE press_key(int key, int down) {
    HANDLE window = window_at(g_x, g_y);
    if (!window) window = front_window();
    g_keys[key & 0xff] = (BYTE)down;
    if (window)
        PostMessageA(window, down ? WM_KEYDOWN : WM_KEYUP, key & 0xff,
                     down ? 1 : (long)0xc0000001);
    return window;
}

static void release_all(void) {
    int key;
    if (!g_virtual) return;
    if (g_keys[1]) press_button(1, 0);
    if (g_keys[2]) press_button(2, 0);
    for (key = 3; key < VK_COUNT; key++)
        if (g_keys[key]) press_key(key, 0);
    g_capture = 0;
    g_virtual = 0;
}

static DWORD *document_flags(void) {
    BYTE *document = *(BYTE **)DOCUMENT_POINTER;
    return document ? (DWORD *)(document + DOCUMENT_FLAGS) : 0;
}

/* --------------------------------------------------------------------- pipe */
static HANDLE g_pipe;

static int send_bytes(const void *data, DWORD count) {
    const BYTE *p = data;
    while (count) {
        DWORD done = 0;
        if (!WriteFile(g_pipe, p, count > 32768 ? 32768 : count, &done, 0) || !done) return 0;
        p += done;
        count -= done;
    }
    return 1;
}
static int send_text(const char *text) { return send_bytes(text, length(text)); }

static void clean_name(char *to, const unsigned short *from, int count) {
    int i;
    for (i = 0; i < count && from[i]; i++)
        to[i] = (from[i] >= 32 && from[i] < 127 && from[i] != '"' && from[i] != '\\')
                    ? (char)from[i] : '?';
    to[i] = 0;
}

struct Listing { DWORD pid; int count; };

static void describe_window(HANDLE window, struct Listing *listing) {
    char line[700], klass[64], title[96];
    unsigned short wide[96];
    RECT r = {0, 0, 0, 0}, c = {0, 0, 0, 0};
    POINT origin = {0, 0};
    int i, n;
    n = GetClassNameA(window, klass, 63);
    klass[n > 0 ? n : 0] = 0;
    for (i = 0; klass[i]; i++)
        if (klass[i] == '"' || klass[i] == '\\' || klass[i] < 32) klass[i] = '?';
    wide[0] = 0;
    InternalGetWindowText(window, wide, 95);      /* never waits on Bryce */
    clean_name(title, wide, 95);
    GetWindowRect(window, &r);
    GetClientRect(window, &c);
    ClientToScreen(window, &origin);
    wsprintfA(line,
              "%s{\"window\":%u,\"parent\":%u,\"class\":\"%s\",\"title\":\"%s\","
              "\"visible\":%d,\"enabled\":%d,\"rect\":[%d,%d,%d,%d],\"client\":[%d,%d,%d,%d]}",
              listing->count++ ? "," : "", (UINT)window, (UINT)GetParent(window), klass, title,
              IsWindowVisible(window) ? 1 : 0, IsWindowEnabled(window) ? 1 : 0,
              (int)r.left, (int)r.top, (int)r.right, (int)r.bottom,
              (int)origin.x, (int)origin.y, (int)(origin.x + c.right), (int)(origin.y + c.bottom));
    send_text(line);
}

static BOOL WINAPI list_child(HANDLE window, long parameter) {
    struct Listing *listing = (struct Listing *)parameter;
    if (listing->count < 400) describe_window(window, listing);
    return 1;
}

static BOOL WINAPI list_top(HANDLE window, long parameter) {
    struct Listing *listing = (struct Listing *)parameter;
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    if (pid != listing->pid || listing->count >= 400) return 1;
    describe_window(window, listing);
    EnumChildWindows(window, list_child, parameter);
    return 1;
}

/* Copy a window's client area. Answers a header line, then width*height*4 bytes. */
static void send_capture(HANDLE window) {
    RECT c;
    BITMAPINFOHEADER info;
    HANDLE source, memory, bitmap, previous;
    void *bits = 0;
    int width, height;
    char line[160];
    DWORD owner = 0;
    GetWindowThreadProcessId(window, &owner);
    if (owner != GetCurrentProcessId() || !GetClientRect(window, &c)) {
        send_text("ERR not one of Bryce's windows\n");
        return;
    }
    width = c.right;
    height = c.bottom;
    if (width < 1 || height < 1 || width > 4096 || height > 4096) {
        send_text("ERR the window has no picture (minimized?)\n");
        return;
    }
    source = GetDC(window);
    memory = CreateCompatibleDC(source);
    memset(&info, 0, sizeof info);
    info.biSize = sizeof info;
    info.biWidth = width;
    info.biHeight = -height;
    info.biPlanes = 1;
    info.biBitCount = 32;
    bitmap = CreateDIBSection(memory, &info, 0, &bits, 0, 0);
    if (!bitmap || !bits) {
        DeleteDC(memory);
        ReleaseDC(window, source);
        send_text("ERR could not make a bitmap\n");
        return;
    }
    previous = SelectObject(memory, bitmap);
    if (BitBlt(memory, 0, 0, width, height, source, 0, 0, SRCCOPY)) {
        GdiFlush();
        wsprintfA(line, "OK {\"width\":%d,\"height\":%d,\"bytes\":%d}\n",
                  width, height, width * height * 4);
        if (send_text(line)) send_bytes(bits, (DWORD)(width * height * 4));
    } else {
        send_text("ERR could not copy the window\n");
    }
    SelectObject(memory, previous);
    DeleteObject(bitmap);
    DeleteDC(memory);
    ReleaseDC(window, source);
}

/* Read the next word as a (possibly negative) decimal number. */
static int number(const char **cursor, long *value) {
    const char *p = *cursor;
    int negative = 0, digits = 0;
    unsigned long total = 0;
    while (*p == ' ') p++;
    if (*p == '-') negative = 1, p++;
    while (*p >= '0' && *p <= '9' && digits < 10) total = total * 10 + (*p++ - '0'), digits++;
    if (!digits || (*p && *p != ' ')) return 0;
    *value = negative ? -(long)total : (long)total;
    *cursor = p;
    return 1;
}

static void answer_window(HANDLE window) {
    char line[64];
    wsprintfA(line, "OK {\"window\":%u}\n", (UINT)window);
    send_text(line);
}

static DWORD main_thread_ms(void) {
    HANDLE window = front_window();
    HANDLE thread;
    FILETIME created, ended, kernel, user;
    DWORD id, total = 0;
    if (!window) return 0;
    id = GetWindowThreadProcessId(window, 0);
    thread = OpenThread(0x0040, 0, id);           /* THREAD_QUERY_INFORMATION */
    if (!thread) return 0;
    if (GetThreadTimes(thread, &created, &ended, &kernel, &user))
        total = user.low / 10000u + kernel.low / 10000u;
    CloseHandle(thread);
    return total;
}

static void handle(char *line) {
    const char *rest = line;
    char verb[24], out[512];
    long a = 0, b = 0;
    int n = 0;
    while (*rest && *rest != ' ' && n < 23) verb[n++] = *rest++;
    verb[n] = 0;
    g_last_request = GetTickCount();

    if (same(verb, "hello")) {
        wsprintfA(out, "OK {\"bridge\":%d,\"pid\":%u,\"ready\":%d,\"import_hooks\":%d,"
                       "\"problem\":\"%s\"}\n",
                  BRIDGE_VERSION, (UINT)GetCurrentProcessId(), g_ready, g_import_hooks, g_problem);
        send_text(out);
        return;
    }
    if (!g_ready) {
        wsprintfA(out, "ERR bridge not active: %s\n", g_problem);
        send_text(out);
        return;
    }
    if (same(verb, "state")) {
        DWORD *flags = document_flags();
        wsprintfA(out, "OK {\"document\":%d,\"unsaved\":%d,\"flags\":%u,\"virtual\":%d,"
                       "\"pointer\":[%d,%d],\"ticks\":%u,\"peeks\":%u,\"checks\":%u,"
                       "\"blits\":%u,\"cursor_reads\":%u,\"key_reads\":%u,\"cpu_ms\":%u}\n",
                  flags ? 1 : 0, flags ? (int)(*flags & 1) : 0, flags ? (UINT)*flags : 0,
                  g_virtual, (int)g_x, (int)g_y, (UINT)GetTickCount(), (UINT)g_peeks,
                  (UINT)g_checks, (UINT)g_blits, (UINT)g_cursor_reads, (UINT)g_key_reads,
                  (UINT)main_thread_ms());
        send_text(out);
    } else if (same(verb, "forget-changes")) {
        DWORD *flags = document_flags();
        if (!flags) {
            send_text("ERR Bryce has no open document\n");
            return;
        }
        wsprintfA(out, "OK {\"was_unsaved\":%d}\n", (int)(*flags & 1));
        *flags &= ~1u;
        send_text(out);
    } else if (same(verb, "windows")) {
        struct Listing listing = {GetCurrentProcessId(), 0};
        send_text("OK [");
        EnumWindows(list_top, (long)&listing);
        send_text("]\n");
    } else if (same(verb, "input")) {
        if (!number(&rest, &a)) {
            send_text("ERR usage: input 0|1\n");
            return;
        }
        if (a) {
            if (!g_virtual) {
                POINT p = {0, 0};
                int key;
                real_GetCursorPos(&p);
                g_x = p.x;
                g_y = p.y;
                for (key = 0; key < VK_COUNT; key++) g_keys[key] = 0;
                g_virtual = 1;
            }
        } else {
            release_all();
        }
        send_text("OK {}\n");
    } else if (same(verb, "capture")) {
        if (!number(&rest, &a)) send_text("ERR usage: capture <window>\n");
        else send_capture((HANDLE)a);
    } else if (same(verb, "move") || same(verb, "button") || same(verb, "key")) {
        if (!number(&rest, &a) || !number(&rest, &b)) {
            send_text("ERR two numbers expected\n");
        } else if (!g_virtual) {
            send_text("ERR switch the virtual input on first\n");
        } else if (verb[0] == 'm') {
            answer_window(move_pointer(a, b));
        } else if (verb[0] == 'b') {
            if (a != 1 && a != 2) send_text("ERR button is 1 (left) or 2 (right)\n");
            else answer_window(press_button((int)a, b != 0));
        } else {
            if (a < 3 || a >= VK_COUNT) send_text("ERR key code out of range\n");
            else answer_window(press_key((int)a, b != 0));
        }
    } else if (same(verb, "char")) {
        HANDLE window;
        if (!number(&rest, &a) || a < 1 || a > 255) {
            send_text("ERR usage: char <code>\n");
            return;
        }
        if (!g_virtual) {
            send_text("ERR switch the virtual input on first\n");
            return;
        }
        window = window_at(g_x, g_y);
        if (!window) window = front_window();
        if (window) PostMessageA(window, WM_CHAR, (UINT)a, 1);
        answer_window(window);
    } else {
        send_text("ERR unknown request\n");
    }
}

/* If the client goes quiet while holding the virtual input, give it back. */
static DWORD WINAPI watchdog(void *unused) {
    (void)unused;
    for (;;) {
        Sleep(500);
        if (g_virtual && GetTickCount() - g_last_request > IDLE_RELEASE_MS) release_all();
    }
    return 0;
}

static DWORD WINAPI serve(void *unused) {
    char name[64], buffer[256], line[256];
    int used = 0;
    (void)unused;
    install();
    wsprintfA(name, "\\\\.\\pipe\\bryce3d-bridge-%u", (UINT)GetCurrentProcessId());
    /* duplex, first instance only; byte mode, no remote clients */
    g_pipe = CreateNamedPipeA(name, 0x00000003 | 0x00080000, 0x00000008, 1, 65536, 4096, 0, 0);
    if (g_pipe == (HANDLE)-1) return 1;
    for (;;) {
        if (!ConnectNamedPipe(g_pipe, 0) && GetLastError() != 535) { /* already connected */
            if (GetLastError() == 109) return 0;
            Sleep(200);
            DisconnectNamedPipe(g_pipe);
            continue;
        }
        used = 0;
        for (;;) {
            DWORD got = 0, i;
            if (!ReadFile(g_pipe, buffer, sizeof buffer, &got, 0) || !got) break;
            for (i = 0; i < got; i++) {
                if (buffer[i] == '\n') {
                    line[used] = 0;
                    used = 0;
                    handle(line);
                } else if (buffer[i] != '\r' && used < (int)sizeof line - 1) {
                    line[used++] = buffer[i];
                }
            }
        }
        release_all();
        FlushFileBuffers(g_pipe);
        DisconnectNamedPipe(g_pipe);
    }
}

BOOL WINAPI DllMain(HANDLE module, DWORD reason, void *reserved) {
    (void)reserved;
    if (reason == 1) { /* DLL_PROCESS_ATTACH */
        HANDLE a, b;
        DisableThreadLibraryCalls(module);
        a = CreateThread(0, 0, serve, 0, 0, 0);
        b = CreateThread(0, 0, watchdog, 0, 0, 0);
        if (a) CloseHandle(a);
        if (b) CloseHandle(b);
    }
    return 1;
}
