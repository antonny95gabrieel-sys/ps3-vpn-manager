#!/usr/bin/env python3
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text()

if "videoGetNumberOfDevice(VIDEO_PRIMARY)" in source:
    print("Correção de diagnóstico de vídeo já aplicada.")
    raise SystemExit(0)

if '#include <stdlib.h>' not in source:
    source = source.replace('#include <malloc.h>\n', '#include <malloc.h>\n#include <stdlib.h>\n', 1)

anchor = 'static ui_runtime_t g_runtime;\n'
if anchor not in source:
    raise SystemExit('Não foi possível localizar as variáveis globais esperadas.')

globals_and_helpers = r'''
static void *g_host_addr;
static int g_rsx_initialized;
static u32 *g_depth_buffer;
static u32 g_depth_offset;
static u32 g_depth_pitch;
static u32 g_color_pitch;
static u8 *g_software_framebuffer;
static u32 g_color_offsets[2];
static int g_first_flip = 1;
static int g_render_diagnostic_done;

static void log_runtime(const char *stage, int result)
{
    FILE *log_file = fopen("/dev_hdd0/tmp/ps3vpnmanager.log", "a");
    if (log_file != NULL) {
        fprintf(log_file, "%s: %d\n", stage, result);
        fclose(log_file);
    }
}

'''
source = source.replace(anchor, anchor + globals_and_helpers, 1)

