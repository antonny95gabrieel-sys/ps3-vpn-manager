#!/usr/bin/env python3
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text()

if "static void set_render_target(int index)" in source:
    print("Correção de runtime já aplicada.")
    raise SystemExit(0)

if '#include <stdlib.h>' not in source:
    source = source.replace('#include <malloc.h>\n', '#include <malloc.h>\n#include <stdlib.h>\n', 1)

anchor = 'static ui_runtime_t g_runtime;\n'
if anchor not in source:
    raise SystemExit('Não foi possível localizar as variáveis globais esperadas.')
source = source.replace(anchor, anchor + '''static void *g_host_addr;
static int g_rsx_initialized;
static u32 *g_depth_buffer;
static u32 g_depth_offset;
static u32 g_depth_pitch;
static u32 g_color_pitch;
static u32 g_color_offsets[2];
static int g_first_flip = 1;

static void log_runtime(const char *stage, int result)
{
    FILE *log_file = fopen("/dev_hdd0/tmp/ps3vpnmanager.log", "a");
    if (log_file != NULL) {
        fprintf(log_file, "%s: %d\\n", stage, result);
        fclose(log_file);
    }
}

static void set_render_target(int index)
{
    gcmSurface surface;
    memset(&surface, 0, sizeof(surface));
    surface.colorFormat = GCM_SURFACE_X8R8G8B8;
    surface.colorTarget = GCM_SURFACE_TARGET_0;
    surface.colorLocation[0] = GCM_LOCATION_RSX;
    surface.colorOffset[0] = g_color_offsets[index];
    surface.colorPitch[0] = g_color_pitch;
    surface.colorLocation[1] = GCM_LOCATION_RSX;
    surface.colorLocation[2] = GCM_LOCATION_RSX;
    surface.colorLocation[3] = GCM_LOCATION_RSX;
    surface.colorPitch[1] = 64;
    surface.colorPitch[2] = 64;
    surface.colorPitch[3] = 64;
    surface.depthFormat = GCM_SURFACE_ZETA_Z24S8;
    surface.depthLocation = GCM_LOCATION_RSX;
    surface.depthOffset = g_depth_offset;
    surface.depthPitch = g_depth_pitch;
    surface.type = GCM_SURFACE_TYPE_LINEAR;
    surface.antiAlias = GCM_SURFACE_CENTER_1;
    surface.width = g_resolution.width;
    surface.height = g_resolution.height;
    surface.x = 0;
    surface.y = 0;
    rsxSetSurface(g_context, &surface);
}
''', 1)

start = source.index('static int init_video(void)')
end = source.index('static int render_frame(void)', start)
new_init = '''static int init_video(void)
{
    videoState state;
    videoConfiguration config;
    u32 buffer_size;
    int i;

    /* Segue o tamanho e alinhamento usados pelo sample gráfico oficial. */
    g_host_addr = memalign(1024 * 1024, 128 * 1024 * 1024);
    if (g_host_addr == NULL) {
        log_runtime("memalign host buffer", -1);
        return -1;
    }

    if (rsxInit(&g_context, 0x80000, 128 * 1024 * 1024, g_host_addr) != 0) {
        log_runtime("rsxInit", -1);
        return -1;
    }
    g_rsx_initialized = 1;

    if (videoGetState(VIDEO_PRIMARY, 0, &state) != 0) {
        log_runtime("videoGetState", -1);
        return -1;
    }
    if (state.state != VIDEO_STATE_ENABLED) {
        log_runtime("video state disabled", state.state);
        return -1;
    }
    if (videoGetResolution(state.displayMode.resolution, &g_resolution) != 0) {
        log_runtime("videoGetResolution", state.displayMode.resolution);
        return -1;
    }

    memset(&config, 0, sizeof(config));
    config.resolution = state.displayMode.resolution;
    config.format = VIDEO_BUFFER_FORMAT_XRGB;
    config.aspect = VIDEO_ASPECT_AUTO;
    config.pitch = (u32)g_resolution.width * 4;
    if (videoConfigure(VIDEO_PRIMARY, &config, NULL, 0) != 0) {
        log_runtime("videoConfigure", -1);
        return -1;
    }

    gcmSetFlipMode(GCM_FLIP_VSYNC);
    g_color_pitch = (u32)g_resolution.width * 4;
    g_depth_pitch = (u32)g_resolution.width * 4;
    buffer_size = (u32)g_resolution.width * (u32)g_resolution.height * 4;

    for (i = 0; i < 2; ++i) {
        g_buffers[i] = (u32 *)rsxMemalign(64, buffer_size);
        if (g_buffers[i] == NULL) {
            log_runtime("rsxMemalign color buffer", i);
            return -1;
        }
        if (rsxAddressToOffset(g_buffers[i], &g_color_offsets[i]) != 0) {
            log_runtime("rsxAddressToOffset color buffer", i);
            return -1;
        }
        if (gcmSetDisplayBuffer((u32)i, g_color_offsets[i], g_color_pitch,
                                g_resolution.width, g_resolution.height) != 0) {
            log_runtime("gcmSetDisplayBuffer", i);
            return -1;
        }
    }

    g_depth_buffer = (u32 *)rsxMemalign(64, buffer_size);
    if (g_depth_buffer == NULL) {
        log_runtime("rsxMemalign depth buffer", -1);
        return -1;
    }
    if (rsxAddressToOffset(g_depth_buffer, &g_depth_offset) != 0) {
        log_runtime("rsxAddressToOffset depth buffer", -1);
        return -1;
    }

    gcmResetFlipStatus();
    g_current_buffer = 0;
    g_first_flip = 1;
    set_render_target(g_current_buffer);
    log_runtime("video initialization complete", 0);
    return 0;
}

'''
source = source[:start] + new_init + source[end:]

