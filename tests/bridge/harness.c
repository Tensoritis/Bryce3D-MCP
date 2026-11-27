/*
 * Runs the real bryce3d_bridge.dll on 32-bit-capable x86 Linux, against the code
 * and import tables of your own copy of Bryce 3D. Windows is replaced by small
 * stand-ins below. Standard input and output are the bridge's pipe; lines that
 * start with "!" are commands to this harness and are answered with "H ...".
 *
 *   harness <Axiom.dll> <"Bryce 3D.exe"> <bryce3d_bridge.dll>
 *
 * No part of Bryce is stored in this repository; the files are read at run time.
 */
#include <stdarg.h>
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;
#define STDCALL __attribute__((stdcall))
#define FASTCALL __attribute__((fastcall))

/* ----------------------------------------------------------- Linux, no libc */
static long sys3(long n, long a, long b, long c) {
    long r;
    __asm__ volatile("int $0x80" : "=a"(r) : "a"(n), "b"(a), "c"(b), "d"(c) : "memory");
    return r;
}
static __attribute__((noreturn)) void quit(int code) { sys3(1, code, 0, 0); __builtin_unreachable(); }
void *memset(void *to, int v, unsigned n) { u8 *p = to; while (n--) *p++ = v; return to; }
void *memcpy(void *to, const void *from, unsigned n) {
    u8 *p = to; const u8 *q = from; while (n--) *p++ = *q++; return to;
}
static int slen(const char *s) { int n = 0; while (s[n]) n++; return n; }
static int streq(const char *a, const char *b) { while (*a && *a == *b) a++, b++; return *a == *b; }
static int starts(const char *a, const char *b) { while (*b) if (*a++ != *b++) return 0; return 1; }
static void out_bytes(const void *p, int n) {
    while (n > 0) { long w = sys3(4, 1, (long)p, n); if (w <= 0) quit(9); p = (const u8 *)p + w; n -= w; }
}
static void say(const char *s) { out_bytes(s, slen(s)); }

static int format(char *to, const char *f, va_list args) {
    char *start = to;
    for (; *f; f++) {
        if (*f != '%') { *to++ = *f; continue; }
        f++;
        if (*f == 's') { const char *s = va_arg(args, const char *); while (*s) *to++ = *s++; }
        else if (*f == 'd' || *f == 'u' || *f == 'x') {
            u32 v = va_arg(args, u32), base = *f == 'x' ? 16 : 10;
            char digits[16]; int n = 0;
            if (*f == 'd' && (int)v < 0) { *to++ = '-'; v = -v; }
            do { digits[n++] = "0123456789abcdef"[v % base]; v /= base; } while (v);
            while (n) *to++ = digits[--n];
        } else *to++ = *f;
    }
    *to = 0;
    return to - start;
}
static void reply(const char *f, ...) {
    char line[600]; va_list a; va_start(a, f); format(line, f, a); va_end(a);
    say("H "); say(line); say("\n");
}

struct mmap_args { u32 address, size, protection, flags, file, offset; };
static void *map(u32 address, u32 size) {
    struct mmap_args a = {address, size, 7, 0x22 | (address ? 0x10 : 0), (u32)-1, 0};
    long r = sys3(90, (long)&a, 0, 0);
    if ((u32)r > 0xfffff000u) { reply("map failed at %x", address); quit(3); }
    return (void *)r;
}
static u8 *read_file(const char *path, u32 *size) {
    long fd = sys3(5, (long)path, 0, 0), got;
    u32 used = 0;
    u8 *data = map(0, 8 << 20);
    if (fd < 0) { reply("cannot open %s", path); quit(3); }
    while ((got = sys3(3, fd, (long)(data + used), 65536)) > 0) used += got;
    sys3(6, fd, 0, 0);
    *size = used;
    return data;
}

/* ------------------------------------------------------- stand-ins: Windows */
static int g_real_cursor_x = 7, g_real_cursor_y = 8, g_real_calls;
static u32 g_ticks = 1000000;
typedef u32 (STDCALL *thread_t)(void *);
static thread_t g_threads[4];
static int g_thread_count, g_suspends, g_resumes;
static struct { u32 window, message, wparam, lparam; } g_posted[256];
static int g_posted_count;
static int g_input_ended;
static u8 *g_axiom = (u8 *)0x10000000, *g_exe = (u8 *)0x400000;

#define MAIN_WINDOW 0x1001u
#define PROMPT_WINDOW 0x1002u
static int g_prompt_visible;