start = source.index("static int init_video(void)")
end = source.index("static int render_frame(void)", start)
new_init = r'''static int init_video(void)
{
    videoState state;
    videoConfiguration active_config;
    videoConfiguration config;
    videoDeviceInfo device_info;
    videoResolution detected_resolution;
    u32 buffer_size;
    u32 device_count;
    u32 i;
    int state_rc;
    int config_rc;
    int resolution_rc;
    int hdmi_enabled = 0;
    int selected_resolution = VIDEO_RESOLUTION_UNDEFINED;

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

    memset(&state, 0, sizeof(state));
    state_rc = videoGetState(VIDEO_PRIMARY, 0, &state);
    log_runtime("videoGetState return", state_rc);
    log_runtime("videoGetState state", state.state);
    log_runtime("videoGetState colorSpace", state.colorSpace);
    log_runtime("videoGetState resolution", state.displayMode.resolution);
    log_runtime("videoGetState scanMode", state.displayMode.scanMode);
    log_runtime("videoGetState aspect", state.displayMode.aspect);
    log_runtime("videoGetState refreshRates", state.displayMode.refreshRates);

    device_count = videoGetNumberOfDevice(VIDEO_PRIMARY);
    log_runtime("videoGetNumberOfDevice", (int)device_count);
    for (i = 0; i < device_count && i < 8; ++i) {
        memset(&device_info, 0, sizeof(device_info));
        if (videoGetDeviceInfo(VIDEO_PRIMARY, i, &device_info) == 0) {
            log_runtime("video device portType", device_info.portType);
            log_runtime("video device state", device_info.state);
            log_runtime("video device colorSpace", device_info.colorSpace);
            log_runtime("video device availableModeCount", device_info.availableModeCount);
            if (device_info.portType == VIDEO_PORT_HDMI &&
                device_info.state == VIDEO_STATE_ENABLED) {
                hdmi_enabled = 1;
                if (device_info.availableModeCount > 0)
                    selected_resolution = device_info.availableModes[0].resolution;
            }
        } else {
            log_runtime("videoGetDeviceInfo failed at index", (int)i);
        }
    }

    memset(&active_config, 0, sizeof(active_config));
    config_rc = videoGetConfiguration(VIDEO_PRIMARY, &active_config, NULL);
    log_runtime("videoGetConfiguration return", config_rc);
    if (config_rc == 0) {
        log_runtime("active video resolution", active_config.resolution);
        log_runtime("active video format", active_config.format);
        log_runtime("active video aspect", active_config.aspect);
        log_runtime("active video pitch", (int)active_config.pitch);
        if (active_config.resolution != VIDEO_RESOLUTION_UNDEFINED)
            selected_resolution = active_config.resolution;
    }

    if (state_rc == 0 && state.state == VIDEO_STATE_ENABLED &&
        state.displayMode.resolution != VIDEO_RESOLUTION_UNDEFINED)
        selected_resolution = state.displayMode.resolution;

    if (selected_resolution == VIDEO_RESOLUTION_UNDEFINED) {
        log_runtime("no valid active video resolution", -1);
        log_runtime("HDMI enabled device", hdmi_enabled);
        return -1;
    }

    resolution_rc = videoGetResolution(selected_resolution, &detected_resolution);
    log_runtime("videoGetResolution return", resolution_rc);
    if (resolution_rc != 0 || detected_resolution.width == 0 ||
        detected_resolution.height == 0) {
        log_runtime("invalid video dimensions", selected_resolution);
        return -1;
    }
    g_resolution = detected_resolution;
    log_runtime("detected video width", g_resolution.width);
    log_runtime("detected video height", g_resolution.height);

    /* Preserva a configuração ativa; só configura se a API não a forneceu. */
    if (config_rc != 0) {
        if (state_rc != 0 || state.state != VIDEO_STATE_ENABLED) {
            log_runtime("video configuration unavailable", state.state);
            return -1;
        }
        memset(&config, 0, sizeof(config));
        config.resolution = selected_resolution;
        config.format = VIDEO_BUFFER_FORMAT_XRGB;
        config.aspect = VIDEO_ASPECT_AUTO;
        config.pitch = (u32)g_resolution.width * 4;
        if (videoConfigure(VIDEO_PRIMARY, &config, NULL, 0) != 0) {
            log_runtime("videoConfigure", -1);
            return -1;
        }
    } else if (active_config.pitch == 0) {
        log_runtime("active video pitch invalid", 0);
        return -1;
    }

    gcmSetFlipMode(GCM_FLIP_VSYNC);
    g_color_pitch = config_rc == 0 ? active_config.pitch : config.pitch;
    if (g_color_pitch < (u32)g_resolution.width * 4 ||
        (g_color_pitch & 63u) != 0) {
        log_runtime("invalid active framebuffer pitch", (int)g_color_pitch);
        return -1;
    }
    g_depth_pitch = g_color_pitch;
    buffer_size = g_color_pitch * (u32)g_resolution.height;
    g_software_framebuffer = (u8 *)malloc((size_t)g_resolution.width *
                                          (size_t)g_resolution.height * 4u);
    if (g_software_framebuffer == NULL) {
        log_runtime("software framebuffer allocation", -1);
        return -1;
    }
    log_runtime("RSX framebuffer pitch", (int)g_color_pitch);

    for (i = 0; i < 2; ++i) {
        g_buffers[i] = (u32 *)rsxMemalign(64, buffer_size);
        if (g_buffers[i] == NULL) {
            log_runtime("rsxMemalign color buffer", (int)i);
            return -1;
        }
        if (rsxAddressToOffset(g_buffers[i], &g_color_offsets[i]) != 0) {
            log_runtime("rsxAddressToOffset color buffer", (int)i);
            return -1;
        }
        if (gcmSetDisplayBuffer(i, g_color_offsets[i], g_color_pitch,
                                g_resolution.width, g_resolution.height) != 0) {
            log_runtime("gcmSetDisplayBuffer", (int)i);
            return -1;
        }
    }

    gcmResetFlipStatus();
    g_current_buffer = 0;
    g_first_flip = 1;
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
if old_flip in source:
    source = source.replace(old_flip, '', 1)

start = source.index("static int render_frame(void)")
end = source.index("static void event_callback", start)
new_render = r'''static int present_frame(void)
{
    int status = 0;
    int polls = 0;

    if (!g_render_diagnostic_done)
        log_runtime("present frame entered", g_current_buffer);

    if (!g_first_flip) {
        do {
            status = gcmGetFlipStatus();
            if (status != 0)
                usleep(200);
            ++polls;
            if (polls > 5000) {
                log_runtime("flip wait timeout status", status);
                return -1;
            }
        } while (status != 0);
    }

    gcmResetFlipStatus();
    if (!g_render_diagnostic_done)
        log_runtime("flip status ready polls", polls);

    if (gcmSetFlip(g_context, g_current_buffer) != 0) {
        log_runtime("gcmSetFlip failed buffer", g_current_buffer);
        return -1;
    }
    rsxFlushBuffer(g_context);
    if (!g_render_diagnostic_done)
        log_runtime("RSX flush complete", 0);

    gcmSetWaitFlip(g_context);
    if (!g_render_diagnostic_done)
        log_runtime("wait flip command queued", 0);

    g_current_buffer ^= 1;
    g_first_flip = 0;
    g_render_diagnostic_done = 1;
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

    fb.pixels = (u32 *)g_software_framebuffer;
    fb.width = g_resolution.width;
    fb.height = g_resolution.height;
    viewport.width = (float)fb.width;
    viewport.height = (float)fb.height;

    if (!g_render_diagnostic_done)
        log_runtime("render frame entered", 0);
    count = ui_view_build(&g_model, viewport, commands,
                          sizeof(commands) / sizeof(commands[0]));
    if (!g_render_diagnostic_done)
        log_runtime("ui view built commands", (int)count);
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
    if (!g_render_diagnostic_done)
        log_runtime("ui renderer submitted", result);
    if (result != (int)UI_RENDER_OK) {
        log_runtime("ui_renderer_submit", result);
        return -1;
    }
    {
        u32 y;
        const u32 row_bytes = (u32)g_resolution.width * 4u;
        for (y = 0; y < (u32)g_resolution.height; ++y) {
            u8 *destination = (u8 *)g_buffers[g_current_buffer] +
                              (size_t)y * g_color_pitch;
            const u8 *source = g_software_framebuffer +
                               (size_t)y * row_bytes;
            memcpy(destination, source, row_bytes);
            if (g_color_pitch > row_bytes)
                memset(destination + row_bytes, 0, g_color_pitch - row_bytes);
        }
    }
    if (!g_render_diagnostic_done)
        log_runtime("framebuffer copied to RSX", (int)g_color_pitch);
    return present_frame();
}