old_flip = '''static int flip(int index)
{
    if (gcmSetFlip(g_context, index) != 0) return -1;
    rsxFlushBuffer(g_context);
    gcmSetWaitFlip(g_context);
    return 0;
}

'''
if old_flip not in source:
    raise SystemExit('Não foi possível localizar a função flip antiga.')
source = source.replace(old_flip, '', 1)

start = source.index('static int render_frame(void)')
end = source.index('static void event_callback', start)
new_render = '''static int present_frame(void)
{
    if (g_first_flip) {
        gcmResetFlipStatus();
    } else {
        wait_flip();
    }

    if (gcmSetFlip(g_context, g_current_buffer) != 0) {
        log_runtime("gcmSetFlip", g_current_buffer);
        return -1;
    }
    rsxFlushBuffer(g_context);
    gcmSetWaitFlip(g_context);

    g_current_buffer ^= 1;
    set_render_target(g_current_buffer);
    g_first_flip = 0;
    return 0;
}

static int render_frame(void)
{
    framebuffer_t fb;
    ui_draw_command_t commands[64];
    ui_renderer_backend_t backend;
    ui_viewport_t viewport;
    size_t count;
    int result;

    fb.pixels = g_buffers[g_current_buffer];
    fb.width = g_resolution.width;
    fb.height = g_resolution.height;
    viewport.width = (float)fb.width;
    viewport.height = (float)fb.height;

    count = ui_view_build(&g_model, viewport, commands,
                          sizeof(commands) / sizeof(commands[0]));
    if (count > sizeof(commands) / sizeof(commands[0])) {
        log_runtime("ui_view_build command overflow", (int)count);
        return -1;
    }

    backend.clear = backend_clear;
    backend.rectangle = backend_rectangle;
    backend.text = backend_text;
    backend.line = backend_line;
    backend.context = &fb;
    result = (int)ui_renderer_submit(&backend, commands, count);
    if (result != (int)UI_RENDER_OK) {
        log_runtime("ui_renderer_submit", result);
        return -1;
    }

    return present_frame();
}

'''
source = source[:start] + new_render + source[end:]