static void window_rect(u32 w, long *r) {
    if (w == MAIN_WINDOW) { r[0] = 100; r[1] = 50; r[2] = 740; r[3] = 530; }
    else { r[0] = 300; r[1] = 200; r[2] = 560; r[3] = 250; }
}

static void *STDCALL w_CreateThread(void *a, u32 b, thread_t f, void *p, u32 c, u32 *d) {
    (void)a; (void)b; (void)p; (void)c; (void)d;
    g_threads[g_thread_count++] = f;
    return (void *)0x77;
}
static void *STDCALL w_CreateNamedPipeA(const char *name, u32 a, u32 b, u32 c, u32 d, u32 e, u32 f,
                                        void *g) {
    (void)c; (void)d; (void)e; (void)f; (void)g;
    reply("pipe %s mode=%x type=%x", name, a, b);
    return (void *)0x88;
}
static u32 g_last_error;
static int STDCALL w_ConnectNamedPipe(void *p, void *o) {
    (void)p; (void)o;
    if (g_input_ended) { g_last_error = 109; return 0; }
    return 1;
}
static int STDCALL w_DisconnectNamedPipe(void *p) { (void)p; reply("disconnected"); return 1; }
static int STDCALL w_FlushFileBuffers(void *p) { (void)p; return 1; }
static int STDCALL w_CloseHandle(void *p) { (void)p; return 1; }
static u32 STDCALL w_GetLastError(void) { return g_last_error; }
static u32 STDCALL w_GetCurrentProcessId(void) { return 4242; }
static void *STDCALL w_GetCurrentProcess(void) { return (void *)-1; }
static int STDCALL w_DisableThreadLibraryCalls(void *m) { (void)m; return 1; }
static u32 STDCALL w_GetTickCount(void) { return g_ticks; }
static u32 g_jump[5];
static int g_sleeps, g_in_watchdog;
static void STDCALL w_Sleep(u32 ms) {
    g_ticks += ms;
    if (g_in_watchdog && ++g_sleeps >= 2) __builtin_longjmp((void **)g_jump, 1);
}
static void *STDCALL w_OpenThread(u32 a, int b, u32 c) { (void)a; (void)b; return (void *)c; }
static u32 STDCALL w_SuspendThread(void *t) { (void)t; g_suspends++; return 0; }
static u32 STDCALL w_ResumeThread(void *t) { (void)t; g_resumes++; return 0; }
static int STDCALL w_GetThreadTimes(void *t, u32 *a, u32 *b, u32 *k, u32 *u) {
    (void)t; a[0] = b[0] = 0; k[0] = 30000; k[1] = 0; u[0] = 1200000; u[1] = 0; return 1;
}
static u8 *g_heap;
static void *STDCALL w_VirtualAlloc(void *a, u32 size, u32 kind, u32 protection) {
    void *r; (void)a; (void)kind; (void)protection;
    if (!g_heap) g_heap = map(0, 1 << 20);
    r = g_heap; g_heap += (size + 63) & ~63u; return r;
}
static int STDCALL w_VirtualFree(void *a, u32 b, u32 c) { (void)a; (void)b; (void)c; return 1; }
static int STDCALL w_VirtualProtect(void *a, u32 b, u32 c, u32 *old) {
    (void)a; (void)b; (void)c; *old = 0x20; return 1;
}
static int STDCALL w_FlushInstructionCache(void *a, const void *b, u32 c) {
    (void)a; (void)b; (void)c; return 1;
}

static u8 *nt(u8 *m) { return m + *(u32 *)(m + 0x3c); }
static void *export_of(u8 *module, const char *name) {
    u32 directory = *(u32 *)(nt(module) + 24 + 96);
    u8 *e = module + directory;
    u32 count = *(u32 *)(e + 24);
    u32 *functions = (u32 *)(module + *(u32 *)(e + 28));
    u32 *names = (u32 *)(module + *(u32 *)(e + 32));
    u16 *ordinals = (u16 *)(module + *(u32 *)(e + 36));
    u32 i;
    for (i = 0; i < count; i++)
        if (streq((char *)(module + names[i]), name)) return module + functions[ordinals[i]];
    return 0;
}
static int g_hide_axiom;
static void *STDCALL w_GetModuleHandleA(const char *name) {
    if (!name) return g_exe;
    if (streq(name, "Axiom.dll") && !g_hide_axiom) return g_axiom;
    return 0;
}
static void *STDCALL w_GetProcAddress(void *m, const char *name) { return export_of(m, name); }

