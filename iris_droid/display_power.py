"""Switch the direct display's KMS connector between on and standby."""

from __future__ import annotations

import ctypes as c
import os
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

SDL_SYSWM_KMSDRM = 13
DRM_MODE_OBJECT_CONNECTOR = 0xC0C0C0C0
DRM_MODE_DPMS_ON = 0
DRM_MODE_DPMS_OFF = 3


class _SdlKmsInfo(c.Structure):
    _fields_ = [("card_index", c.c_int), ("drm_fd", c.c_int), ("gbm_device", c.c_void_p)]


class _SdlWindowData(c.Union):
    _fields_ = [("kms", _SdlKmsInfo), ("padding", c.c_uint8 * 64)]


class _SdlWindowInfo(c.Structure):
    _fields_ = [
        ("version", c.c_uint8 * 3),
        ("subsystem", c.c_int),
        ("data", _SdlWindowData),
    ]


# Only the fields we read from the libdrm structures are needed.
class _DrmProperties(c.Structure):
    _fields_ = [("count", c.c_uint32), ("ids", c.POINTER(c.c_uint32))]


class _DrmProperty(c.Structure):
    _fields_ = [("id", c.c_uint32), ("flags", c.c_uint32), ("name", c.c_char * 32)]


def _dpms_property(drm: c.CDLL, fd: int, connector_id: int) -> int:
    drm.drmModeObjectGetProperties.argtypes = [c.c_int, c.c_uint32, c.c_uint32]
    drm.drmModeObjectGetProperties.restype = c.POINTER(_DrmProperties)
    drm.drmModeFreeObjectProperties.argtypes = [c.POINTER(_DrmProperties)]
    drm.drmModeFreeObjectProperties.restype = None
    drm.drmModeGetProperty.argtypes = [c.c_int, c.c_uint32]
    drm.drmModeGetProperty.restype = c.POINTER(_DrmProperty)
    drm.drmModeFreeProperty.argtypes = [c.POINTER(_DrmProperty)]
    drm.drmModeFreeProperty.restype = None

    properties = drm.drmModeObjectGetProperties(fd, connector_id, DRM_MODE_OBJECT_CONNECTOR)
    if not properties:
        raise OSError("could not read the KMS connector's properties")
    try:
        for prop_id in properties.contents.ids[: properties.contents.count]:
            prop = drm.drmModeGetProperty(fd, prop_id)
            if prop:
                try:
                    if prop.contents.name == b"DPMS":
                        return int(prop_id)
                finally:
                    drm.drmModeFreeProperty(prop)
    finally:
        drm.drmModeFreeObjectProperties(properties)
    raise OSError("the KMS connector has no DPMS property")


def _kms_power(pygame: ModuleType) -> Callable[[bool], None]:
    # Resolve SDL symbols through Pygame so we use the same SDL instance and
    # borrow its DRM master descriptor. A fresh open cannot control the output.
    sdl = c.CDLL(pygame.base.__file__)
    sdl.SDL_GetKeyboardFocus.argtypes = []
    sdl.SDL_GetKeyboardFocus.restype = c.c_void_p
    sdl.SDL_GetWindowWMInfo.argtypes = [c.c_void_p, c.POINTER(_SdlWindowInfo)]
    sdl.SDL_GetWindowWMInfo.restype = c.c_int
    # KMSDRM gives the window keyboard focus when it creates it, even on a Pi
    # with no keyboard attached. This process creates only one window.
    window = sdl.SDL_GetKeyboardFocus()
    info = _SdlWindowInfo()
    info.version[:] = pygame.get_sdl_version()
    if (
        not window
        or not sdl.SDL_GetWindowWMInfo(window, c.byref(info))
        or info.subsystem != SDL_SYSWM_KMSDRM
        or info.data.kms.drm_fd < 0
    ):
        raise OSError("SDL did not provide a KMS display descriptor")

    fd = info.data.kms.drm_fd
    card = f"card{info.data.kms.card_index}"
    connectors = [
        path
        for path in Path("/sys/class/drm").glob(f"{card}-*")
        if (path / "enabled").read_text(encoding="ascii").strip() == "enabled"
    ]
    if len(connectors) != 1:
        raise OSError(f"expected one active connector on {card}, found {len(connectors)}")
    connector_id = int((connectors[0] / "connector_id").read_text(encoding="ascii"))
    drm = c.CDLL("libdrm.so.2", use_errno=True)
    dpms_property = _dpms_property(drm, fd, connector_id)
    set_property = drm.drmModeConnectorSetProperty
    set_property.argtypes = [c.c_int, c.c_uint32, c.c_uint32, c.c_uint64]
    set_property.restype = c.c_int

    def set_power(on: bool) -> None:
        mode = DRM_MODE_DPMS_ON if on else DRM_MODE_DPMS_OFF
        c.set_errno(0)
        result = set_property(fd, connector_id, dpms_property, mode)
        if result != 0:
            action = "wake" if on else "sleep"
            reason = os.strerror(c.get_errno() or abs(result))
            raise OSError(f"could not {action} {connectors[0].name}: {reason}")

    return set_power


class IdleDisplayPower:
    def __init__(self, pygame: ModuleType) -> None:
        self._pygame = pygame
        self._set_power: Callable[[bool], None] | None = None
        self.asleep = False
        self._unavailable = False

    def sleep(self) -> None:
        if self._unavailable or self.asleep:
            return
        try:
            if self._set_power is None:
                self._set_power = _kms_power(self._pygame)
            self._set_power(False)
        except (OSError, AttributeError, ValueError) as error:
            self._unavailable = True
            print(
                f"Iris display: idle power off unavailable ({error}); using black.", file=sys.stderr
            )
        else:
            self.asleep = True

    def wake(self) -> None:
        if self.asleep and self._set_power is not None:
            self._set_power(True)
            self.asleep = False