'''
source = source[:start] + new_render + source[end:]

start = source.index("s32 main(s32 argc, const char *argv[])")
new_main = r'''s32 main(s32 argc, const char *argv[])
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
        if (g_software_framebuffer != NULL) free(g_software_framebuffer);
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
                if (pad_info.status[i] && ioPadGetData(i, &pad_data) == 0) {
                    keys = ps3_pad_to_ui_keys(&pad_data);
                    break;
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
    if (g_software_framebuffer != NULL) free(g_software_framebuffer);
    if (g_host_addr != NULL) free(g_host_addr);
    log_runtime("application exit", 0);
    return 0;
}
'''
source = source[:start] + new_main
path.write_text(source)

project_root = path.parents[2]
rsx_stub = project_root / 'tests/ps3_stubs/rsx/rsx.h'
if rsx_stub.exists():
    rsx_stub.write_text(r'''#ifndef TEST_RSX_H
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
    if 'videoGetDeviceInfo' not in video_content:
        video_content += r'''
#ifndef VIDEO_PORT_HDMI
#define VIDEO_PORT_HDMI 0x01
#endif
#ifndef VIDEO_RESOLUTION_UNDEFINED
#define VIDEO_RESOLUTION_UNDEFINED 0
#endif
#ifndef VIDEO_STATE_ENABLED
#define VIDEO_STATE_ENABLED 1
#endif
#ifndef VIDEO_BUFFER_FORMAT_XRGB
#define VIDEO_BUFFER_FORMAT_XRGB 0
#endif
#ifndef VIDEO_ASPECT_AUTO
#define VIDEO_ASPECT_AUTO 0
#endif
typedef struct { u16 redX, redY, greenX, greenY, blueX, blueY, whiteX, whiteY; u32 gamma; } videoColorInfo;
typedef struct { u8 ksv[32*5]; u8 padding[4]; u32 count; } videoKSVList;
typedef struct {
    u8 portType, colorSpace;
    u16 latency;
    u8 availableModeCount, state, rgbOutputRange, padding[5];
    videoColorInfo colorInfo;
    videoDisplayMode availableModes[32];
    videoKSVList ksvList;
} videoDeviceInfo;
u32 videoGetNumberOfDevice(u32 videoOut);
s32 videoGetDeviceInfo(u32 videoOut, u32 deviceIndex, videoDeviceInfo *info);
s32 videoGetConfiguration(u32 videoOut, videoConfiguration *config, void *option);
'''
    video_stub.write_text(video_content)
print(f'Patched {path}')