static int STDCALL w_ReadFile(void *pipe, char *to, u32 size, u32 *got, void *o);
static int STDCALL w_WriteFile(void *pipe, const void *from, u32 size, u32 *done, void *o) {
    (void)pipe; (void)o; out_bytes(from, size); *done = size; return 1;
}
static int STDCALL w_PeekNamedPipe(void *a, void *b, u32 c, u32 *d, u32 *e, u32 *f) {
    (void)a; (void)b; (void)c; (void)d; (void)e; (void)f; return 0;
}

static int w_wsprintfA(char *to, const char *f, ...) {
    va_list a; int n; va_start(a, f); n = format(to, f, a); va_end(a); return n;
}
typedef int (STDCALL *enum_t)(u32, long);
static int STDCALL w_EnumWindows(enum_t f, long p) {
    if (g_prompt_visible && !f(PROMPT_WINDOW, p)) return 0;
    if (!f(0x2001u, p)) return 0;                       /* another program's window */
    return f(MAIN_WINDOW, p);
}
static int STDCALL w_EnumChildWindows(u32 w, enum_t f, long p) { (void)w; (void)f; (void)p; return 1; }
static u32 STDCALL w_GetWindowThreadProcessId(u32 w, u32 *pid) {
    if (pid) *pid = (w == MAIN_WINDOW || w == PROMPT_WINDOW) ? 4242 : 999;
    return 55;
}
static int STDCALL w_IsWindow(u32 w) { return w == MAIN_WINDOW || (w == PROMPT_WINDOW && g_prompt_visible); }
static int STDCALL w_IsWindowVisible(u32 w) { (void)w; return 1; }
static int STDCALL w_IsWindowEnabled(u32 w) { (void)w; return 1; }
static int STDCALL w_IsIconic(u32 w) { (void)w; return 0; }
static int STDCALL w_GetClassNameA(u32 w, char *to, int n) {
    const char *s = w == MAIN_WINDOW ? "CWinAppl" : "Bryce\"Alert"; (void)n;
    memcpy(to, s, slen(s) + 1); return slen(s);
}
static int STDCALL w_InternalGetWindowText(u32 w, u16 *to, int n) {
    const char *s = w == MAIN_WINDOW ? "Bryce 3D \\ \x01" : ""; int i; (void)n;
    for (i = 0; s[i]; i++) to[i] = (u8)s[i];
    to[i] = 0; return i;
}
static int STDCALL w_GetWindowRect(u32 w, long *r) { window_rect(w, r); return 1; }
static int STDCALL w_GetClientRect(u32 w, long *r) {
    long s[4]; window_rect(w, s); r[0] = r[1] = 0;
    r[2] = w == MAIN_WINDOW ? 4 : s[2] - s[0]; r[3] = w == MAIN_WINDOW ? 3 : s[3] - s[1]; return 1;
}
static int STDCALL w_ClientToScreen(u32 w, long *p) { long r[4]; window_rect(w, r); p[0] += r[0]; p[1] += r[1]; return 1; }
static int STDCALL w_ScreenToClient(u32 w, long *p) { long r[4]; window_rect(w, r); p[0] -= r[0]; p[1] -= r[1]; return 1; }
static u32 STDCALL w_GetParent(u32 w) { (void)w; return 0; }
static u32 STDCALL w_ChildWindowFromPointEx(u32 w, long x, long y, u32 f) { (void)x; (void)y; (void)f; return w; }
static int STDCALL w_PostMessageA(u32 w, u32 m, u32 wp, u32 lp) {
    if (g_posted_count < 256) {
        g_posted[g_posted_count].window = w; g_posted[g_posted_count].message = m;
        g_posted[g_posted_count].wparam = wp; g_posted[g_posted_count].lparam = lp;
        g_posted_count++;
    }
    return 1;
}
static u32 STDCALL w_GetDC(u32 w) { return w + 1; }
static int STDCALL w_ReleaseDC(u32 w, u32 d) { (void)w; (void)d; return 1; }
static u32 STDCALL w_CreateCompatibleDC(u32 d) { return d + 1; }
static u32 g_bits[640 * 480];
static u32 STDCALL w_CreateDIBSection(u32 d, void *info, u32 use, void **bits, u32 s, u32 o) {
    (void)d; (void)info; (void)use; (void)s; (void)o; *bits = g_bits; return 0x99;
}
static u32 STDCALL w_SelectObject(u32 d, u32 o) { (void)d; return o; }
static int STDCALL w_BitBlt(u32 to, int x, int y, int w, int h, u32 from, int sx, int sy, u32 op) {
    int i; (void)to; (void)x; (void)y; (void)from; (void)sx; (void)sy; (void)op;
    g_real_calls++;
    for (i = 0; i < w * h && i < 640 * 480; i++) g_bits[i] = 0x00102030u + i;
    return 1;
}
static int STDCALL w_GdiFlush(void) { return 1; }
static int STDCALL w_DeleteObject(u32 o) { (void)o; return 1; }
static int STDCALL w_DeleteDC(u32 d) { (void)d; return 1; }