start = source.index('s32 main(s32 argc, const char *argv[])')
new_main = '''s32 main(s32 argc, const char *argv[])
{
    padInfo pad_info;
    padData pad_data;
    int pad_initialized = 0;
    (void)argc;
    (void)argv;

    log_runtime("application entry", 0);
    ui_model_init(&g_model);
    (void)strncpy(g_model.server_label, "Motor VPN nao integrado",
                  sizeof(g_model.server_label) - 1u);
    ui_runtime_init(&g_runtime);

    if (init_video() != 0) {
        log_runtime("application stopped: video initialization failed", -1);
        if (g_rsx_initialized) rsxFinish(g_context, 1);
        if (g_host_addr != NULL) free(g_host_addr);
        return -1;
    }

    if (ioPadInit(7) == 0) pad_initialized = 1;
    if (sysUtilRegisterCallback(SYSUTIL_EVENT_SLOT0, event_callback, NULL) != 0) {
        log_runtime("sysUtilRegisterCallback", -1);
        if (pad_initialized) ioPadEnd();
        if (g_rsx_initialized) rsxFinish(g_context, 1);
        if (g_host_addr != NULL) free(g_host_addr);
        return -1;
    }

    log_runtime("main loop started", 0);
    while (g_running) {
        u32 keys = 0;
        int i;
        if (pad_initialized && ioPadGetInfo(&pad_info) == 0) {
            for (i = 0; i < MAX_PORT_NUM; ++i) {
                if (pad_info.status[i]) {
                    if (ioPadGetData(i, &pad_data) == 0) {
                        keys = ps3_pad_to_ui_keys(&pad_data);
                        break;
                    }
                }
            }
        }
        (void)ui_runtime_update(&g_runtime, &g_model, keys);
        if (render_frame() != 0) {
            log_runtime("main loop stopped: render failure", -1);
            break;
        }
        sysUtilCheckCallback();
        usleep(10000);
    }

    if (pad_initialized) ioPadEnd();
    sysUtilUnregisterCallback(SYSUTIL_EVENT_SLOT0);
    if (g_rsx_initialized) rsxFinish(g_context, 1);
    if (g_host_addr != NULL) free(g_host_addr);
    log_runtime("application exit", 0);
    return 0;
}
'''
source = source[:start] + new_main
source = source.replace('APP 2.2', 'APP 2.4')

path.write_text(source)
print(f'Patched {path}')

project_root = path.parents[2]
rsx_stub = project_root / 'tests/ps3_stubs/rsx/rsx.h'
if rsx_stub.exists():
    rsx_stub.write_text('''#ifndef TEST_RSX_H
#define TEST_RSX_H
#include <ppu-types.h>
typedef struct gcmContextData { int unused; } gcmContextData;
typedef struct {
    u8 type, antiAlias, colorFormat, colorTarget;
    u8 colorLocation[4];
    u32 colorOffset[4];
    u32 colorPitch[4];
    u8 depthFormat, depthLocation;
    u8 _pad[2];
    u32 depthOffset, depthPitch;
    u16 width, height, x, y;
} gcmSurface;
#define GCM_FLIP_VSYNC 2
#define GCM_SURFACE_X8R8G8B8 0
#define GCM_SURFACE_TARGET_0 1
#define GCM_LOCATION_RSX 0
#define GCM_SURFACE_ZETA_Z24S8 0
#define GCM_SURFACE_TYPE_LINEAR 0
#define GCM_SURFACE_CENTER_1 0
s32 rsxInit(gcmContextData **context, u32 cmd_size, u32 io_size, const void *io_address);
void *rsxMemalign(u32 alignment, u32 size);
s32 rsxAddressToOffset(const void *ptr, u32 *offset);
void rsxSetSurface(gcmContextData *context, const gcmSurface *surface);
void rsxFinish(gcmContextData *context, u32 wait);
void gcmSetFlipMode(u32 mode);
s32 gcmSetDisplayBuffer(u32 id, u32 offset, u32 pitch, u32 width, u32 height);
void gcmResetFlipStatus(void);
s32 gcmGetFlipStatus(void);
s32 gcmSetFlip(gcmContextData *context, u32 buffer);
void rsxFlushBuffer(gcmContextData *context);
void gcmSetWaitFlip(gcmContextData *context);
#endif
''')
video_stub = project_root / 'tests/ps3_stubs/sysutil/video.h'
if video_stub.exists():
    video_content = video_stub.read_text()
    if '#define VIDEO_ASPECT_AUTO' not in video_content:
        video_content = video_content.replace('#define VIDEO_BUFFER_FORMAT_XRGB 0\n', '#define VIDEO_BUFFER_FORMAT_XRGB 0\n#define VIDEO_ASPECT_AUTO 0\n')
        video_stub.write_text(video_content)