/* What Bryce itself would call: the "real" user32 behind the hooks. */
static int STDCALL r_GetCursorPos(long *p) { p[0] = g_real_cursor_x; p[1] = g_real_cursor_y; g_real_calls++; return 1; }
static int STDCALL r_SetCursorPos(int x, int y) { g_real_cursor_x = x; g_real_cursor_y = y; g_real_calls++; return 1; }
static short STDCALL r_GetAsyncKeyState(int k) { (void)k; g_real_calls++; return 0; }
static short STDCALL r_GetKeyState(int k) { (void)k; g_real_calls++; return 0; }
static u32 STDCALL r_GetForegroundWindow(void) { return 0x2001u; }
static u32 STDCALL r_WindowFromPoint(long x, long y) { (void)x; (void)y; return 0x2001u; }
static int STDCALL r_PeekMessageA(void *m, u32 w, u32 a, u32 b, u32 c) {
    (void)m; (void)w; (void)a; (void)b; (void)c; g_real_calls++; return 0;
}
static int STDCALL r_GetSystemMetrics(int i) { (void)i; return 0; }
static u32 STDCALL r_GetQueueStatus(u32 f) { (void)f; return 0; }
static void trap(void) { reply("trap: an import without a stand-in was called"); quit(4); }

struct stand_in { const char *name; void *function; };
#define W(n) {#n, (void *)w_##n}
#define R(n) {#n, (void *)r_##n}
static const struct stand_in for_bridge[] = {
    W(CreateThread), W(CreateNamedPipeA), W(ConnectNamedPipe), W(DisconnectNamedPipe),
    W(PeekNamedPipe), W(ReadFile), W(WriteFile), W(FlushFileBuffers), W(CloseHandle),
    W(GetLastError), W(GetCurrentProcessId), W(GetModuleHandleA), W(GetProcAddress),
    W(VirtualProtect), W(VirtualAlloc), W(VirtualFree), W(FlushInstructionCache),
    W(GetCurrentProcess), W(GetTickCount), W(Sleep), W(OpenThread), W(GetThreadTimes),
    W(SuspendThread), W(ResumeThread), W(DisableThreadLibraryCalls), W(wsprintfA),
    W(EnumWindows), W(EnumChildWindows), W(GetWindowThreadProcessId), W(IsWindow),
    W(IsWindowVisible), W(IsWindowEnabled), W(IsIconic), W(GetClassNameA),
    W(InternalGetWindowText), W(GetWindowRect), W(GetClientRect), W(ClientToScreen),
    W(ScreenToClient), W(GetParent), W(ChildWindowFromPointEx), W(PostMessageA), W(GetDC),
    W(ReleaseDC), W(CreateCompatibleDC), W(CreateDIBSection), W(SelectObject), W(BitBlt),
    W(GdiFlush), W(DeleteObject), W(DeleteDC), {0, 0}};
static const struct stand_in for_bryce[] = {
    R(GetCursorPos), R(SetCursorPos), R(GetAsyncKeyState), R(GetKeyState),
    R(GetForegroundWindow), R(WindowFromPoint), R(PeekMessageA), R(GetSystemMetrics),
    R(GetQueueStatus), W(ScreenToClient), W(ClientToScreen), W(BitBlt), W(IsIconic),
    W(PostMessageA), {0, 0}};

/* ------------------------------------------------------------------- loader */
static u8 *load(const char *path, u32 want, const struct stand_in *table) {
    u32 size, i;
    u8 *file = read_file(path, &size), *h = nt(file);
    u16 sections = *(u16 *)(h + 6), optional = *(u16 *)(h + 20);
    u32 preferred = *(u32 *)(h + 24 + 28), image = *(u32 *)(h + 24 + 56);
    u32 headers = *(u32 *)(h + 24 + 60);
    u8 *base = map(want ? want : preferred, image);
    memcpy(base, file, headers);
    for (i = 0; i < sections; i++) {
        u8 *s = h + 24 + optional + 40 * i;
        u32 virtual_size = *(u32 *)(s + 8), address = *(u32 *)(s + 12);
        u32 raw = *(u32 *)(s + 16), at = *(u32 *)(s + 20);
        memcpy(base + address, file + at, raw < virtual_size || !virtual_size ? raw : virtual_size);
    }
    if ((u32)base != preferred) { /* base relocations */
        u32 rva = *(u32 *)(h + 24 + 96 + 8 * 5), total = *(u32 *)(h + 24 + 96 + 8 * 5 + 4);
        u8 *block = base + rva, *end = block + total;
        if (!total) { reply("%s cannot be moved", path); quit(3); }
        while (block < end) {
            u32 page = *(u32 *)block, length = *(u32 *)(block + 4);
            for (i = 8; i < length; i += 2) {
                u16 entry = *(u16 *)(block + i);
                if (entry >> 12 == 3) *(u32 *)(base + page + (entry & 0xfff)) += (u32)base - preferred;
            }
            block += length;
        }
    }
    { /* imports */
        u32 rva = *(u32 *)(h + 24 + 96 + 8);
        u32 *entry = (u32 *)(base + rva);
        for (; rva && entry[3]; entry += 5) {
            u32 *names = (u32 *)(base + (entry[0] ? entry[0] : entry[4]));
            void **slots = (void **)(base + entry[4]);
            int n;
            for (n = 0; names[n]; n++) {
                const struct stand_in *s;
                void *found = (void *)trap;
                if (!(names[n] & 0x80000000u))
                    for (s = table; s->name; s++)
                        if (streq(s->name, (char *)(base + names[n] + 2))) found = s->function;
                if (table == for_bridge && found == (void *)trap) {
                    reply("no stand-in for %s", (char *)(base + names[n] + 2)); quit(3);
                }
                slots[n] = found;
            }
        }
    }
    return base;
}

/* The slot through which a module calls an imported function. */
static void *slot(u8 *module, const char *name) {
    u32 *entry = (u32 *)(module + *(u32 *)(nt(module) + 24 + 96 + 8));
    for (; entry[3]; entry += 5) {
        u32 *names = (u32 *)(module + (entry[0] ? entry[0] : entry[4]));
        void **slots = (void **)(module + entry[4]);
        int n;
        for (n = 0; names[n]; n++)
            if (!(names[n] & 0x80000000u) && streq((char *)(module + names[n] + 2), name))
                return slots[n];
    }
    reply("%s is not imported", name); quit(3);
}

/* ------------------------------------------------------- harness commands */
/* A stand-in application object that uses Axiom's real CWinAppl vtable, so
   these calls run Bryce's own code. */
static u32 g_application[256];
static u8 g_document[0x1000];

static u32 call_method(const char *symbol) {
    typedef u32 (FASTCALL *method_t)(void *self, void *unused);
    method_t f = (method_t)export_of(g_axiom, symbol);
    if (!f) { reply("no export %s", symbol); quit(3); }
    return f(g_application, 0) & 0xff;
}

static int number(const char **p) {
    int v = 0, minus = 0;
    while (**p == ' ') (*p)++;
    if (**p == '-') minus = 1, (*p)++;
    while (**p >= '0' && **p <= '9') v = v * 10 + *(*p)++ - '0';
    return minus ? -v : v;
}

static void command(const char *c) {
    if (streq(c, "button")) reply("button %d", call_method("?ButtonDown@CWinAppl@@UBEEXZ"));
    else if (streq(c, "shift")) {
        typedef u32 (FASTCALL *key_t)(void *, void *, u32);
        key_t f = (key_t)export_of(g_axiom, "?IsKeyDown@CWinAppl@@UBEEK@Z");
        reply("shift %d", f(g_application, 0, 0x10) & 0xff);
    } else if (streq(c, "front")) reply("front %d", call_method("?IsFrontProcess@CWinAppl@@UBEEXZ"));
    else if (streq(c, "interrupt")) reply("interrupt %d", call_method("?InterruptionInQueue@CWinAppl@@UBEEXZ"));
    else if (streq(c, "pointer")) {
        typedef void (FASTCALL *loc_t)(void *, void *, long *);
        loc_t f = (loc_t)export_of(g_axiom, "?PointerLoc@CWinAppl@@UBE?AVC2iVector@@XZ");
        long p[2] = {0, 0};
        f(g_application, 0, p);
        reply("pointer %d %d", p[0], p[1]);
    } else if (streq(c, "exe")) { /* the calls the program itself makes */
        long p[2];
        ((int (STDCALL *)(long *))slot(g_exe, "GetCursorPos"))(p);
        u32 front = ((u32 (STDCALL *)(void))slot(g_exe, "GetForegroundWindow"))();
        u32 under = ((u32 (STDCALL *)(long, long))slot(g_exe, "WindowFromPoint"))(p[0], p[1]);
        short held = ((short (STDCALL *)(int))slot(g_exe, "GetKeyState"))(0x11);
        ((int (STDCALL *)(void *, u32, u32, u32, u32))slot(g_exe, "PeekMessageA"))(p, 0, 0, 0, 0);
        reply("exe cursor %d %d front %u under %u ctrl %d", p[0], p[1], front, under, held < 0);
    } else if (starts(c, "warp ")) {
        const char *p = c + 5; int x = number(&p), y = number(&p);
        ((int (STDCALL *)(int, int))slot(g_exe, "SetCursorPos"))(x, y);
        reply("real cursor %d %d", g_real_cursor_x, g_real_cursor_y);
    } else if (starts(c, "dirty ")) {
        const char *p = c + 6; *(u32 *)(g_document + 0x4d8) = number(&p); reply("ok");
    } else if (streq(c, "flags")) reply("flags %u", *(u32 *)(g_document + 0x4d8));
    else if (streq(c, "posted")) {
        int i;
        for (i = 0; i < g_posted_count; i++)
            reply("post %u %u %u %u", g_posted[i].window, g_posted[i].message, g_posted[i].wparam, g_posted[i].lparam);
        reply("posted %d", g_posted_count);
        g_posted_count = 0;
    } else if (streq(c, "prompt")) { g_prompt_visible = !g_prompt_visible; reply("prompt %d", g_prompt_visible); }
    else if (starts(c, "idle ")) { /* let time pass, then let the watchdog look once */
        const char *p = c + 5;
        g_ticks += number(&p);
        g_sleeps = 0; g_in_watchdog = 1;
        if (!__builtin_setjmp((void **)g_jump)) g_threads[1](0);
        g_in_watchdog = 0;
        reply("idled");
    } else if (streq(c, "suspends")) reply("suspends %d %d", g_suspends, g_resumes);
    else reply("unknown command");
}

static int STDCALL w_ReadFile(void *pipe, char *to, u32 size, u32 *got, void *o) {
    static char line[512];
    int used = 0;
    (void)pipe; (void)o;
    for (;;) {
        char c;
        if (sys3(3, 0, (long)&c, 1) <= 0) { g_input_ended = 1; g_last_error = 109; *got = 0; return 0; }
        if (c != '\n') { if (used < 500) line[used++] = c; continue; }
        line[used] = 0;
        if (line[0] == '!') { command(line + 1); used = 0; continue; }
        line[used++] = '\n';
        if ((u32)used > size) used = size;
        memcpy(to, line, used);
        *got = used;
        return 1;
    }
}

static __attribute__((noreturn)) void run(u32 *stack) {
    int argc = stack[0];
    char **argv = (char **)(stack + 1);
    u8 *bridge, *h;
    typedef int (STDCALL *main_t)(void *, u32, void *);
    if (argc < 4) { say("usage: harness Axiom.dll bryce.exe bridge.dll [no-axiom|other-build]\n"); quit(2); }
    g_axiom = load(argv[1], 0, for_bryce);
    g_exe = load(argv[2], 0, for_bryce);
    bridge = load(argv[3], 0x30000000, for_bridge);
    if (argc > 4 && streq(argv[4], "no-axiom")) g_hide_axiom = 1;
    if (argc > 4 && streq(argv[4], "other-build")) *(u32 *)(nt(g_exe) + 8) ^= 1;
    memset(g_application, 0, sizeof g_application);
    g_application[0] = (u32)export_of(g_axiom, "??_7CWinAppl@@6B@");
    *(u32 *)0x635018 = (u32)g_document;
    h = nt(bridge);
    ((main_t)(bridge + *(u32 *)(h + 24 + 16)))(bridge, 1, 0);
    reply("loaded %d threads", g_thread_count);
    g_threads[0](0);
    reply("server stopped");
    quit(0);
}

__attribute__((naked)) void _start(void) {
    __asm__("mov %esp, %eax\n and $-16, %esp\n sub $12, %esp\n push %eax\n call run_entry\n");
}
void run_entry(u32 *stack) { run(stack); }
